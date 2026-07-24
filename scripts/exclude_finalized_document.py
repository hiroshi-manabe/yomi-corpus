#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from yomi_corpus.yomi.exclusion_migration import migrate_terminal_exclusion


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Terminally exclude one finalized track document and create content-free tombstones."
    )
    parser.add_argument("track_doc_seq", type=int)
    parser.add_argument("--track", default="dev")
    parser.add_argument("--reason-category", default="sensitive_content")
    parser.add_argument("--confirmation-submission-id", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    migration_id = f"terminal_exclusion_{args.track}_{args.track_doc_seq}"
    report = args.report or root / "data" / "state" / "migrations" / migration_id / "manifest.json"
    backup_root = args.backup_root
    if args.apply and backup_root is None:
        backup_root = root / "data" / "state" / "migrations" / migration_id / "backups" / stamp
    result = migrate_terminal_exclusion(
        root=root,
        track_name=args.track,
        track_doc_seq=args.track_doc_seq,
        reason_category=args.reason_category,
        confirmation_submission_id=args.confirmation_submission_id,
        apply=args.apply,
        report_json=report,
        backup_root=backup_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["anomalies"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
