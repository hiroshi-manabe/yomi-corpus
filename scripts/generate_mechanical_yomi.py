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
from yomi_corpus.yomi.export import ProgressBar, count_nonempty_lines
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic yomi generation for unit records.")
    parser.add_argument(
        "--input-jsonl",
        default="data/units/batch_0001/units.jsonl",
        help="Input units JSONL.",
    )
    parser.add_argument(
        "--output-jsonl",
        default="data/units/batch_0001/units.yomi.jsonl",
        help="Output units JSONL with updated mechanical yomi fields.",
    )
    parser.add_argument(
        "--config",
        default="config/yomi/default.toml",
        help="Yomi generation config TOML.",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Override yomi strategy name.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of units to process.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yomi_generation_config(args.config)
    count = 0
    input_path = PROJECT_ROOT / args.input_jsonl
    output_path = PROJECT_ROOT / args.output_jsonl
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = count_nonempty_lines(input_path)
    if args.limit is not None:
        total = min(total, args.limit)
    progress = None
    if not args.no_progress:
        progress = ProgressBar(label="mechanical yomi jsonl", total=total)
    with input_path.open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            if args.limit is not None and count >= args.limit:
                dst.write(json.dumps(row, ensure_ascii=False) + "\n")
                continue
            row["analysis"]["mechanical"]["yomi"] = generate_mechanical_yomi(
                row["text"],
                config=config,
                strategy_name=args.strategy,
            ).__dict__
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
            if progress is not None:
                progress.update()
    if progress is not None:
        progress.finish()
    print({"processed_units": count, "output_jsonl": str(output_path)})


if __name__ == "__main__":
    main()
