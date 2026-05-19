#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from contextlib import nullcontext
from pathlib import Path

from yomi_corpus.yomi.furigana import FuriganaConverter, result_to_json_line


DEFAULT_LOOKUP = Path("data/external/sudachi_annotated_forms/sudachi_20251022.tsv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render surface/reading pairs as parenthesized furigana forms using "
            "the reduced Sudachi annotated-form lookup plus alignment fallback."
        )
    )
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP, help=f"Lookup TSV. Default: {DEFAULT_LOOKUP}")
    parser.add_argument(
        "--input",
        type=Path,
        help="Input TSV with surface and reading columns. Default reads TSV from stdin.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path. Default writes to stdout.",
    )
    parser.add_argument(
        "--format",
        choices=("tsv", "jsonl"),
        default="tsv",
        help="Output format. Default: tsv.",
    )
    parser.add_argument("--surface", help="Single surface form to convert.")
    parser.add_argument("--reading", help="Single reading to convert.")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=2000,
        help="Maximum fallback alignments to enumerate per pair. Default: 2000.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if bool(args.surface) != bool(args.reading):
        raise SystemExit("--surface and --reading must be specified together")
    converter = FuriganaConverter.from_tsv(args.lookup, max_candidates=args.max_candidates)

    rows: list[tuple[str, str]]
    if args.surface and args.reading:
        rows = [(args.surface, args.reading)]
    else:
        input_context = args.input.open(encoding="utf-8", newline="") if args.input else nullcontext(sys.stdin)
        with input_context as input_handle:
            reader = csv.DictReader(input_handle, delimiter="\t")
            if not {"surface", "reading"}.issubset(reader.fieldnames or set()):
                raise SystemExit("input TSV must contain surface and reading columns")
            rows = [(row["surface"], row["reading"]) for row in reader if row.get("surface") and row.get("reading")]

    output_context = args.output.open("w", encoding="utf-8", newline="") if args.output else nullcontext(sys.stdout)
    with output_context as output_handle:
        if args.format == "jsonl":
            for surface, reading in rows:
                output_handle.write(result_to_json_line(converter.convert(surface, reading)) + "\n")
            return

        writer = csv.writer(output_handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["surface", "reading", "annotated_surface", "method", "confidence", "reason"])
        for surface, reading in rows:
            result = converter.convert(surface, reading)
            writer.writerow(
                [
                    result.surface,
                    result.reading,
                    result.annotated_surface or "",
                    result.method,
                    result.confidence,
                    result.reason or "",
                ]
            )


if __name__ == "__main__":
    main()
