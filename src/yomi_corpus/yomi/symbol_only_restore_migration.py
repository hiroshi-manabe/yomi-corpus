from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.final_review import (
    canonicalize_finalized_unit_yomi,
    current_yomi_tokens_for_correction,
)
MIGRATION_ID = "symbol_only_skip_restore_v1"


def is_symbol_only_unit(unit: dict[str, Any]) -> bool:
    text = str(unit.get("text", ""))
    return bool(text.strip()) and not any(char.isalnum() for char in text)


def migrate_symbol_only_skips(
    *,
    root: Path,
    track_name: str,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    generated_at = datetime.now(timezone.utc)
    generated_at_epoch = int(generated_at.timestamp())
    replacements: dict[Path, str] = {}
    batches: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []

    for batch_dir in sorted((root / "data" / "units").glob(f"{track_name}_batch_*")):
        skipped_path = batch_dir / "units.yomi.skipped.jsonl"
        if not skipped_path.exists():
            continue
        final_path = batch_dir / "units.yomi.final.jsonl"
        skipped_rows = load_jsonl(skipped_path)
        final_rows = load_jsonl(final_path)
        final_ids = {str(row.get("unit_id") or "") for row in final_rows}
        restored_rows: list[dict[str, Any]] = []
        retained_skipped_rows: list[dict[str, Any]] = []
        batch_anomalies: list[dict[str, Any]] = []

        for row in skipped_rows:
            if not is_symbol_only_unit(row):
                retained_skipped_rows.append(row)
                continue
            unit_id = str(row.get("unit_id") or "")
            if not unit_id:
                batch_anomalies.append({"reason": "missing_unit_id", "text": row.get("text")})
                retained_skipped_rows.append(row)
                continue
            if unit_id in final_ids:
                batch_anomalies.append(
                    {"reason": "duplicate_finalized_unit_id", "unit_id": unit_id}
                )
                retained_skipped_rows.append(row)
                continue
            restored = copy.deepcopy(row)
            try:
                baseline_tokens = current_yomi_tokens_for_correction(restored)
                if not baseline_tokens:
                    raise ValueError("preserved hybrid yomi has no tokens")
                canonicalize_finalized_unit_yomi(
                    restored,
                    grandfathered_tokens=baseline_tokens,
                )
            except (TypeError, ValueError) as exc:
                batch_anomalies.append(
                    {
                        "reason": "invalid_preserved_yomi",
                        "unit_id": unit_id,
                        "error": str(exc),
                    }
                )
                retained_skipped_rows.append(row)
                continue
            record_symbol_only_restoration(
                restored,
                generated_at=generated_at.isoformat(),
                generated_at_epoch=generated_at_epoch,
            )
            restored_rows.append(restored)
            final_ids.add(unit_id)

        if restored_rows:
            combined_final_rows = sorted(
                [*final_rows, *restored_rows],
                key=unit_source_order,
            )
            replacements[final_path] = encode_jsonl(combined_final_rows)
            replacements[skipped_path] = encode_jsonl(retained_skipped_rows)
        anomalies.extend(
            {"batch_name": batch_dir.name, **row} for row in batch_anomalies
        )
        if restored_rows or batch_anomalies:
            batches.append(
                {
                    "batch_name": batch_dir.name,
                    "candidate_count": len(restored_rows) + len(batch_anomalies),
                    "restorable_count": len(restored_rows),
                    "anomaly_count": len(batch_anomalies),
                    "units": [
                        {
                            "track_doc_seq": row.get("track_doc_seq"),
                            "doc_id": str(row.get("doc_id") or ""),
                            "unit_id": str(row.get("unit_id") or ""),
                            "unit_seq": row.get("unit_seq"),
                            "text": str(row.get("text") or ""),
                        }
                        for row in restored_rows
                    ],
                }
            )

    changed_paths = [
        path
        for path, content in replacements.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "migration_id": MIGRATION_ID,
        "mode": "apply" if apply else "dry_run",
        "generated_at": generated_at.isoformat(),
        "track_name": track_name,
        "batch_count": len(batches),
        "candidate_count": sum(int(row["candidate_count"]) for row in batches),
        "restorable_count": sum(int(row["restorable_count"]) for row in batches),
        "anomaly_count": len(anomalies),
        "batches": batches,
        "anomalies": anomalies,
        "changed_paths": [str(path.relative_to(root)) for path in changed_paths],
        "applied": False,
        "decoder_models": {
            "action": "include restored units in the next scheduled corpus/model rebuild"
        },
    }

    if apply and not anomalies:
        if backup_root is None:
            raise ValueError("backup_root is required in apply mode")
        apply_replacements(
            root=root,
            replacements={path: replacements[path] for path in changed_paths},
            backup_root=backup_root,
        )
        report["applied"] = True
        report["backup_root"] = str(backup_root)

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def record_symbol_only_restoration(
    unit: dict[str, Any],
    *,
    generated_at: str,
    generated_at_epoch: int,
) -> None:
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    review = human_review.setdefault("yomi_final", {})
    previous_submission_id = str(review.get("submission_id") or "")
    review.update(
        {
            "reviewed": True,
            "disposition": "Keep",
            "skip": False,
            "restored": True,
            "restoration_submission_id": MIGRATION_ID,
            "restoration_source": "deterministic_symbol_only_keep",
            "restored_at": generated_at,
        }
    )
    history = human_review.setdefault("skip_history", [])
    if not any(
        isinstance(event, dict)
        and event.get("event") == "restored"
        and event.get("submission_id") == MIGRATION_ID
        for event in history
    ):
        history.append(
            {
                "event": "restored",
                "submission_id": MIGRATION_ID,
                "review_stage": "migration",
                "source": "deterministic_symbol_only_keep",
                "previous_submission_id": previous_submission_id,
                "generated_at_epoch": generated_at_epoch,
            }
        )
    unit.setdefault("analysis", {}).setdefault("pipeline", {})[MIGRATION_ID] = {
        "status": "restored",
        "generated_at": generated_at,
    }


def unit_source_order(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(row.get("track_doc_seq") or 0),
        int(row.get("unit_seq") or 0),
        str(row.get("unit_id") or ""),
    )


def apply_replacements(
    *,
    root: Path,
    replacements: dict[Path, str],
    backup_root: Path,
) -> None:
    staged: list[tuple[Path, Path]] = []
    for path, content in replacements.items():
        relative = path.relative_to(root)
        backup_path = backup_root / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not backup_path.exists():
            shutil.copy2(path, backup_path)
        temporary = path.with_suffix(path.suffix + f".{MIGRATION_ID}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(content, encoding="utf-8")
        staged.append((path, temporary))
    for path, temporary in staged:
        temporary.replace(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def encode_jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
