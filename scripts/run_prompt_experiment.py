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
from yomi_corpus.llm.runner import LLM_EXECUTION_MODES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one prompt experiment on a fixed eval set.")
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
        "--rendered-yomi-display",
        choices=["full", "compact", "furigana_no_space"],
        help="Override yomi rendering for prompt inputs. Use full/compact/furigana_no_space for format A/B tests.",
    )
    parser.add_argument(
        "--include-source-text",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override whether yomi prompt variables include source text. Default uses task config.",
    )
    parser.add_argument(
        "--yomi-context-side-chars",
        type=int,
        help=(
            "For yomi-reading experiments, force this many source characters "
            "on each side of the marked target. Zero keeps only the target."
        ),
    )
    parser.add_argument(
        "--processing-tier",
        choices=["standard", "batch", "priority", "flex"],
        default="standard",
        help="Pricing tier used for cost estimation. Default: standard.",
    )
    parser.add_argument(
        "--llm-mode",
        choices=sorted(LLM_EXECUTION_MODES),
        default="sync",
        help="LLM execution mode. Default: sync.",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Show runner progress for sync/background/batch modes.",
    )
    parser.add_argument(
        "--batch-no-wait",
        action="store_true",
        help="For --llm-mode background/batch, submit/poll once and exit instead of waiting.",
    )
    parser.add_argument(
        "--batch-poll-interval-seconds",
        type=float,
        default=60.0,
        help="For --llm-mode background/batch, polling interval while waiting. Default: 60.",
    )
    parser.add_argument(
        "--batch-max-wait-seconds",
        type=float,
        help="For --llm-mode background/batch, stop waiting after this many seconds.",
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
        rendered_yomi_display=args.rendered_yomi_display,
        include_source_text=args.include_source_text,
        yomi_reading_context_side_chars=args.yomi_context_side_chars,
        execution_mode=args.llm_mode,
        show_progress=args.show_progress,
        batch_wait=not args.batch_no_wait,
        batch_poll_interval_seconds=args.batch_poll_interval_seconds,
        batch_max_wait_seconds=args.batch_max_wait_seconds,
        processing_tier=args.processing_tier,
        pricing_config_path=args.pricing_config,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
