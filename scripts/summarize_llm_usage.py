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

from yomi_corpus.llm.pricing import DEFAULT_PRICING_CONFIG_PATH
from yomi_corpus.llm.usage_report import summarize_batch_job, summarize_results_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize token usage, cache hits, and estimated cost.")
    parser.add_argument("--job-dir", help="Batch job directory to summarize.")
    parser.add_argument("--results-jsonl", help="Sync result JSONL to summarize.")
    parser.add_argument("--model", help="Model name for results-jsonl mode.")
    parser.add_argument(
        "--processing-tier",
        choices=["standard", "batch", "priority", "flex"],
        default="standard",
        help="Pricing tier for results-jsonl mode. Default: standard.",
    )
    parser.add_argument(
        "--pricing-config",
        default=DEFAULT_PRICING_CONFIG_PATH,
        help=f"Pricing config path relative to repo root. Default: {DEFAULT_PRICING_CONFIG_PATH}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.job_dir:
        print(json.dumps(summarize_batch_job(args.job_dir, pricing_config_path=args.pricing_config), ensure_ascii=False, indent=2))
        return

    if args.results_jsonl:
        if not args.model:
            raise SystemExit("--model is required with --results-jsonl.")
        print(
            json.dumps(
                summarize_results_jsonl(
                    args.results_jsonl,
                    model=args.model,
                    processing_tier=args.processing_tier,
                    pricing_config_path=args.pricing_config,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    raise SystemExit("Use either --job-dir or --results-jsonl.")


if __name__ == "__main__":
    main()
