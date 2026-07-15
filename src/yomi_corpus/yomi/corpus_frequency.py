from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Sequence


KANJI_OR_ALPHABETIC_RE = re.compile(r"[\u3400-\u9fff々〆〻A-Za-zＡ-Ｚａ-ｚ]")
SCRIPT_VERSION = "surface_reading_stats_v1"


@dataclass(frozen=True)
class SourceToken:
    surface: str
    reading: str


@dataclass(frozen=True)
class SurfaceReadingCount:
    surface: str
    reading: str
    count: int
    surface_total_count: int
    share: float
    source_corpus_version: str


@dataclass(frozen=True)
class DominantReading:
    surface: str
    reading: str
    count: int
    surface_total_count: int
    share: float
    source_corpus_version: str


@dataclass(frozen=True)
class CorpusFrequencyBuildSummary:
    source_corpus: str
    output_tsv: str
    manifest_json: str
    source_corpus_version: str
    surface_filter: str
    token_count: int
    counted_token_count: int
    skipped_malformed_line_count: int
    surface_count: int
    pair_count: int
    checksum_sha256: str | None


class SurfaceReadingStats:
    def __init__(
        self,
        *,
        rows_by_surface: dict[str, list[SurfaceReadingCount]],
        source_corpus_version: str | None = None,
        artifact_path: str | None = None,
    ) -> None:
        self.rows_by_surface = rows_by_surface
        self.source_corpus_version = source_corpus_version
        self.artifact_path = artifact_path

    @classmethod
    def load_tsv(cls, path: str | Path) -> SurfaceReadingStats:
        stats_path = Path(path)
        rows_by_surface: dict[str, list[SurfaceReadingCount]] = defaultdict(list)
        versions: set[str] = set()
        with stats_path.open(encoding="utf-8", newline="") as handle:
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
                raise ValueError(f"Stats TSV missing fields: {sorted(missing)}")
            for row in reader:
                item = SurfaceReadingCount(
                    surface=row["surface"],
                    reading=row["reading"],
                    count=int(row["count"]),
                    surface_total_count=int(row["surface_total_count"]),
                    share=float(row["share"]),
                    source_corpus_version=row["source_corpus_version"],
                )
                rows_by_surface[item.surface].append(item)
                versions.add(item.source_corpus_version)

        for rows in rows_by_surface.values():
            rows.sort(key=lambda item: (-item.count, item.reading))
        version = next(iter(versions)) if len(versions) == 1 else None
        return cls(
            rows_by_surface=dict(rows_by_surface),
            source_corpus_version=version,
            artifact_path=str(stats_path),
        )

    def dominant_reading(
        self,
        surface: str,
        *,
        min_count: int,
        min_share: float,
    ) -> DominantReading | None:
        rows = self.rows_by_surface.get(surface)
        if not rows:
            return None
        top = rows[0]
        if top.count < min_count or top.share < min_share:
            return None
        return DominantReading(
            surface=top.surface,
            reading=top.reading,
            count=top.count,
            surface_total_count=top.surface_total_count,
            share=top.share,
            source_corpus_version=top.source_corpus_version,
        )

    def matches_dominant(
        self,
        surface: str,
        reading: str,
        *,
        min_count: int,
        min_share: float,
    ) -> bool:
        dominant = self.dominant_reading(
            surface,
            min_count=min_count,
            min_share=min_share,
        )
        return dominant is not None and dominant.reading == reading


def build_surface_reading_stats(
    *,
    source_corpus: str | Path,
    output_tsv: str | Path,
    manifest_json: str | Path,
    source_corpus_version: str,
    surface_filter: str = "target",
    checksum: bool = True,
    additional_source_corpora: Sequence[str | Path] = (),
) -> CorpusFrequencyBuildSummary:
    source_path = Path(source_corpus)
    source_paths = [source_path, *(Path(path) for path in additional_source_corpora)]
    output_path = Path(output_tsv)
    manifest_path = Path(manifest_json)
    if surface_filter not in {"all", "target"}:
        raise ValueError("surface_filter must be 'all' or 'target'")

    token_count = 0
    counted_token_count = 0
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    skipped_malformed_line_count = 0
    for corpus_path in source_paths:
        for token in iter_source_corpus_token_records(corpus_path):
            if token is None:
                skipped_malformed_line_count += 1
                continue
            token_count += 1
            if surface_filter == "target" and not is_target_surface(token.surface):
                continue
            counts[token.surface][token.reading] += 1
            counted_token_count += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_stats_tsv(
        output_path,
        counts=counts,
        source_corpus_version=source_corpus_version,
    )

    source_digests = [sha256_file(path) for path in source_paths] if checksum else []
    digest = combined_source_digest(source_paths, source_digests) if checksum else None
    summary = CorpusFrequencyBuildSummary(
        source_corpus=str(source_path),
        output_tsv=str(output_path),
        manifest_json=str(manifest_path),
        source_corpus_version=source_corpus_version,
        surface_filter=surface_filter,
        token_count=token_count,
        counted_token_count=counted_token_count,
        skipped_malformed_line_count=skipped_malformed_line_count,
        surface_count=len(counts),
        pair_count=sum(len(reading_counts) for reading_counts in counts.values()),
        checksum_sha256=digest,
    )
    manifest = {
        "script_version": SCRIPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_corpus_path": str(source_path),
        "source_corpus_paths": [str(path) for path in source_paths],
        "source_corpus_version": source_corpus_version,
        "source_corpus_size_bytes": source_path.stat().st_size,
        "source_corpora_total_size_bytes": sum(path.stat().st_size for path in source_paths),
        "source_corpus_mtime": datetime.fromtimestamp(
            source_path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat(),
        "source_corpus_sha256": source_digests[0] if checksum else None,
        "source_corpora_sha256": digest,
        "source_corpora": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                "sha256": source_digests[index] if checksum else None,
            }
            for index, path in enumerate(source_paths)
        ],
        "normalization": {
            "policy": "none",
            "notes": "Surface and reading strings are counted exactly as found in the source TSV.",
        },
        "filters": {
            "surface_filter": surface_filter,
            "target_surface_regex": KANJI_OR_ALPHABETIC_RE.pattern if surface_filter == "target" else None,
            "skip_eos": True,
            "skip_empty_surface_or_reading": True,
            "skip_malformed_rows": True,
        },
        "output_tsv": str(output_path),
        "summary": {
            "token_count": summary.token_count,
            "counted_token_count": summary.counted_token_count,
            "skipped_malformed_line_count": summary.skipped_malformed_line_count,
            "surface_count": summary.surface_count,
            "pair_count": summary.pair_count,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def iter_source_corpus_tokens(path: str | Path) -> Iterable[SourceToken]:
    for token in iter_source_corpus_token_records(path):
        if token is not None:
            yield token


def iter_source_corpus_token_records(path: str | Path) -> Iterable[SourceToken | None]:
    source_path = Path(path)
    with source_path.open(encoding="utf-8", newline="") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line or line == "EOS":
                continue
            columns = line.split("\t")
            if len(columns) < 5:
                yield None
                continue
            surface = columns[0]
            reading = columns[4]
            if not surface or not reading:
                continue
            yield SourceToken(surface=surface, reading=reading)


def write_stats_tsv(
    path: str | Path,
    *,
    counts: dict[str, Counter[str]],
    source_corpus_version: str,
) -> None:
    output_path = Path(path)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "surface",
                "reading",
                "count",
                "surface_total_count",
                "share",
                "source_corpus_version",
            ]
        )
        for surface in sorted(counts):
            reading_counts = counts[surface]
            total = sum(reading_counts.values())
            for reading, count in sorted(reading_counts.items(), key=lambda item: (-item[1], item[0])):
                writer.writerow(
                    [
                        surface,
                        reading,
                        count,
                        total,
                        f"{count / total:.12g}",
                        source_corpus_version,
                    ]
                )


def is_target_surface(surface: str) -> bool:
    return bool(KANJI_OR_ALPHABETIC_RE.search(surface))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_source_digest(paths: Sequence[Path], digests: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for path, source_digest in zip(paths, digests, strict=True):
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_digest.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()
