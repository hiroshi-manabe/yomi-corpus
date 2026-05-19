#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_INPUT = Path("/panfs/panmt22/users/hmanabe/yomi_tagger/sudachi_20251022/sudachi_3_3.csv")
DEFAULT_OUTPUT = Path("data/external/sudachi_annotated_forms/sudachi_20251022.tsv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract a reduced Sudachi annotated-form lookup as "
            "surface<TAB>reading<TAB>annotated_surface."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"Source CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output TSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument(
        "--summary",
        type=Path,
        help="Summary JSON path. Default: output path with .summary.json suffix.",
    )
    parser.add_argument(
        "--include-plain",
        action="store_true",
        help="Include rows whose annotated form is identical to the surface. Default keeps only useful annotated rows.",
    )
    parser.add_argument(
        "--include-non-furigana",
        action="store_true",
        help="Include non-parenthesized annotated forms. Default keeps only forms containing full-width furigana parentheses.",
    )
    parser.add_argument(
        "--keep-boundaries",
        action="store_true",
        help="Keep dictionary-internal '|' boundaries in annotated_surface. Default removes them.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary_path = args.summary or args.output.with_suffix(args.output.suffix + ".summary.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    stats = Counter()
    value_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    with args.input.open(encoding="utf-8", errors="replace", newline="") as src:
        reader = csv.reader(src)
        for row in reader:
            stats["input_rows"] += 1
            if len(row) < 12:
                stats["short_rows"] += 1
                continue

            annotated = row[0]
            surface = row[4]
            reading = row[11]
            if not surface or not reading or not annotated:
                stats["missing_required"] += 1
                continue

            if not args.keep_boundaries:
                annotated = annotated.replace("|", "")

            if not args.include_non_furigana and not ("（" in annotated and "）" in annotated):
                stats["non_furigana_rows_skipped"] += 1
                continue

            if not args.include_plain and annotated == surface:
                stats["plain_rows_skipped"] += 1
                continue

            value_counts[(surface, reading)][annotated] += 1
            stats["candidate_rows"] += 1

    with args.output.open("w", encoding="utf-8", newline="") as dst:
        writer = csv.writer(dst, delimiter="\t", lineterminator="\n")
        writer.writerow(["surface", "reading", "annotated_surface"])
        for (surface, reading), annotated_counts in sorted(value_counts.items()):
            for annotated in sorted(annotated_counts):
                writer.writerow([surface, reading, annotated])
                stats["output_rows"] += 1

    ambiguous_keys = {
        key: counts for key, counts in value_counts.items() if len(counts) > 1
    }
    output_bytes = args.output.stat().st_size
    summary = {
        "input": str(args.input),
        "output": str(args.output),
        "include_plain": bool(args.include_plain),
        "include_non_furigana": bool(args.include_non_furigana),
        "keep_boundaries": bool(args.keep_boundaries),
        "stats": dict(stats),
        "unique_surface_reading_keys": len(value_counts),
        "ambiguous_surface_reading_keys": len(ambiguous_keys),
        "output_bytes": output_bytes,
        "output_mib": round(output_bytes / 1024 / 1024, 3),
        "ambiguous_examples": [
            {
                "surface": surface,
                "reading": reading,
                "annotated_surfaces": sorted(counts),
            }
            for (surface, reading), counts in sorted(ambiguous_keys.items())[:50]
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
