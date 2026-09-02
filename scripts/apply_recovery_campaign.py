#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.recovery_application import apply_recovery_campaign


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate or apply finalized recovery units to original documents."
    )
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--batch", required=True, help="Finalized recovery batch name")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write validated changes; without this option the command is a dry run.",
    )
    args = parser.parse_args()
    summary = apply_recovery_campaign(
        root=PROJECT_ROOT,
        campaign_dir=args.campaign_dir,
        recovery_batch_name=args.batch,
        apply=args.apply,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
