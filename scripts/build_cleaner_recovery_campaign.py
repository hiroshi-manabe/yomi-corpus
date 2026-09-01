#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from yomi_corpus.recovery_documents import (
    DEFAULT_MAX_UNITS,
    DEFAULT_MIN_CHARS,
    DEFAULT_TARGET_CHARS,
    build_recovery_units,
    iter_jsonl,
    load_json,
    pack_recovery_units,
    source_record_id,
    RestoredChunk,
    write_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a report-first cleaner recovery campaign for finalized documents."
    )
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--old-source", type=Path, required=True)
    parser.add_argument("--new-source", type=Path, required=True)
    parser.add_argument(
        "--document-ledger",
        type=Path,
        default=PROJECT_ROOT / "data/pipeline/document_ledger/dev.json",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=PROJECT_ROOT / "docs/review/archive/dev",
        help="Only ledger documents with a finalized archive shard are included.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-chars", type=int, default=DEFAULT_TARGET_CHARS)
    parser.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS)
    parser.add_argument("--max-units", type=int, default=DEFAULT_MAX_UNITS)
    parser.add_argument(
        "--alignment-overrides",
        type=Path,
        help="Optional audited JSONL insertion anchors for otherwise ambiguous records.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledger = load_json(args.document_ledger)
    finalized = _finalized_track_sequences(args.archive_dir)
    destinations = {
        int(row["source_line_no"]): row
        for row in ledger.get("documents", [])
        if isinstance(row, dict) and int(row.get("track_doc_seq") or 0) in finalized
    }
    old_records = _records_at_lines(args.old_source, set(destinations))
    source_id_to_destination: dict[str, dict[str, Any]] = {}
    old_by_source_id: dict[str, dict[str, Any]] = {}
    for source_line_no, record in old_records.items():
        stable_id = source_record_id(record)
        if stable_id in source_id_to_destination:
            raise ValueError(f"Duplicate stable source ID in processed prefix: {stable_id}")
        source_id_to_destination[stable_id] = destinations[source_line_no]
        old_by_source_id[stable_id] = record

    new_by_source_id: dict[str, dict[str, Any]] = {}
    wanted = set(source_id_to_destination)
    for record in iter_jsonl(args.new_source):
        stable_id = source_record_id(record)
        if stable_id not in wanted:
            continue
        if stable_id in new_by_source_id:
            raise ValueError(f"Duplicate stable source ID in regenerated source: {stable_id}")
        new_by_source_id[stable_id] = record
        if len(new_by_source_id) == len(wanted):
            break

    overrides = _load_alignment_overrides(args.alignment_overrides)
    unknown_overrides = sorted(overrides.keys() - wanted)
    if unknown_overrides:
        raise ValueError(f"Alignment overrides target unknown source IDs: {unknown_overrides[:10]}")

    units: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unchanged = 0
    for stable_id in sorted(wanted, key=lambda item: int(source_id_to_destination[item]["track_doc_seq"])):
        old_record = old_by_source_id[stable_id]
        new_record = new_by_source_id.get(stable_id)
        destination = source_id_to_destination[stable_id]
        if new_record is None:
            conflicts.append(_conflict(args.campaign_id, stable_id, destination, "missing_regenerated_record"))
            continue
        if old_record.get("text") == new_record.get("text"):
            unchanged += 1
            continue
        try:
            units.extend(
                build_recovery_units(
                    campaign_id=args.campaign_id,
                    old_record=old_record,
                    new_record=new_record,
                    destination=destination,
                    restored_chunks=overrides.get(stable_id),
                )
            )
        except ValueError as exc:
            conflicts.append(_conflict(args.campaign_id, stable_id, destination, str(exc)))

    documents = pack_recovery_units(
        units,
        campaign_id=args.campaign_id,
        target_chars=args.target_chars,
        min_chars=args.min_chars,
        max_units=args.max_units,
    )
    output_dir = args.output_dir
    write_jsonl(output_dir / "recovery_units.jsonl", units)
    write_jsonl(output_dir / "recovery_documents.jsonl", documents)
    write_jsonl(output_dir / "conflicts.jsonl", conflicts)
    manifest = {
        "schema_version": 1,
        "campaign_id": args.campaign_id,
        "old_source": str(args.old_source.resolve()),
        "new_source": str(args.new_source.resolve()),
        "document_ledger": str(args.document_ledger.resolve()),
        "archive_dir": str(args.archive_dir.resolve()),
        "alignment_overrides": (
            str(args.alignment_overrides.resolve()) if args.alignment_overrides else None
        ),
        "packing": {
            "target_chars": args.target_chars,
            "min_chars": args.min_chars,
            "max_units": args.max_units,
        },
        "counts": {
            "finalized_documents_considered": len(destinations),
            "unchanged_documents": unchanged,
            "destination_documents_with_recovery": len(
                {row["destination_doc_id"] for row in units}
            ),
            "recovery_units": len(units),
            "recovery_documents": len(documents),
            "conflicts": len(conflicts),
        },
        "recovery_document_distribution": _distribution(documents),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "campaign.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 2 if conflicts else 0


def _records_at_lines(path: Path, wanted: set[int]) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    if not wanted:
        return records
    for line_number, record in enumerate(iter_jsonl(path), start=1):
        if line_number in wanted:
            records[line_number] = record
        if len(records) == len(wanted):
            break
    missing = sorted(wanted - records.keys())
    if missing:
        raise EOFError(f"Old source lines are missing: {missing[:20]}")
    return records


def _finalized_track_sequences(archive_dir: Path) -> set[int]:
    sequences: set[int] = set()
    for path in archive_dir.glob("docs_*_*.json"):
        parts = path.stem.split("_")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            sequences.update(range(int(parts[1]), int(parts[2]) + 1))
    return sequences


def _conflict(
    campaign_id: str,
    stable_id: str,
    destination: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "source_record_id": stable_id,
        "destination_doc_id": destination["doc_id"],
        "destination_track_doc_seq": destination["track_doc_seq"],
        "state": "conflict",
        "reason": reason,
    }


def _distribution(documents: list[dict[str, Any]]) -> dict[str, float | int]:
    if not documents:
        return {"character_mean": 0, "character_median": 0, "unit_mean": 0, "unit_median": 0}
    characters = [int(row["character_count"]) for row in documents]
    units = [int(row["unit_count"]) for row in documents]
    return {
        "character_mean": round(statistics.fmean(characters), 1),
        "character_median": round(statistics.median(characters), 1),
        "unit_mean": round(statistics.fmean(units), 1),
        "unit_median": round(statistics.median(units), 1),
    }


def _load_alignment_overrides(path: Path | None) -> dict[str, list[RestoredChunk]]:
    if path is None:
        return {}
    overrides: dict[str, list[RestoredChunk]] = {}
    for row in iter_jsonl(path):
        source_id = str(row.get("source_record_id") or "")
        if not source_id or source_id in overrides:
            raise ValueError(f"Invalid or duplicate recovery override source ID: {source_id!r}")
        insertions = row.get("insertions")
        if not isinstance(insertions, list):
            raise ValueError(f"Recovery override {source_id} does not define insertions.")
        overrides[source_id] = [
            RestoredChunk(
                old_start=int(insertion["old_start"]),
                new_start=-1,
                new_end=-1,
                text=str(insertion["text"]),
            )
            for insertion in insertions
            if isinstance(insertion, dict)
        ]
        if len(overrides[source_id]) != len(insertions):
            raise ValueError(f"Recovery override {source_id} contains a non-object insertion.")
    return overrides


if __name__ == "__main__":
    raise SystemExit(main())
