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

from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.processing_order import ProcessingOrderStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage a track's document processing order.")
    parser.add_argument("track", choices=["dev", "working"])
    parser.add_argument(
        "action", choices=["init", "status", "swap", "rewind", "migrate-suffix"]
    )
    parser.add_argument("slots", nargs="*", type=int)
    parser.add_argument("--dataset-config", default="config/datasets/ja_cc_level2.toml")
    parser.add_argument("--new-source", type=Path)
    parser.add_argument("--frozen-through", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = PipelineWorkspace(PROJECT_ROOT)
    dataset = workspace._load_dataset_config(args.dataset_config)
    ledger = workspace._load_document_ledger(args.track)
    store = ProcessingOrderStore(PROJECT_ROOT, args.track)
    if args.action == "migrate-suffix":
        if args.slots:
            raise SystemExit("migrate-suffix does not accept processing slots")
        if args.new_source is None or args.frozen_through is None:
            raise SystemExit("migrate-suffix requires --new-source and --frozen-through")
        manifest = store.migrate_unprocessed_suffix(
            source_path=args.new_source,
            dataset_name=str(dataset["name"]),
            ledger_rows=ledger.get("documents", []),
            frozen_through_slot=args.frozen_through,
        )
    elif args.action == "rewind":
        if args.slots:
            raise SystemExit("rewind does not accept processing slots")
        if args.frozen_through is None:
            raise SystemExit("rewind requires --frozen-through")
        manifest = store.rewind_to_frozen_prefix(
            ledger_rows=ledger.get("documents", []),
            frozen_through_slot=args.frozen_through,
        )
    else:
        manifest = store.ensure(
            source_path=Path(dataset["source_path"]),
            dataset_name=str(dataset["name"]),
            ledger_rows=ledger.get("documents", []),
        )
    if args.action == "swap":
        if len(args.slots) != 2:
            raise SystemExit("swap requires exactly two processing slots")
        manifest = store.swap_slots(args.slots[0], args.slots[1])
    elif args.slots:
        raise SystemExit(f"{args.action} does not accept processing slots")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
