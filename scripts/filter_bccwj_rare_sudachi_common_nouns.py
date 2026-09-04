#!/usr/bin/env python3
"""Filter a BCCWJ coverage report to registered Sudachi common nouns."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sudachipy import dictionary, tokenizer


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT / "data/analysis/reordering/bccwj_lexical_surfaces_processed_at_most_once.tsv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "data/analysis/reordering/"
    / "bccwj_rare_sudachi_common_nouns_min5.tsv"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-bccwj-count", type=int, default=5)
    parser.add_argument(
        "--dictionary",
        choices=("small", "core", "full"),
        default="full",
        help="Installed Sudachi dictionary edition to query (default: full).",
    )
    args = parser.parse_args()

    sudachi = dictionary.Dictionary(dict=args.dictionary).create()
    mode = tokenizer.Tokenizer.SplitMode.C
    selected: list[dict[str, str | int]] = []

    with args.input.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            bccwj_count = int(row["bccwj_count"])
            if bccwj_count < args.min_bccwj_count:
                continue
            tokens = sudachi.tokenize(row["surface"], mode)
            if len(tokens) != 1:
                continue
            token = tokens[0]
            pos = token.part_of_speech()
            if token.is_oov() or token.surface() != row["surface"]:
                continue
            if pos[:2] != ("名詞", "普通名詞"):
                continue
            selected.append(
                {
                    **row,
                    "sudachi_reading": token.reading_form(),
                    "sudachi_dictionary_form": token.dictionary_form(),
                    "sudachi_pos": ",".join(pos),
                    "sudachi_word_id": token.word_id(),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "surface",
        "bccwj_count",
        "processed_count",
        "bccwj_readings",
        "bccwj_pos",
        "sudachi_reading",
        "sudachi_dictionary_form",
        "sudachi_pos",
        "sudachi_word_id",
    )
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(selected)

    print(f"selected={len(selected)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
