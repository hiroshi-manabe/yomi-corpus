#!/usr/bin/env python3
"""Report BCCWJ token surfaces that are rare in finalized decoder exports."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BCCWJ = ROOT.parent / "yomi-decoder/data/raw/core_SUW_yomi_final.txt"
DEFAULT_PROCESSED_GLOB = str(ROOT / "data/decoder_corpora/dev/dev_batch_*.txt")
DEFAULT_OUTPUT_DIR = ROOT / "data/analysis/reordering"

JAPANESE_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002ffff]"
)
NUMERIC_ONLY_RE = re.compile(r"[0-9０-９〇○零一二三四五六七八九十百千万億兆]+")


def _compact_counts(counts: Counter[str]) -> str:
    return "|".join(
        f"{value}:{count}"
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def _read_bccwj(path: Path) -> tuple[Counter[str], dict[str, Counter[str]], dict[str, Counter[str]]]:
    surfaces: Counter[str] = Counter()
    readings: dict[str, Counter[str]] = defaultdict(Counter)
    parts_of_speech: dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line or line == "EOS":
                continue
            fields = line.split("\t")
            surface = fields[0]
            surfaces[surface] += 1
            if len(fields) > 1 and fields[1]:
                parts_of_speech[surface][fields[1]] += 1
            if len(fields) > 4 and fields[4]:
                readings[surface][fields[4]] += 1
    return surfaces, readings, parts_of_speech


def _read_processed(paths: list[Path]) -> Counter[str]:
    surfaces: Counter[str] = Counter()
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if not line or line == "EOS":
                    continue
                surfaces[line.split("\t", 1)[0]] += 1
    return surfaces


def _is_lexical(surface: str, parts_of_speech: Counter[str]) -> bool:
    if not JAPANESE_RE.search(surface) or NUMERIC_ONLY_RE.fullmatch(surface):
        return False
    return any(not pos.startswith(("補助記号", "空白")) for pos in parts_of_speech)


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("surface", "bccwj_count", "processed_count", "bccwj_readings", "bccwj_pos"),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bccwj", type=Path, default=DEFAULT_BCCWJ)
    parser.add_argument("--processed-glob", default=DEFAULT_PROCESSED_GLOB)
    parser.add_argument("--max-processed-count", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    processed_paths = [Path(path) for path in sorted(glob.glob(args.processed_glob))]
    if not processed_paths:
        parser.error(f"no processed corpora matched: {args.processed_glob}")

    bccwj, readings, parts_of_speech = _read_bccwj(args.bccwj)
    processed = _read_processed(processed_paths)
    selected = [
        surface for surface in bccwj if processed[surface] <= args.max_processed_count
    ]
    selected.sort(key=lambda surface: (-bccwj[surface], processed[surface], surface))

    rows = [
        {
            "surface": surface,
            "bccwj_count": bccwj[surface],
            "processed_count": processed[surface],
            "bccwj_readings": _compact_counts(readings[surface]),
            "bccwj_pos": _compact_counts(parts_of_speech[surface]),
        }
        for surface in selected
    ]
    lexical_rows = [
        row for row in rows if _is_lexical(str(row["surface"]), parts_of_speech[str(row["surface"])])
    ]

    all_path = args.output_dir / "bccwj_surfaces_processed_at_most_once.tsv"
    lexical_path = args.output_dir / "bccwj_lexical_surfaces_processed_at_most_once.tsv"
    manifest_path = args.output_dir / "bccwj_surfaces_processed_at_most_once.json"
    _write_tsv(all_path, rows)
    _write_tsv(lexical_path, lexical_rows)

    manifest = {
        "bccwj_path": str(args.bccwj.resolve()),
        "processed_glob": args.processed_glob,
        "processed_file_count": len(processed_paths),
        "max_processed_count": args.max_processed_count,
        "bccwj_token_count": sum(bccwj.values()),
        "bccwj_distinct_surface_count": len(bccwj),
        "processed_token_count": sum(processed.values()),
        "processed_distinct_surface_count": len(processed),
        "matching_surface_count": len(rows),
        "matching_lexical_surface_count": len(lexical_rows),
        "all_output": str(all_path.resolve()),
        "lexical_output": str(lexical_path.resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
