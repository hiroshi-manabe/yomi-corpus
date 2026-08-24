from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from yomi_corpus.yomi.corpus_frequency import combined_source_digest, sha256_file
from yomi_corpus.yomi.stable_surface_lexicon import (
    is_candidate_surface,
    iter_source_corpus_segments,
)


SCRIPT_VERSION = "ngram_reading_transition_stats_v1"
MODEL_NGRAM_READING_TRANSITIONS_FILENAME = "ngram_reading_transitions.tsv"
MODEL_NGRAM_READING_TRANSITIONS_MANIFEST_FILENAME = (
    "ngram_reading_transitions.manifest.json"
)
DEFAULT_MIN_SURFACE_COUNT = 5
DEFAULT_MIN_READING_COUNT = 5
DEFAULT_MIN_READING_SHARE = 0.95
DEFAULT_SHARD_COUNT = 64


@dataclass(frozen=True)
class NgramReadingTransitionBuildSummary:
    source_corpus: str
    output_tsv: str
    manifest_json: str
    source_corpus_version: str
    source_token_count: int
    observed_transition_count: int
    retained_surface_transition_count: int
    retained_reading_transition_count: int
    min_surface_count: int
    checksum_sha256: str | None
    rule: str = SCRIPT_VERSION


@dataclass(frozen=True)
class NgramReadingTransition:
    left_surface: str
    right_surface: str
    left_reading: str
    right_reading: str
    count: int
    surface_total_count: int
    share: float
    rank: int
    source_corpus_version: str


@dataclass(frozen=True)
class NgramReadingTransitionJudgment:
    value: bool
    reason: str
    candidate: NgramReadingTransition | None = None
    dominant: NgramReadingTransition | None = None


class NgramReadingTransitionStats:
    def __init__(
        self,
        *,
        rows_by_surfaces: dict[tuple[str, str], list[NgramReadingTransition]],
        artifact_path: str | None = None,
    ) -> None:
        self.rows_by_surfaces = rows_by_surfaces
        self.artifact_path = artifact_path

    @classmethod
    def load_tsv(cls, path: str | Path) -> NgramReadingTransitionStats:
        artifact_path = Path(path)
        rows: dict[tuple[str, str], list[NgramReadingTransition]] = defaultdict(list)
        with artifact_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            required = {
                "left_surface",
                "right_surface",
                "left_reading",
                "right_reading",
                "count",
                "surface_total_count",
                "share",
                "rank",
                "source_corpus_version",
            }
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(
                    f"N-gram transition TSV missing fields: {sorted(missing)}"
                )
            for row in reader:
                transition = NgramReadingTransition(
                    left_surface=row["left_surface"],
                    right_surface=row["right_surface"],
                    left_reading=row["left_reading"],
                    right_reading=row["right_reading"],
                    count=int(row["count"]),
                    surface_total_count=int(row["surface_total_count"]),
                    share=float(row["share"]),
                    rank=int(row["rank"]),
                    source_corpus_version=row["source_corpus_version"],
                )
                rows[(transition.left_surface, transition.right_surface)].append(
                    transition
                )
        for variants in rows.values():
            variants.sort(
                key=lambda row: (
                    row.rank,
                    -row.count,
                    row.left_reading,
                    row.right_reading,
                )
            )
        return cls(rows_by_surfaces=dict(rows), artifact_path=str(artifact_path))

    def judge(
        self,
        left_surface: str,
        left_reading: str,
        right_surface: str,
        right_reading: str,
        *,
        min_count: int = DEFAULT_MIN_READING_COUNT,
        min_share: float = DEFAULT_MIN_READING_SHARE,
    ) -> NgramReadingTransitionJudgment:
        variants = self.rows_by_surfaces.get((left_surface, right_surface), [])
        if not variants:
            return NgramReadingTransitionJudgment(
                False,
                "missing_surface_transition",
            )
        dominant = variants[0]
        candidate = next(
            (
                row
                for row in variants
                if row.left_reading == left_reading
                and row.right_reading == right_reading
            ),
            None,
        )
        if candidate is None:
            return NgramReadingTransitionJudgment(
                False,
                "unseen_reading_transition",
                dominant=dominant,
            )
        if candidate.rank != 1:
            return NgramReadingTransitionJudgment(
                False,
                "non_dominant_reading_transition",
                candidate=candidate,
                dominant=dominant,
            )
        if candidate.count < min_count:
            return NgramReadingTransitionJudgment(
                False,
                "insufficient_dominant_count",
                candidate=candidate,
                dominant=dominant,
            )
        if candidate.share < min_share:
            return NgramReadingTransitionJudgment(
                False,
                "weak_dominant_share",
                candidate=candidate,
                dominant=dominant,
            )
        return NgramReadingTransitionJudgment(
            True,
            "dominant_reading_transition",
            candidate=candidate,
            dominant=dominant,
        )


def resolve_ngram_reading_transitions_artifact(
    decoder_model_dir: str | Path | None,
) -> Path | None:
    if not decoder_model_dir:
        return None
    artifact = Path(decoder_model_dir) / MODEL_NGRAM_READING_TRANSITIONS_FILENAME
    return artifact if artifact.exists() else None


def build_ngram_reading_transition_stats(
    *,
    source_corpus: str | Path,
    output_tsv: str | Path,
    manifest_json: str | Path,
    source_corpus_version: str,
    additional_source_corpora: Sequence[str | Path] = (),
    min_surface_count: int = DEFAULT_MIN_SURFACE_COUNT,
    shard_count: int = DEFAULT_SHARD_COUNT,
    checksum: bool = True,
) -> NgramReadingTransitionBuildSummary:
    if min_surface_count < 1:
        raise ValueError("min_surface_count must be at least 1")
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")

    source_path = Path(source_corpus)
    source_paths = [source_path, *(Path(path) for path in additional_source_corpora)]
    output_path = Path(output_tsv)
    manifest_path = Path(manifest_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    source_token_count = 0
    observed_transition_count = 0
    retained_surface_count = 0
    retained_reading_count = 0

    with TemporaryDirectory(prefix="yomi-ngram-reading-transition-") as temporary:
        shard_paths = [
            Path(temporary) / f"shard-{index:04d}.tsv"
            for index in range(shard_count)
        ]
        handles = [path.open("w", encoding="utf-8", newline="") for path in shard_paths]
        writers = [csv.writer(handle, delimiter="\t", lineterminator="\n") for handle in handles]
        try:
            for corpus_path in source_paths:
                for sentence, _malformed in iter_source_corpus_segments(corpus_path):
                    source_token_count += len(sentence)
                    for left, right in zip(sentence, sentence[1:]):
                        if not is_candidate_surface(left.surface + right.surface):
                            continue
                        observed_transition_count += 1
                        shard_index = transition_shard(
                            left.surface,
                            right.surface,
                            shard_count,
                        )
                        writers[shard_index].writerow(
                            [
                                left.surface,
                                right.surface,
                                left.reading,
                                right.reading,
                            ]
                        )
        finally:
            for handle in handles:
                handle.close()

        with output_path.open("w", encoding="utf-8", newline="") as output_handle:
            writer = csv.writer(output_handle, delimiter="\t", lineterminator="\n")
            writer.writerow(
                [
                    "left_surface",
                    "right_surface",
                    "left_reading",
                    "right_reading",
                    "count",
                    "surface_total_count",
                    "share",
                    "rank",
                    "source_corpus_version",
                ]
            )
            for shard_path in shard_paths:
                counts: dict[
                    tuple[str, str], Counter[tuple[str, str]]
                ] = defaultdict(Counter)
                with shard_path.open(encoding="utf-8", newline="") as handle:
                    for (
                        left_surface,
                        right_surface,
                        left_reading,
                        right_reading,
                    ) in csv.reader(handle, delimiter="\t"):
                        counts[(left_surface, right_surface)][
                            (left_reading, right_reading)
                        ] += 1
                for (left_surface, right_surface), reading_counts in sorted(counts.items()):
                    total = sum(reading_counts.values())
                    if total < min_surface_count:
                        continue
                    retained_surface_count += 1
                    ranked = sorted(
                        reading_counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                    for rank, ((left_reading, right_reading), count) in enumerate(
                        ranked,
                        start=1,
                    ):
                        retained_reading_count += 1
                        writer.writerow(
                            [
                                left_surface,
                                right_surface,
                                left_reading,
                                right_reading,
                                count,
                                total,
                                f"{count / total:.12g}",
                                rank,
                                source_corpus_version,
                            ]
                        )

    source_digests = [sha256_file(path) for path in source_paths] if checksum else []
    digest = combined_source_digest(source_paths, source_digests) if checksum else None
    summary = NgramReadingTransitionBuildSummary(
        source_corpus=str(source_path),
        output_tsv=str(output_path),
        manifest_json=str(manifest_path),
        source_corpus_version=source_corpus_version,
        source_token_count=source_token_count,
        observed_transition_count=observed_transition_count,
        retained_surface_transition_count=retained_surface_count,
        retained_reading_transition_count=retained_reading_count,
        min_surface_count=min_surface_count,
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
            "min_surface_count": min_surface_count,
            "default_safety_min_reading_count": DEFAULT_MIN_READING_COUNT,
            "default_safety_min_reading_share": DEFAULT_MIN_READING_SHARE,
            "shard_count": shard_count,
        },
        "output_tsv": str(output_path),
        "summary": asdict(summary),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def transition_shard(left_surface: str, right_surface: str, shard_count: int) -> int:
    digest = hashlib.blake2b(
        f"{left_surface}\0{right_surface}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") % shard_count
