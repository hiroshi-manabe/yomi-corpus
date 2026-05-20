#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from yomi_corpus.llm.rendering import restore_source_whitespace_tokens


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh yomi eval JSONL rendered fields by restoring explicit source "
            "whitespace tokens without changing existing non-whitespace readings."
        )
    )
    parser.add_argument("paths", type=Path, nargs="+", help="Eval JSONL file(s) to refresh in place.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write files; exit non-zero if any file would change or any row fails alignment.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    any_changed = False
    any_warnings = False
    for path in args.paths:
        rows: list[dict[str, object]] = []
        changed = 0
        warnings: list[str] = []
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                text = row.get("text")
                rendered = row.get("rendered")
                if isinstance(text, str) and isinstance(rendered, str):
                    refreshed, row_warnings = restore_source_whitespace_tokens(text, rendered)
                    if row_warnings:
                        warnings.extend(f"{path}:{line_no}: {warning}" for warning in row_warnings)
                    elif refreshed != rendered:
                        row["rendered"] = refreshed
                        changed += 1
                rows.append(row)

        any_changed = any_changed or changed > 0
        any_warnings = any_warnings or bool(warnings)
        for warning in warnings:
            print(warning, file=sys.stderr)
        print(f"{path}: refreshed {changed} row(s); warnings {len(warnings)}", file=sys.stderr)

        if changed and not args.check:
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    if args.check and (any_changed or any_warnings):
        raise SystemExit(1)
    if any_warnings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
