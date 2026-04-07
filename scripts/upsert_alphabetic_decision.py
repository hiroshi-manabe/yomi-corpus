#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_state import AlphabeticDecision, upsert_alphabetic_decision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upsert one global alphabetic token decision.")
    parser.add_argument("token_key", help="Token key used by the alphabetic pipeline.")
    parser.add_argument(
        "status",
        choices=["whitelist", "blacklist", "needs_context", "unknown"],
        help="Decision status to store.",
    )
    parser.add_argument(
        "--strict-case",
        action="store_true",
        help="Mark this token key as strict-case.",
    )
    parser.add_argument(
        "--source",
        default="manual",
        help="Decision source label, e.g. manual, llm, promoted.",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Optional note stored with the decision.",
    )
    parser.add_argument(
        "--decisions-path",
        default="data/state/alphabetic/token_decisions.jsonl",
        help="Global decision registry path relative to repo root.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    upsert_alphabetic_decision(
        PROJECT_ROOT / args.decisions_path,
        AlphabeticDecision(
            token_key=args.token_key,
            strict_case=args.strict_case,
            status=args.status,
            source=args.source,
            note=args.note,
        ),
    )


if __name__ == "__main__":
    main()
