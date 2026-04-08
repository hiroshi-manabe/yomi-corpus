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

from yomi_corpus.yomi.export import available_export_variant_names, export_named_variant


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export batch yomi artifacts in JSONL and/or plain text."
    )
    parser.add_argument(
        "--batch-dir",
        default="data/units/batch_0001",
        help="Batch directory containing units.jsonl.",
    )
    parser.add_argument(
        "--config",
        default="config/yomi/default.toml",
        help="Yomi generation config TOML.",
    )
    parser.add_argument(
        "--variant",
        action="append",
        choices=available_export_variant_names(),
        help="Export variant to generate. Repeatable. Defaults to both aligned_hybrid and sudachi_only.",
    )
    parser.add_argument(
        "--format",
        action="append",
        choices=["jsonl", "txt"],
        help="Output format to generate. Repeatable. Defaults to both jsonl and txt.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    variants = args.variant or ["aligned_hybrid", "sudachi_only"]
    formats = args.format or ["jsonl", "txt"]
    summaries = []
    for variant_name in variants:
        summaries.append(
            export_named_variant(
                variant_name=variant_name,
                batch_dir=args.batch_dir,
                config_path=args.config,
                formats=formats,
                show_progress=not args.no_progress,
            )
        )
    print(json.dumps({"exports": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
