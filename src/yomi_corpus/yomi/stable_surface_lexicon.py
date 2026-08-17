from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Iterable, Sequence
import unicodedata

from yomi_corpus.yomi.corpus_frequency import (
    SourceToken,
    combined_source_digest,
    hiragana_to_katakana,
    sha256_file,
)


SCRIPT_VERSION = "stable_surface_reading_lexicon_v2"
DEFAULT_MAX_SPAN_TOKENS = 4
DEFAULT_MAX_SURFACE_CHARS = 16
DEFAULT_MIN_COUNT = 5
DEFAULT_MIN_SHARE = 0.95
DEFAULT_SHARD_COUNT = 64
MODEL_STABLE_SURFACE_LEXICON_FILENAME = "stable_surface_readings.tsv"
READING_RE = re.compile(r"^[ぁ-ゖァ-ヺーゝゞヽヾ]+$")
TARGET_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002ffff々〆〻A-Za-zＡ-Ｚａ-ｚ]"
)


@dataclass(frozen=True)
class StableSurfaceLexiconBuildSummary:
    source_corpus: str
    output_tsv: str
    manifest_json: str
    source_corpus_version: str
    source_token_count: int
    malformed_line_count: int
    enumerated_span_count: int
    candidate_span_count: int
    observed_surface_count: int
    stable_surface_count: int
    ambiguous_surface_count: int
    insufficient_surface_count: int
    min_count: int
    min_share: float
    max_span_tokens: int
    max_surface_chars: int
    checksum_sha256: str | None
    rule: str = SCRIPT_VERSION


@dataclass(frozen=True)
class StableSurfaceRow:
    surface: str
    reading: str
    count: int
    surface_total_count: int
    min_span_tokens: int
    max_span_tokens: int
    segmentation_counts: tuple[tuple[tuple[str, ...], int], ...]


@dataclass(frozen=True)
class StableSurfaceReading:
    surface: str
    reading: str
    count: int
    surface_total_count: int
    share: float
    source_corpus_version: str


@dataclass(frozen=True)
class StableSurfaceJudgment:
    value: bool
    reason: str
    evidence: StableSurfaceReading | None = None


class StableSurfaceReadingLexicon:
    def __init__(
        self,
        *,
        rows_by_surface: dict[str, StableSurfaceReading],
        artifact_path: str | None = None,
    ) -> None:
        self.rows_by_surface = rows_by_surface
        self.artifact_path = artifact_path

    @classmethod
    def load_tsv(cls, path: str | Path) -> StableSurfaceReadingLexicon:
        artifact_path = Path(path)
        rows: dict[str, StableSurfaceReading] = {}
        with artifact_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            required = {
                "surface",
                "reading",
                "count",
                "surface_total_count",
                "share",
                "source_corpus_version",
            }
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"Stable-surface TSV missing fields: {sorted(missing)}")
            for row in reader:
                entry = StableSurfaceReading(
                    surface=row["surface"],
                    reading=row["reading"],
                    count=int(row["count"]),
                    surface_total_count=int(row["surface_total_count"]),
                    share=float(row["share"]),
                    source_corpus_version=row["source_corpus_version"],
                )
                rows[entry.surface] = entry
        return cls(rows_by_surface=rows, artifact_path=str(artifact_path))

    @property
    def source_corpus_version(self) -> str | None:
        versions = {row.source_corpus_version for row in self.rows_by_surface.values()}
        return next(iter(versions)) if len(versions) == 1 else None

    def judge(self, surface: str, reading: str) -> StableSurfaceJudgment:
        evidence = self.rows_by_surface.get(surface)
        if evidence is None:
            return StableSurfaceJudgment(False, "missing_stable_surface")
        if evidence.reading != reading:
            return StableSurfaceJudgment(
                False,
                f"stable_surface_reading_mismatch:{evidence.reading}",
                evidence,
            )
        return StableSurfaceJudgment(
            True,
            "stable_surface_dominant_corpus_reading",
            evidence,
        )


def resolve_stable_surface_lexicon_artifact(
    decoder_model_dir: str | Path | None,
) -> Path | None:
    if not decoder_model_dir:
        return None
    artifact = Path(decoder_model_dir) / MODEL_STABLE_SURFACE_LEXICON_FILENAME
    return artifact if artifact.exists() else None


def build_stable_surface_lexicon(
    *,
    source_corpus: str | Path,
    output_tsv: str | Path,
    manifest_json: str | Path,
    source_corpus_version: str,
    additional_source_corpora: Sequence[str | Path] = (),
    min_count: int = DEFAULT_MIN_COUNT,
    min_share: float = DEFAULT_MIN_SHARE,
    max_span_tokens: int = DEFAULT_MAX_SPAN_TOKENS,
    max_surface_chars: int = DEFAULT_MAX_SURFACE_CHARS,
    shard_count: int = DEFAULT_SHARD_COUNT,
    checksum: bool = True,
) -> StableSurfaceLexiconBuildSummary:
    if min_count < 1:
        raise ValueError("min_count must be at least 1")
    if not 0 < min_share <= 1:
        raise ValueError("min_share must be greater than 0 and at most 1")
    if max_span_tokens < 1:
        raise ValueError("max_span_tokens must be at least 1")
    if max_surface_chars < 1:
        raise ValueError("max_surface_chars must be at least 1")
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")

    source_path = Path(source_corpus)
    source_paths = [source_path, *(Path(path) for path in additional_source_corpora)]
    output_path = Path(output_tsv)
    manifest_path = Path(manifest_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    source_token_count = 0
    malformed_line_count = 0
    enumerated_span_count = 0
    candidate_span_count = 0
    stable_rows: list[StableSurfaceRow] = []
    observed_surface_count = 0
    ambiguous_surface_count = 0
    insufficient_surface_count = 0

    with TemporaryDirectory(prefix="yomi-stable-surface-") as temporary:
        shard_paths = [Path(temporary) / f"shard-{index:04d}.tsv" for index in range(shard_count)]
        handles = [path.open("w", encoding="utf-8", newline="") for path in shard_paths]
        writers = [csv.writer(handle, delimiter="\t", lineterminator="\n") for handle in handles]
        try:
            for corpus_path in source_paths:
                for sentence, malformed in iter_source_corpus_segments(corpus_path):
                    malformed_line_count += malformed
                    source_token_count += len(sentence)
                    for surface, reading, segmentation in enumerate_candidate_spans(
                        sentence,
                        max_span_tokens=max_span_tokens,
                        max_surface_chars=max_surface_chars,
                    ):
                        enumerated_span_count += 1
                        if not is_candidate_surface(surface):
                            continue
                        candidate_span_count += 1
                        shard_index = stable_shard(surface, shard_count)
                        writers[shard_index].writerow(
                            [surface, reading, json.dumps(segmentation, ensure_ascii=False)]
                        )
        finally:
            for handle in handles:
                handle.close()

        for shard_path in shard_paths:
            counts: dict[str, Counter[str]] = defaultdict(Counter)
            segmentations: dict[tuple[str, str], Counter[tuple[str, ...]]] = defaultdict(Counter)
            with shard_path.open(encoding="utf-8", newline="") as handle:
                for surface, reading, segmentation_json in csv.reader(handle, delimiter="\t"):
                    segmentation = tuple(json.loads(segmentation_json))
                    counts[surface][reading] += 1
                    segmentations[(surface, reading)][segmentation] += 1
            observed_surface_count += len(counts)
            for surface, reading_counts in counts.items():
                total = sum(reading_counts.values())
                reading, count = reading_counts.most_common(1)[0]
                if count / total < min_share:
                    ambiguous_surface_count += 1
                    continue
                if count < min_count:
                    insufficient_surface_count += 1
                    continue
                segmentation_counts = tuple(
                    sorted(
                        segmentations[(surface, reading)].items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                )
                token_lengths = [len(segmentation) for segmentation, _ in segmentation_counts]
                stable_rows.append(
                    StableSurfaceRow(
                        surface=surface,
                        reading=reading,
                        count=count,
                        surface_total_count=total,
                        min_span_tokens=min(token_lengths),
                        max_span_tokens=max(token_lengths),
                        segmentation_counts=segmentation_counts,
                    )
                )

    stable_rows.sort(key=lambda row: row.surface)
    write_stable_surface_tsv(output_path, stable_rows, source_corpus_version)
    source_digests = [sha256_file(path) for path in source_paths] if checksum else []
    digest = combined_source_digest(source_paths, source_digests) if checksum else None
    summary = StableSurfaceLexiconBuildSummary(
        source_corpus=str(source_path),
        output_tsv=str(output_path),
        manifest_json=str(manifest_path),
        source_corpus_version=source_corpus_version,
        source_token_count=source_token_count,
        malformed_line_count=malformed_line_count,
        enumerated_span_count=enumerated_span_count,
        candidate_span_count=candidate_span_count,
        observed_surface_count=observed_surface_count,
        stable_surface_count=len(stable_rows),
        ambiguous_surface_count=ambiguous_surface_count,
        insufficient_surface_count=insufficient_surface_count,
        min_count=min_count,
        min_share=min_share,
        max_span_tokens=max_span_tokens,
        max_surface_chars=max_surface_chars,
        checksum_sha256=digest,
    )
    manifest = {
        "script_version": SCRIPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_corpus_path": str(source_path),
        "source_corpus_paths": [str(path) for path in source_paths],
        "source_corpus_version": source_corpus_version,
        "source_corpora_sha256": digest,
        "source_corpora": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": source_digests[index] if checksum else None,
            }
            for index, path in enumerate(source_paths)
        ],
        "parameters": {
            "min_count": min_count,
            "min_share": min_share,
            "max_span_tokens": max_span_tokens,
            "max_surface_chars": max_surface_chars,
            "shard_count": shard_count,
            "require_unique_reading": min_share == 1,
            "normalize_reading_to_katakana": True,
        },
        "filters": {
            "target_surface_regex": TARGET_RE.pattern,
            "reading_regex": READING_RE.pattern,
            "whitespace_breaks_span": True,
            "empty_or_non_kana_reading_breaks_span": True,
            "eos_breaks_span": True,
        },
        "output_tsv": str(output_path),
        "summary": asdict(summary),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def iter_source_corpus_segments(path: str | Path) -> Iterable[tuple[list[SourceToken], int]]:
    segment: list[SourceToken] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line or line == "EOS":
                if segment:
                    yield segment, 0
                segment = []
                continue
            columns = line.split("\t")
            if len(columns) < 5:
                if segment:
                    yield segment, 0
                segment = []
                yield [], 1
                continue
            surface = columns[0]
            reading = hiragana_to_katakana(columns[4])
            if (
                not surface
                or is_span_separator_surface(surface)
                or not READING_RE.fullmatch(reading)
            ):
                if segment:
                    yield segment, 0
                segment = []
                continue
            segment.append(SourceToken(surface=surface, reading=reading))
    if segment:
        yield segment, 0


def is_span_separator_surface(surface: str) -> bool:
    return surface.isspace() or all(
        unicodedata.category(character)[0] in {"P", "S", "Z"}
        for character in surface
    )


def enumerate_candidate_spans(
    sentence: Sequence[SourceToken],
    *,
    max_span_tokens: int,
    max_surface_chars: int,
) -> Iterable[tuple[str, str, tuple[str, ...]]]:
    for start in range(len(sentence)):
        surface = ""
        reading = ""
        segmentation: list[str] = []
        for token in sentence[start : start + max_span_tokens]:
            surface += token.surface
            if len(surface) > max_surface_chars:
                break
            reading += token.reading
            segmentation.append(token.surface)
            yield surface, reading, tuple(segmentation)


def is_candidate_surface(surface: str) -> bool:
    return bool(TARGET_RE.search(surface))


def stable_shard(surface: str, shard_count: int) -> int:
    digest = hashlib.blake2b(surface.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % shard_count


def write_stable_surface_tsv(
    path: Path,
    rows: Sequence[StableSurfaceRow],
    source_corpus_version: str,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "surface",
                "reading",
                "count",
                "surface_total_count",
                "share",
                "min_span_tokens",
                "max_span_tokens",
                "segmentation_counts_json",
                "source_corpus_version",
            ]
        )
        for row in rows:
            segmentation_payload = [
                {"surfaces": list(segmentation), "count": count}
                for segmentation, count in row.segmentation_counts
            ]
            writer.writerow(
                [
                    row.surface,
                    row.reading,
                    row.count,
                    row.surface_total_count,
                    f"{row.count / row.surface_total_count:.12g}",
                    row.min_span_tokens,
                    row.max_span_tokens,
                    json.dumps(segmentation_payload, ensure_ascii=False, separators=(",", ":")),
                    source_corpus_version,
                ]
            )
