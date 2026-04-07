#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_reports import build_unresolved_entity_rows, load_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a review/LLM-ready report of unresolved Latin/alphanumeric entities for one batch."
    )
    parser.add_argument(
        "--input-types",
        default="data/units/batch_0001/alphabetic_types.jsonl",
        help="Alphabetic entity-type JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--output-jsonl",
        default="data/units/batch_0001/alphabetic_unresolved_entities.jsonl",
        help="Output JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--output-tsv",
        default="data/units/batch_0001/alphabetic_unresolved_entities.tsv",
        help="Output TSV path relative to repo root.",
    )
    parser.add_argument(
        "--min-occurrences",
        type=int,
        default=1,
        help="Only include unresolved entities with at least this many occurrences.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=3,
        help="Maximum number of example sentences to keep per entity.",
    )
    parser.add_argument(
        "--max-example-chars",
        type=int,
        default=160,
        help="Maximum number of characters to keep per example snippet.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = PROJECT_ROOT / args.input_types
    output_jsonl_path = PROJECT_ROOT / args.output_jsonl
    output_tsv_path = PROJECT_ROOT / args.output_tsv
    output_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    output_tsv_path.parent.mkdir(parents=True, exist_ok=True)

    rows = load_jsonl(input_path)
    unresolved = build_unresolved_entity_rows(
        rows,
        min_occurrences=args.min_occurrences,
        max_examples=args.max_examples,
        max_example_chars=args.max_example_chars,
    )

    with output_jsonl_path.open("w", encoding="utf-8") as handle:
        for row in unresolved:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    with output_tsv_path.open("w", encoding="utf-8") as handle:
        header = [
            "entity_key",
            "strict_case",
            "resolved_status",
            "base_list_status",
            "occurrence_count",
            "unit_count",
            "surface_forms",
            "example_unit_ids",
            "example_texts",
        ]
        handle.write("\t".join(header) + "\n")
        for row in unresolved:
            handle.write(
                "\t".join(
                    [
                        row["entity_key"],
                        str(row["strict_case"]),
                        row["resolved_status"],
                        row["base_list_status"],
                        str(row["occurrence_count"]),
                        str(row["unit_count"]),
                        " | ".join(row["surface_forms"]),
                        " | ".join(row["example_unit_ids"]),
                        " || ".join(text.replace("\t", " ").replace("\n", " ") for text in row["example_texts"]),
                    ]
                )
                + "\n"
            )


if __name__ == "__main__":
    main()
