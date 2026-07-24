from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MIGRATION_SCHEMA_VERSION = 1


def migrate_terminal_exclusion(
    *,
    root: Path,
    track_name: str,
    track_doc_seq: int,
    reason_category: str,
    confirmation_submission_id: str,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    unit_root = root / "data" / "units"
    target_rows: list[dict[str, Any]] = []
    existing_tombstones: dict[str, dict[str, Any]] = {}
    source_files: dict[Path, list[dict[str, Any]]] = {}
    target_batches: set[str] = set()

    for batch_dir in sorted(unit_root.glob(f"{track_name}_batch_*")):
        for filename in ("units.yomi.final.jsonl", "units.yomi.skipped.jsonl"):
            path = batch_dir / filename
            if not path.exists():
                continue
            rows = load_jsonl(path)
            source_files[path] = rows
            matched = [row for row in rows if row_track_doc_seq(row) == track_doc_seq]
            if matched:
                target_rows.extend(matched)
                target_batches.add(batch_dir.name)
        excluded_path = batch_dir / "units.yomi.excluded.jsonl"
        if excluded_path.exists():
            rows = load_jsonl(excluded_path)
            source_files[excluded_path] = rows
            for row in rows:
                if row_track_doc_seq(row) == track_doc_seq:
                    existing_tombstones[str(row.get("unit_id") or "")] = row
                    target_batches.add(batch_dir.name)

    doc_ids = sorted(
        {
            str(row.get("doc_id") or "")
            for row in [*target_rows, *existing_tombstones.values()]
            if row.get("doc_id")
        }
    )
    anomalies: list[str] = []
    if len(doc_ids) > 1:
        anomalies.append(
            f"track document {track_doc_seq} maps to multiple document IDs: {doc_ids}"
        )
    if len(target_batches) > 1:
        anomalies.append(
            f"track document {track_doc_seq} is present in multiple batches: {sorted(target_batches)}"
        )
    if not target_rows and not existing_tombstones:
        anomalies.append(f"track document {track_doc_seq} was not found")

    doc_id = doc_ids[0] if len(doc_ids) == 1 else ""
    tombstones = dict(existing_tombstones)
    for row in target_rows:
        unit_id = str(row.get("unit_id") or "")
        if not unit_id:
            anomalies.append("target row has no unit_id")
            continue
        tombstones[unit_id] = terminal_exclusion_tombstone(
            row,
            reason_category=reason_category,
            confirmation_submission_id=confirmation_submission_id,
        )

    replacements: dict[Path, str] = {}
    removed_by_path: dict[str, int] = {}
    for path, rows in source_files.items():
        if path.name == "units.yomi.excluded.jsonl":
            continue
        retained = [row for row in rows if row_track_doc_seq(row) != track_doc_seq]
        removed = len(rows) - len(retained)
        if removed:
            replacements[path] = encode_jsonl(retained)
            removed_by_path[str(path.relative_to(root))] = removed

    if target_batches:
        batch_name = next(iter(target_batches))
        excluded_path = unit_root / batch_name / "units.yomi.excluded.jsonl"
        retained = [
            row
            for row in source_files.get(excluded_path, load_jsonl_if_exists(excluded_path))
            if row_track_doc_seq(row) != track_doc_seq
        ]
        ordered_tombstones = sorted(
            tombstones.values(),
            key=lambda row: (int(row.get("unit_seq") or 0), str(row.get("unit_id") or "")),
        )
        replacements[excluded_path] = encode_jsonl([*retained, *ordered_tombstones])

    derived_reports: list[dict[str, Any]] = []
    if doc_id:
        collect_review_pack_replacements(
            root=root,
            doc_id=doc_id,
            replacements=replacements,
            reports=derived_reports,
        )
        collect_eval_replacements(
            root=root,
            doc_id=doc_id,
            replacements=replacements,
            reports=derived_reports,
        )

    changed_paths = [
        path
        for path, content in replacements.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    generated_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": f"terminal_exclusion_{track_name}_{track_doc_seq}",
        "mode": "apply" if apply else "dry_run",
        "generated_at": generated_at,
        "track_name": track_name,
        "track_doc_seq": track_doc_seq,
        "doc_id": doc_id,
        "reason_category": reason_category,
        "confirmation_submission_id": confirmation_submission_id,
        "source_unit_count": len(target_rows),
        "existing_tombstone_count": len(existing_tombstones),
        "tombstone_count": len(tombstones),
        "removed_by_path": removed_by_path,
        "derived": derived_reports,
        "changed_paths": [str(path.relative_to(root)) for path in changed_paths],
        "anomalies": anomalies,
        "applied": False,
        "decoder_models": {
            "action": "supersede models built before this exclusion; do not rewrite immutable models",
            "exclusion_recorded_at": generated_at,
        },
    }

    if apply and not anomalies:
        if backup_root is None:
            raise ValueError("backup_root is required in apply mode")
        apply_replacements(root=root, replacements=replacements, backup_root=backup_root)
        report["applied"] = True
        report["backup_root"] = str(backup_root)

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def terminal_exclusion_tombstone(
    row: dict[str, Any],
    *,
    reason_category: str,
    confirmation_submission_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "excluded": True,
        "tombstone_label": "Removed",
        "doc_id": str(row.get("doc_id") or ""),
        "track_doc_seq": row.get("track_doc_seq"),
        "unit_id": str(row.get("unit_id") or ""),
        "unit_seq": row.get("unit_seq"),
        "reason_category": reason_category,
        "confirmation_submission_id": confirmation_submission_id,
        "confirmed_at_epoch": 0,
    }


def collect_review_pack_replacements(
    *,
    root: Path,
    doc_id: str,
    replacements: dict[Path, str],
    reports: list[dict[str, Any]],
) -> None:
    for path in sorted((root / "data" / "review_packs").rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        retained = [item for item in items if not contains_document_identity(item, doc_id)]
        removed = len(items) - len(retained)
        if not removed:
            continue
        payload["items"] = retained
        payload["item_count"] = len(retained)
        replacements[path] = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        reports.append(
            {"kind": "review_pack", "path": str(path.relative_to(root)), "removed": removed}
        )


def collect_eval_replacements(
    *,
    root: Path,
    doc_id: str,
    replacements: dict[Path, str],
    reports: list[dict[str, Any]],
) -> None:
    for path in sorted((root / "data" / "evals").rglob("*.jsonl")):
        rows = load_jsonl(path)
        retained = [row for row in rows if not contains_document_identity(row, doc_id)]
        removed = len(rows) - len(retained)
        if not removed:
            continue
        replacements[path] = encode_jsonl(retained)
        reports.append(
            {"kind": "evaluation", "path": str(path.relative_to(root)), "removed": removed}
        )


def contains_document_identity(value: Any, doc_id: str) -> bool:
    if isinstance(value, dict):
        if str(value.get("doc_id") or "") == doc_id:
            return True
        for key in ("unit_id", "item_id", "source_unit_id"):
            candidate = str(value.get(key) or "")
            if candidate == doc_id or candidate.startswith(f"{doc_id}:"):
                return True
        return any(contains_document_identity(item, doc_id) for item in value.values())
    if isinstance(value, list):
        return any(contains_document_identity(item, doc_id) for item in value)
    return False


def apply_replacements(
    *,
    root: Path,
    replacements: dict[Path, str],
    backup_root: Path,
) -> None:
    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path | None] = {}
    try:
        for path, content in replacements.items():
            if path.exists() and path.read_text(encoding="utf-8") == content:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_name(f".{path.name}.exclude.tmp")
            temp_path.write_text(content, encoding="utf-8")
            staged.append((path, temp_path))
        for path, _temp_path in staged:
            if path.exists():
                backup_path = backup_root / path.relative_to(root)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
                backups[path] = backup_path
            else:
                backups[path] = None
        for path, temp_path in staged:
            temp_path.replace(path)
    except Exception:
        for path, backup_path in backups.items():
            if backup_path is None:
                path.unlink(missing_ok=True)
            elif backup_path.exists():
                shutil.copy2(backup_path, path)
        raise
    finally:
        for _path, temp_path in staged:
            temp_path.unlink(missing_ok=True)


def row_track_doc_seq(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("track_doc_seq"))
    except (TypeError, ValueError):
        return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as src:
        for line_number, line in enumerate(src, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(row)
    return rows


def load_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    return load_jsonl(path) if path.exists() else []


def encode_jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
