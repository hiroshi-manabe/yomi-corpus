#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Batch API input for vocabulary-familiarity classification."
    )
    parser.add_argument("--input-tsv", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--min-document-count", type=int, default=10)
    parser.add_argument("--min-lemma-chars", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_tsv)
    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    selected = 0
    with input_path.open(encoding="utf-8", newline="") as source, output_path.open(
        "w", encoding="utf-8"
    ) as destination:
        rows = csv.DictReader(source, delimiter="\t")
        for row in rows:
            lemma = row["lemma"]
            document_count = int(row["bccwj_common_noun_document_count"])
            if len(lemma) < args.min_lemma_chars or document_count < args.min_document_count:
                continue
            selected += 1
            destination.write(
                json.dumps(
                    {
                        "item_id": f"vocabulary_familiarity_{selected:05d}",
                        "text": lemma,
                        "lemma": lemma,
                        "bccwj_common_noun_document_count": document_count,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"Wrote {selected} candidates to {output_path}")


if __name__ == "__main__":
    main()
