#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from yomi_corpus.yomi.source_surface_migration import migrate_source_surfaces


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore exact source surfaces in yomi artifacts."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace validated files after backing them up.",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = args.report or (
        root
        / "data"
        / "state"
        / "migrations"
        / f"source_surface_preservation_v1_{stamp}.json"
    )
    backup_root = args.backup_root
    if args.apply and backup_root is None:
        backup_root = root / "data" / "state" / "migrations" / "backups" / stamp
    result = migrate_source_surfaces(
        root=root,
        apply=args.apply,
        report_json=report,
        backup_root=backup_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["anomaly_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
