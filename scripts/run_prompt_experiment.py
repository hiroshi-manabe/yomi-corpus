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

from yomi_corpus.llm.experiments import run_prompt_experiment
from yomi_corpus.llm.pricing import DEFAULT_PRICING_CONFIG_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one sync prompt experiment on a fixed eval set.")
    parser.add_argument("--task-config", required=True, help="Task config TOML path relative to repo root.")
    parser.add_argument("--eval-jsonl", required=True, help="Eval JSONL path relative to repo root.")
    parser.add_argument("--run-dir", required=True, help="Experiment run directory relative to repo root.")
    parser.add_argument("--api-key-file", help="Optional OpenAI API key file override.")
    parser.add_argument("--prompt-template", help="Prompt template override path relative to repo root.")
    parser.add_argument("--model", help="Model override.")
    parser.add_argument("--reasoning-effort", help="Reasoning effort override.")
    parser.add_argument("--verbosity", help="Verbosity override.")
    parser.add_argument("--max-output-tokens", type=int, help="Max output tokens override.")
    parser.add_argument(
        "--processing-tier",
        choices=["standard", "batch", "priority", "flex"],
        default="standard",
        help="Pricing tier used for cost estimation. Default: standard.",
    )
    parser.add_argument(
        "--pricing-config",
        default=DEFAULT_PRICING_CONFIG_PATH,
        help=f"Pricing config path relative to repo root. Default: {DEFAULT_PRICING_CONFIG_PATH}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_prompt_experiment(
        task_config_path=args.task_config,
        eval_jsonl_path=args.eval_jsonl,
        run_dir=args.run_dir,
        api_key_file=args.api_key_file,
        prompt_template=args.prompt_template,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        verbosity=args.verbosity,
        max_output_tokens=args.max_output_tokens,
        processing_tier=args.processing_tier,
        pricing_config_path=args.pricing_config,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
