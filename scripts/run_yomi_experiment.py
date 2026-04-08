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

from yomi_corpus.yomi import load_yomi_generation_config
from yomi_corpus.yomi.experiments import load_eval_items, run_yomi_experiment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one yomi-generation strategy on an eval set.")
    parser.add_argument(
        "--eval-jsonl",
        default="data/evals/yomi_generation/dev.jsonl",
        help="Eval set JSONL path.",
    )
    parser.add_argument(
        "--config",
        default="config/yomi/default.toml",
        help="Yomi generation config TOML.",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Strategy name to run. Defaults to the config default.",
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Directory for experiment artifacts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yomi_generation_config(args.config)
    strategy_name = args.strategy or config.default_strategy
    summary = run_yomi_experiment(
        eval_items=load_eval_items(PROJECT_ROOT / args.eval_jsonl),
        config=config,
        strategy_name=strategy_name,
        run_dir=PROJECT_ROOT / args.run_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
