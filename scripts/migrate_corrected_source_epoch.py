#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yomi_corpus.source_epoch_migration import (  # noqa: E402
    DEFAULT_DATASET_NAME,
    DEFAULT_EPOCH,
    apply_staged_migration,
    build_plan,
    stage_migration,
    validate_staged_migration,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan and apply the corrected-source dev epoch migration.")
    parser.add_argument("action", choices=("plan", "stage", "validate", "apply"))
    parser.add_argument(
        "--plan-dir",
        type=Path,
        default=ROOT / "data/migrations/source_epoch/home_tag_v1_corrected_20260902",
    )
    parser.add_argument(
        "--old-source",
        type=Path,
        default=Path("/panfs/panmt22/users/hmanabe/llm-jp-corpus-v4/data/migrations/home_tag_v1/full.membership_refreshed.jsonl.gz"),
    )
    parser.add_argument(
        "--new-source",
        type=Path,
        default=Path("/panfs/panmt22/users/hmanabe/llm-jp-corpus-v4/data/builds/home-tag-v1-corrected-20260902/ja_cc_level2.surface_word_kept.jsonl.gz"),
    )
    parser.add_argument(
        "--build-manifest",
        type=Path,
        default=Path("/panfs/panmt22/users/hmanabe/llm-jp-corpus-v4/data/builds/home-tag-v1-corrected-20260902/manifest.json"),
    )
    parser.add_argument("--epoch", default=DEFAULT_EPOCH)
    parser.add_argument("--new-dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--confirm", action="store_true", help="Required for apply")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.action == "plan":
        result = build_plan(
            root=ROOT,
            old_source=args.old_source,
            new_source=args.new_source,
            build_manifest=args.build_manifest,
            output_dir=args.plan_dir,
            epoch=args.epoch,
            new_dataset_name=args.new_dataset_name,
        )
    elif args.action == "stage":
        result = stage_migration(root=ROOT, plan_dir=args.plan_dir)
    elif args.action == "validate":
        result = validate_staged_migration(root=ROOT, plan_dir=args.plan_dir)
    else:
        if not args.confirm:
            raise SystemExit("apply requires --confirm")
        result = apply_staged_migration(root=ROOT, plan_dir=args.plan_dir)
    if args.action == "plan":
        result = {
            "migration_id": result["migration_id"],
            "status": result["status"],
            "counts": result["counts"],
            "action_counts": result["action_counts"],
            "plan": str(args.plan_dir / "plan.json"),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
