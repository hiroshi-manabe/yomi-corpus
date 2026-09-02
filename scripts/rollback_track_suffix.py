#!/usr/bin/env python3
"""Retire every materialized track document at or after a sequence boundary."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yomi_corpus.processing_order import ProcessingOrderStore

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track", choices=["dev", "working"])
    parser.add_argument("--from-track-doc-seq", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    boundary = args.from_track_doc_seq
    if boundary < 1:
        raise SystemExit("--from-track-doc-seq must be positive")
    ledger_path = ROOT / "data" / "pipeline" / "document_ledger" / f"{args.track}.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = [row for row in ledger.get("documents", []) if isinstance(row, dict)]
    retained = [row for row in rows if int(row.get("track_doc_seq") or 0) < boundary]
    retired = [row for row in rows if int(row.get("track_doc_seq") or 0) >= boundary]
    if not retired:
        raise SystemExit(f"No {args.track} documents exist at or after {boundary}.")
    if {int(row["track_doc_seq"]) for row in retained} != set(range(1, boundary)):
        raise SystemExit("The retained document ledger is not a complete frozen prefix.")

    retired_batches = sorted({str(row["first_batch_name"]) for row in retired})
    retained_batches = {
        str(row["first_batch_name"]) for row in retained if row.get("first_batch_name")
    }
    overlap = retained_batches.intersection(retired_batches)
    if overlap:
        raise SystemExit(f"Rollback boundary splits batches: {sorted(overlap)}")
    high_watermark = max(batch_serial(name) for name in retired_batches)
    paths = collect_paths(retired_batches, boundary)
    report = {
        "schema_version": 1,
        "operation": "rollback_track_suffix",
        "mode": "apply" if args.apply else "dry_run",
        "track_name": args.track,
        "from_track_doc_seq": boundary,
        "retained_document_count": len(retained),
        "retired_document_count": len(retired),
        "retired_batches": retired_batches,
        "batch_serial_high_watermark": high_watermark,
        "paths": [str(path.relative_to(ROOT)) for path in paths],
        "generated_at": now_iso(),
        "applied": False,
    }
    report_path = args.report or (
        ROOT / "data" / "state" / "migrations" / f"{args.track}_suffix_{boundary}_rollback.json"
    )
    if args.apply:
        backup_root = args.backup_root
        if backup_root is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_root = (
                ROOT / "data" / "state" / "migrations" / "backups" / f"{args.track}_{boundary}_{stamp}"
            )
        backup_root = backup_root.resolve()
        for path in paths:
            destination = backup_root / path.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))

        ledger["documents"] = retained
        ledger["updated_at"] = now_iso()
        write_json(ledger_path, ledger)
        ProcessingOrderStore(ROOT, args.track).rewind_to_frozen_prefix(
            ledger_rows=retained,
            frozen_through_slot=boundary - 1,
        )
        track_state_path = ROOT / "data" / "pipeline" / "tracks" / f"{args.track}.json"
        track_state = json.loads(track_state_path.read_text(encoding="utf-8"))
        track_state["current_batch_name"] = latest_batch(retained)
        track_state["updated_at"] = now_iso()
        write_json(track_state_path, track_state)

        # Keep allocation monotonic so old GitHub acknowledgments cannot match new packs.
        marker = ROOT / "data" / "units" / f"{args.track}_batch_{high_watermark:04d}"
        marker.mkdir(parents=True, exist_ok=True)
        write_json(
            marker / "obsolete_suffix_rebuild.json",
            {
                "schema_version": 1,
                "retired_at": now_iso(),
                "from_track_doc_seq": boundary,
                "backup_root": str(backup_root),
            },
        )
        report["backup_root"] = str(backup_root)
        report["applied"] = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def collect_paths(batch_names: list[str], boundary: int) -> list[Path]:
    paths: set[Path] = set()
    for batch_name in batch_names:
        candidates = [
            ROOT / "data" / "units" / batch_name,
            ROOT / "data" / "pipeline" / "batches" / f"{batch_name}.json",
            ROOT / "data" / "pipeline" / "document_states" / f"{batch_name}.json",
        ]
        candidates.extend((ROOT / "data" / "review_packs").glob(f"**/*{batch_name}*"))
        candidates.extend((ROOT / "data" / "review_submissions").glob(f"**/*{batch_name}*"))
        candidates.extend((ROOT / "data" / "llm" / "jobs").glob(f"{batch_name}_*"))
        candidates.extend((ROOT / "data" / "decoder_corpora").glob(f"**/{batch_name}.txt*"))
        paths.update(path for path in candidates if path.exists())
    archive = ROOT / "docs" / "review" / "archive"
    for path in archive.glob("**/docs_*_*.json"):
        match = re.search(r"docs_(\d+)_(\d+)\.json$", path.name)
        if match and int(match.group(2)) >= boundary:
            paths.add(path)
    return sorted(paths, key=lambda path: (len(path.parts), str(path)), reverse=True)


def batch_serial(batch_name: str) -> int:
    match = re.search(r"_batch_(\d+)$", batch_name)
    if not match:
        raise ValueError(f"Unsupported batch name: {batch_name}")
    return int(match.group(1))


def latest_batch(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    return str(max(rows, key=lambda row: int(row["track_doc_seq"]))["first_batch_name"])


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
