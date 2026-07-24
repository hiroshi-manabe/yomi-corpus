#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from yomi_corpus.yomi.skipped_backfill import backfill_skipped_hybrid_yomi


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill hybrid yomi and archive historical scope-triage and "
            "human-confirmed skips."
        )
    )
    parser.add_argument("--track", default="dev", choices=["dev", "working"])
    parser.add_argument("--batch", action="append", dest="batches")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write artifacts. Without this flag, generate and report a dry run.",
    )
    args = parser.parse_args()
    summary = backfill_skipped_hybrid_yomi(
        root=PROJECT_ROOT,
        track_name=args.track,
        apply=args.apply,
        batch_names=set(args.batches) if args.batches else None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["stage_complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
