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

from yomi_corpus.yomi.scope_removal_migration import migrate_scope_triage_removal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove retired scope-classifier state from active batches."
    )
    parser.add_argument("--track", choices=("dev", "working"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag, only print a dry-run report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = migrate_scope_triage_removal(
        root=PROJECT_ROOT,
        track_name=args.track,
        dry_run=not args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
