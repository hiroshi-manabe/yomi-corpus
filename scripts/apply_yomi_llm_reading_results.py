#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from yomi_corpus.yomi.llm_readings import apply_yomi_llm_reading_results_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply LLM reading-generation results to yomi units.")
    parser.add_argument("--units-jsonl", required=True)
    parser.add_argument("--queue-jsonl", required=True)
    parser.add_argument("--results-jsonl", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--summary-json", required=True)
    args = parser.parse_args()

    summary = apply_yomi_llm_reading_results_file(
        units_jsonl=Path(args.units_jsonl),
        queue_jsonl=Path(args.queue_jsonl),
        results_jsonl=Path(args.results_jsonl),
        output_jsonl=Path(args.output_jsonl),
        summary_json=Path(args.summary_json),
    )
    print(summary)


if __name__ == "__main__":
    main()
