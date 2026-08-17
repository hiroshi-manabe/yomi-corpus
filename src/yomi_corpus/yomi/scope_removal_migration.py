from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import (
    RETIRED_LLM_POLICY_TASKS,
    STAGE_FINAL_REVIEW_PREPARED,
    STAGE_PREPARED,
    STAGE_YOMI_FINALIZED,
    PipelineWorkspace,
    normalize_stage_name,
)


MIGRATION_NAME = "scope_triage_removal"
RETIRED_STAGE_NAMES = frozenset(
    {
        "alphabetic_analyzed",
        "alphabetic_reported",
        "alphabetic_judged",
        "alphabetic_llm_judged",
        "alphabetic_promotion_candidates",
        "scope_triage_queued",
        "scope_triage_completed",
        "scope_triage_llm_completed",
    }
)
RETIRED_ARTIFACT_PREFIXES = (
    "alphabetic_",
    "scope_triage_",
    "units_alphabetic_",
    "units_scope_triaged_",
)


def migrate_scope_triage_removal(
    *,
    root: Path,
    track_name: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    workspace = PipelineWorkspace(root)
    state_root = workspace.batches_root()
    rows: list[dict[str, Any]] = []
    changed_batches = 0

    for state_path in sorted(state_root.glob("*.json")):
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if track_name and str(raw.get("track_name") or "") != track_name:
            continue
        raw_stage = str(raw.get("current_stage") or STAGE_PREPARED)
        normalized_stage = normalize_stage_name(raw_stage)
        if normalized_stage == STAGE_YOMI_FINALIZED:
            continue

        batch_name = str(raw.get("batch_name") or state_path.stem)
        manifest_path = workspace.manifest_path(batch_name)
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else None
        )
        cleaned = clean_batch_payload(raw, raw_stage=raw_stage)
        cleaned_manifest = clean_manifest_payload(manifest) if manifest is not None else None
        state_changed = cleaned != raw
        manifest_changed = cleaned_manifest is not None and cleaned_manifest != manifest
        review_pack_needs_regeneration = (
            normalized_stage == STAGE_FINAL_REVIEW_PREPARED
            and needs_review_pack_regeneration(workspace, batch_name)
        )
        changed = state_changed or manifest_changed or review_pack_needs_regeneration
        if changed:
            changed_batches += 1

        row = {
            "batch_name": batch_name,
            "track_name": str(raw.get("track_name") or ""),
            "previous_stage": raw_stage,
            "current_stage": str(cleaned.get("current_stage") or ""),
            "returned_to_prepared": raw_stage in RETIRED_STAGE_NAMES,
            "state_changed": state_changed,
            "manifest_changed": manifest_changed,
            "removed_policy_tasks": retired_policy_tasks(raw),
            "removed_artifact_keys": retired_artifact_keys(raw.get("artifacts")),
            "remote_job_paths": existing_remote_job_paths(root, batch_name),
            "review_pack_needs_regeneration": review_pack_needs_regeneration,
            "review_pack_regenerated": False,
            "identity": {
                key: raw.get(key)
                for key in ("batch_name", "track_name", "docs_written", "units_written")
            },
        }
        rows.append(row)

        if dry_run or not changed:
            continue
        backup_migration_inputs(
            root=root,
            batch_name=batch_name,
            state_path=state_path,
            manifest_path=manifest_path if manifest_path.exists() else None,
        )
        if review_pack_needs_regeneration:
            pack_id = str(
                cleaned.get("artifacts", {}).get("final_review_pack_id")
                or f"yomi_final_{batch_name}_v1"
            )
            workspace._refresh_final_review_pack(  # noqa: SLF001
                batch_name=batch_name,
                pack_id=pack_id,
            )
            row["review_pack_regenerated"] = True
        write_json_atomic(state_path, cleaned)
        if cleaned_manifest is not None and manifest_changed:
            write_json_atomic(manifest_path, cleaned_manifest)

    report = {
        "schema_version": 1,
        "migration": MIGRATION_NAME,
        "dry_run": dry_run,
        "track_name": track_name,
        "generated_at": now_iso(),
        "active_batches": len(rows),
        "changed_batches": changed_batches,
        "batches": rows,
    }
    if not dry_run:
        report_path = root / "data" / "migrations" / MIGRATION_NAME / "report.json"
        write_json_atomic(report_path, report)
        report["report_json"] = str(report_path)
    return report


def clean_batch_payload(payload: dict[str, Any], *, raw_stage: str) -> dict[str, Any]:
    cleaned = json.loads(json.dumps(payload, ensure_ascii=False))
    if raw_stage in RETIRED_STAGE_NAMES:
        cleaned["current_stage"] = STAGE_PREPARED
        cleaned["blocking_reason"] = None
    else:
        cleaned["current_stage"] = normalize_stage_name(raw_stage)
    for key in ("llm_policy", "llm_execution_policy"):
        if key in cleaned:
            cleaned[key] = clean_policy(cleaned.get(key))
    artifacts = cleaned.get("artifacts")
    if isinstance(artifacts, dict):
        cleaned["artifacts"] = {
            key: value
            for key, value in artifacts.items()
            if not is_retired_artifact_key(str(key))
        }
    cleaned["updated_at"] = str(payload.get("updated_at") or now_iso())
    return cleaned


def clean_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = json.loads(json.dumps(payload, ensure_ascii=False))
    for key in ("llm_policy", "llm_execution_policy"):
        if key in cleaned:
            cleaned[key] = clean_policy(cleaned.get(key))
    return cleaned


def clean_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if str(key) not in RETIRED_LLM_POLICY_TASKS
    }


def retired_policy_tasks(payload: dict[str, Any]) -> list[str]:
    tasks: set[str] = set()
    for key in ("llm_policy", "llm_execution_policy"):
        policy = payload.get(key)
        if isinstance(policy, dict):
            tasks.update(str(task) for task in policy if task in RETIRED_LLM_POLICY_TASKS)
    return sorted(tasks)


def is_retired_artifact_key(key: str) -> bool:
    return key.startswith(RETIRED_ARTIFACT_PREFIXES)


def retired_artifact_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value if is_retired_artifact_key(str(key)))


def existing_remote_job_paths(root: Path, batch_name: str) -> list[str]:
    jobs_root = root / "data" / "llm" / "jobs"
    paths = [
        jobs_root / f"{batch_name}_alphabetic_judgment",
        jobs_root / f"{batch_name}_scope_triage",
    ]
    return [str(path) for path in paths if path.exists()]


def needs_review_pack_regeneration(
    workspace: PipelineWorkspace,
    batch_name: str,
) -> bool:
    pack_path = workspace.batch_dir(batch_name) / "final_review_pack.json"
    if not pack_path.exists():
        return True
    try:
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    items = pack.get("items")
    if not isinstance(items, list):
        return True
    return any(
        isinstance(item, dict)
        and (
            "scope_default" in item
            or "skip_default" in item
            or item.get("provisional_skip") is True
        )
        for item in items
    )


def backup_migration_inputs(
    *,
    root: Path,
    batch_name: str,
    state_path: Path,
    manifest_path: Path | None,
) -> None:
    backup_root = (
        root / "data" / "migrations" / MIGRATION_NAME / "backups" / batch_name
    )
    backup_root.mkdir(parents=True, exist_ok=True)
    for path in (state_path, manifest_path):
        if path is None:
            continue
        destination = backup_root / path.name
        if not destination.exists():
            shutil.copy2(path, destination)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
