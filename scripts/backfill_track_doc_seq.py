#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from yomi_corpus.pipeline import PipelineWorkspace, normalize_track_name, now_iso


DOC_ID_PATTERN = re.compile(r"^(?P<dataset>.+):(?P<source_line>\d+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill stable per-track document sequence numbers into historical artifacts."
    )
    parser.add_argument("track", nargs="?", default="dev", help="Track name. Default: dev.")
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Also rewrite files below data/units/*/legacy/. Default skips legacy snapshots.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    track = normalize_track_name(args.track)
    workspace = PipelineWorkspace(PROJECT_ROOT)
    mapping = build_track_doc_seq_mapping(track)
    ledger = build_ledger(track, mapping)

    changed: list[Path] = []
    if not args.dry_run:
        workspace._write_document_ledger(track, ledger)
    changed.append(workspace.document_ledger_path(track))

    for path in candidate_jsonl_files(track=track, include_legacy=args.include_legacy):
        if backfill_jsonl(path, mapping, dry_run=args.dry_run):
            changed.append(path)

    for path in candidate_json_files(track=track):
        if backfill_json(path, mapping, dry_run=args.dry_run):
            changed.append(path)

    print(
        json.dumps(
            {
                "track": track,
                "doc_count": len(mapping),
                "changed_files": [str(path.relative_to(PROJECT_ROOT)) for path in changed],
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_track_doc_seq_mapping(track: str) -> dict[str, int]:
    doc_ids: set[str] = set()
    prefix = "dev_batch_" if track == "dev" else "batch_"
    for units_path in sorted((PROJECT_ROOT / "data" / "units").glob(f"{prefix}*/units.jsonl")):
        for row in load_jsonl(units_path):
            doc_id = str(row.get("doc_id") or "")
            if doc_id:
                doc_ids.add(doc_id)
    if not doc_ids:
        for pack_path in sorted((PROJECT_ROOT / "data" / "review_packs").glob("**/*.json")):
            payload = load_json_object(pack_path)
            if payload.get("track_name") != track:
                continue
            for doc in payload.get("documents", []) or []:
                if isinstance(doc, dict) and doc.get("doc_id"):
                    doc_ids.add(str(doc["doc_id"]))
    ordered = sorted(doc_ids, key=doc_sort_key)
    return {doc_id: index for index, doc_id in enumerate(ordered, start=1)}


def build_ledger(track: str, mapping: dict[str, int]) -> dict[str, Any]:
    created = now_iso()
    return {
        "schema_version": 1,
        "track_name": track,
        "created_at": created,
        "updated_at": created,
        "documents": [
            {
                "doc_id": doc_id,
                "track_doc_seq": seq,
                "dataset_name": dataset_name_from_doc_id(doc_id),
                "source_line_no": source_line_no_from_doc_id(doc_id),
                "created_at": created,
            }
            for doc_id, seq in sorted(mapping.items(), key=lambda item: item[1])
        ],
    }


def candidate_jsonl_files(*, track: str, include_legacy: bool) -> list[Path]:
    prefix = "dev_batch_" if track == "dev" else "batch_"
    paths: list[Path] = []
    for batch_dir in sorted((PROJECT_ROOT / "data" / "units").glob(f"{prefix}*")):
        if not batch_dir.is_dir():
            continue
        for path in sorted(batch_dir.rglob("*.jsonl")):
            if not include_legacy and "legacy" in path.relative_to(batch_dir).parts:
                continue
            paths.append(path)
    return paths


def candidate_json_files(*, track: str) -> list[Path]:
    paths: list[Path] = []
    for path in sorted((PROJECT_ROOT / "data" / "pipeline" / "document_states").glob("*.json")):
        if path.name.startswith("dev_batch_") == (track == "dev"):
            paths.append(path)
    for path in sorted((PROJECT_ROOT / "data" / "review_packs").glob("**/*.json")):
        payload = load_json_object(path)
        if payload.get("track_name") == track:
            paths.append(path)
    return paths


def backfill_jsonl(path: Path, mapping: dict[str, int], *, dry_run: bool) -> bool:
    rows = load_jsonl(path)
    changed = False
    for row in rows:
        changed = backfill_object(row, mapping) or changed
    if changed and not dry_run:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    return changed


def backfill_json(path: Path, mapping: dict[str, int], *, dry_run: bool) -> bool:
    payload = load_json_object(path)
    changed = backfill_object(payload, mapping)
    if changed and not dry_run:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def backfill_object(value: Any, mapping: dict[str, int]) -> bool:
    changed = False
    if isinstance(value, dict):
        doc_id = str(value.get("doc_id") or "")
        if doc_id in mapping and value.get("track_doc_seq") != mapping[doc_id]:
            value["track_doc_seq"] = mapping[doc_id]
            changed = True
        for child in value.values():
            changed = backfill_object(child, mapping) or changed
    elif isinstance(value, list):
        for child in value:
            changed = backfill_object(child, mapping) or changed
    return changed


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def doc_sort_key(doc_id: str) -> tuple[str, int, str]:
    match = DOC_ID_PATTERN.match(doc_id)
    if match:
        return (match.group("dataset"), int(match.group("source_line")), doc_id)
    return ("", 0, doc_id)


def dataset_name_from_doc_id(doc_id: str) -> str:
    match = DOC_ID_PATTERN.match(doc_id)
    return match.group("dataset") if match else ""


def source_line_no_from_doc_id(doc_id: str) -> int | None:
    match = DOC_ID_PATTERN.match(doc_id)
    return int(match.group("source_line")) if match else None


if __name__ == "__main__":
    main()
