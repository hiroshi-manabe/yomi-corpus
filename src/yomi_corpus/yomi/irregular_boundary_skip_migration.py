from __future__ import annotations

import json
import shutil
from pathlib import Path
from time import time
from typing import Any

from yomi_corpus.yomi.final_review import (
    FINALIZED_CORRECTION_STAGE,
    FINALIZED_CORRECTION_SUBMISSION_TYPE,
    apply_finalized_correction_patches_to_batch,
    current_yomi_tokens_for_correction,
    irregular_sentence_boundary_skip_reason,
    load_jsonl,
    store_review_submission,
)


MIGRATION_ID = "irregular_sentence_boundary_skip_v1"


def migrate_irregular_sentence_boundary_skips(
    *,
    root: Path,
    track_name: str,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    candidates_by_batch = collect_irregular_sentence_boundary_candidates(root)
    generated_at_epoch = int(time())
    submissions = {
        batch_name: build_skip_submission(
            batch_name=batch_name,
            track_name=track_name,
            rows=rows,
            generated_at_epoch=generated_at_epoch,
        )
        for batch_name, rows in candidates_by_batch.items()
    }
    apply_summaries: list[dict[str, Any]] = []
    submission_paths: list[str] = []
    backup_paths: list[str] = []

    if apply:
        if backup_root is None:
            raise ValueError("backup_root is required when applying the migration")
        submission_store = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
        for batch_name, submission in submissions.items():
            backup_paths.extend(
                backup_finalized_artifacts(
                    root=root,
                    batch_name=batch_name,
                    backup_root=backup_root,
                )
            )
            submission_path = store_review_submission(
                submission,
                submission_store_dir=submission_store,
            )
            submission_paths.append(str(submission_path))
            apply_summaries.append(
                apply_finalized_correction_patches_to_batch(
                    root=root,
                    batch_name=batch_name,
                    patches=[(submission, patch) for patch in submission["units"]],
                )
            )

    skipped_count = sum(int(summary.get("skipped_count") or 0) for summary in apply_summaries)
    result = {
        "migration_id": MIGRATION_ID,
        "track_name": track_name,
        "apply": apply,
        "candidate_count": sum(len(rows) for rows in candidates_by_batch.values()),
        "batch_count": len(candidates_by_batch),
        "candidates": [
            {
                "batch_name": batch_name,
                "unit_id": str(row.get("unit_id") or ""),
                "doc_id": str(row.get("doc_id") or ""),
                "text": str(row.get("text") or ""),
                "reason": candidate_skip_reason(row),
            }
            for batch_name, rows in candidates_by_batch.items()
            for row in rows
        ],
        "submission_paths": submission_paths,
        "backup_paths": backup_paths,
        "apply_summaries": apply_summaries,
        "applied_count": sum(int(summary.get("applied_count") or 0) for summary in apply_summaries),
        "skipped_count": skipped_count,
        "anomaly_count": skipped_count,
        "report_json": str(report_json),
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def collect_irregular_sentence_boundary_candidates(
    root: Path,
) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for final_jsonl in sorted((root / "data" / "units").glob("*/units.yomi.final.jsonl")):
        rows = [row for row in load_jsonl(final_jsonl) if candidate_skip_reason(row)]
        if rows:
            candidates[final_jsonl.parent.name] = rows
    return candidates


def candidate_skip_reason(row: dict[str, Any]) -> str:
    yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    sudachi = yomi.get("sudachi", {}) if isinstance(yomi, dict) else {}
    tokens = sudachi.get("tokens", []) if isinstance(sudachi, dict) else []
    return irregular_sentence_boundary_skip_reason(
        str(row.get("text") or ""),
        sudachi_tokens=tokens if isinstance(tokens, list) else [],
    )


def build_skip_submission(
    *,
    batch_name: str,
    track_name: str,
    rows: list[dict[str, Any]],
    generated_at_epoch: int,
) -> dict[str, Any]:
    return {
        "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
        "schema_version": 2,
        "submission_id": f"finalized_correction__{MIGRATION_ID}__{batch_name}",
        "track_name": track_name,
        "review_stage": FINALIZED_CORRECTION_STAGE,
        "batch_name": batch_name,
        "generated_at_epoch": generated_at_epoch,
        "migration_id": MIGRATION_ID,
        "units": [
            {
                "unit_id": str(row.get("unit_id") or ""),
                "unit_seq": row.get("unit_seq"),
                "text": str(row.get("text") or ""),
                "disposition": "Skip",
                "skip": True,
                "reason": candidate_skip_reason(row),
                "original_yomi_tokens": current_yomi_tokens_for_correction(row),
                "proposed_yomi_tokens": current_yomi_tokens_for_correction(row),
            }
            for row in rows
        ],
    }


def backup_finalized_artifacts(
    *,
    root: Path,
    batch_name: str,
    backup_root: Path,
) -> list[str]:
    paths: list[str] = []
    source_dir = root / "data" / "units" / batch_name
    destination_dir = backup_root / batch_name
    destination_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "units.yomi.final.jsonl",
        "units.yomi.skipped.jsonl",
        "units.yomi.excluded.jsonl",
    ):
        source = source_dir / filename
        if not source.exists():
            continue
        destination = destination_dir / filename
        shutil.copy2(source, destination)
        paths.append(str(destination))
    return paths
