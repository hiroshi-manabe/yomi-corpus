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

from yomi_corpus.llm.token_count import (
    DEFAULT_ENCODING_NAME,
    count_task_prompt_tokens,
    count_text_tokens,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count local prompt tokens with tiktoken.")
    parser.add_argument(
        "--encoding",
        default=DEFAULT_ENCODING_NAME,
        help=f"Tiktoken encoding name. Default: {DEFAULT_ENCODING_NAME}.",
    )
    parser.add_argument(
        "--text",
        help="Count tokens for a literal text string.",
    )
    parser.add_argument(
        "--file",
        help="Count tokens for the contents of a text file.",
    )
    parser.add_argument(
        "--task-config",
        help="Task config TOML path relative to repo root for rendered prompt counting.",
    )
    parser.add_argument(
        "--input-jsonl",
        help="Input JSONL path relative to repo root for rendered prompt counting.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.text is not None:
        print(
            json.dumps(
                {
                    "mode": "text",
                    "encoding": args.encoding,
                    "token_count": count_text_tokens(args.text, args.encoding),
                    "character_count": len(args.text),
                },
                ensure_ascii=False,
            )
        )
        return

    if args.file is not None:
        text = Path(args.file).read_text(encoding="utf-8")
        print(
            json.dumps(
                {
                    "mode": "file",
                    "path": args.file,
                    "encoding": args.encoding,
                    "token_count": count_text_tokens(text, args.encoding),
                    "character_count": len(text),
                },
                ensure_ascii=False,
            )
        )
        return

    if args.task_config and args.input_jsonl:
        rows = count_task_prompt_tokens(
            args.task_config,
            args.input_jsonl,
            encoding_name=args.encoding,
        )
        token_counts = [row["token_count"] for row in rows]
        summary = {
            "mode": "task",
            "task_config": args.task_config,
            "input_jsonl": args.input_jsonl,
            "encoding": args.encoding,
            "item_count": len(rows),
            "min_token_count": min(token_counts) if token_counts else 0,
            "max_token_count": max(token_counts) if token_counts else 0,
            "total_token_count": sum(token_counts),
        }
        print(json.dumps(summary, ensure_ascii=False))
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return

    raise SystemExit("Use either --text, --file, or both --task-config and --input-jsonl.")


if __name__ == "__main__":
    main()
