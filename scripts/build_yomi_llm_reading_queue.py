#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from yomi_corpus.yomi.llm_readings import build_yomi_llm_reading_queue_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Build LLM reading-generation queue items.")
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument(
        "--skip-stable-two-kanji",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip stable two-kanji tokens with unique raw SudachiDict readings.",
    )
    args = parser.parse_args()

    summary = build_yomi_llm_reading_queue_file(
        input_jsonl=Path(args.input_jsonl),
        output_jsonl=Path(args.output_jsonl),
        summary_json=Path(args.summary_json),
        skip_stable_two_kanji=args.skip_stable_two_kanji,
    )
    print(summary)


if __name__ == "__main__":
    main()
