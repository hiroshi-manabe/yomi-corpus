from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import (
    STAGE_FINAL_REVIEW_PREPARED,
    STAGE_SEQUENCE,
    PipelineWorkspace,
    llm_task_for_stage,
)
from yomi_corpus.review_sync import (
    ReviewSyncLock,
    aggregate_document_queue_summary,
    build_bulk_review_refill_plan,
    list_track_batches,
)


REFILL_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RefillWorkerOptions:
    track_name: str
    target_ready_docs: int
    pass_limit: int
    max_stages: int = 20
    llm_execution_mode_override: str | None = None
    dry_run: bool = False


def run_refill_worker_pass(root: Path, options: RefillWorkerOptions) -> dict[str, Any]:
    state_dir = root / "data" / "state" / "refill"
    lock_path = state_dir / f"{options.track_name}.lock"
    with ReviewSyncLock(lock_path, label="Refill worker"):
        summary = _run_refill_worker_pass_unlocked(root=root, options=options)
    if not options.dry_run:
        summary_path = write_refill_summary(root, options.track_name, summary)
        summary["summary_json"] = str(summary_path)
    return summary


def _run_refill_worker_pass_unlocked(
    *,
    root: Path,
    options: RefillWorkerOptions,
) -> dict[str, Any]:
    workspace = PipelineWorkspace(root)
    started_at_epoch = int(time.time())
    queue_summary = aggregate_document_queue_summary(
        root=root,
        workspace=workspace,
        track_name=options.track_name,
    )
    plan = build_bulk_review_refill_plan(
        document_queue_summary=queue_summary,
        target_ready_docs=options.target_ready_docs,
        pass_limit=options.pass_limit,
    )
    resumable_batch = find_resumable_refill_batch(workspace, options.track_name)
    action: dict[str, Any]

    if resumable_batch is not None:
        action = {
            "action": "resume_batch",
            "batch_name": resumable_batch,
            "changed": False,
        }
    elif int(plan.get("planned_prepare_documents") or 0) > 0:
        planned = int(plan["planned_prepare_documents"])
        plan["source_selection"] = workspace.preview_next_source_documents(
            track_name=options.track_name,
            target_documents=planned,
        )
        if options.dry_run:
            action = {
                "action": "prepare_next_batch",
                "planned_prepare_documents": planned,
                "changed": False,
                "dry_run": True,
            }
        else:
            prepared = workspace.prepare_next_batch(
                track_name=options.track_name,
                target_documents=planned,
            )
            resumable_batch = str(prepared["batch_name"])
            action = {
                "action": "prepare_next_batch",
                "batch_name": resumable_batch,
                "prepare_result": prepared,
                "changed": True,
            }
    else:
        action = {
            "action": "none",
            "reason": "bulk_review_target_satisfied",
            "changed": False,
        }

    if resumable_batch is not None and not options.dry_run:
        batch_lock = root / "data" / "state" / "refill" / "batches" / f"{resumable_batch}.lock"
        with ReviewSyncLock(batch_lock, label="Refill batch"):
            advance_result = advance_batch_to_bulk_review_ready(
                workspace=workspace,
                batch_name=resumable_batch,
                max_stages=options.max_stages,
                llm_execution_mode_override=options.llm_execution_mode_override,
            )
        action["advance_result"] = advance_result
        action["changed"] = bool(action["changed"] or advance_result.get("changed"))
        action["review_publish_requested"] = (
            advance_result.get("status") == "bulk_review_ready"
        )

    completed_at_epoch = int(time.time())
    return {
        "schema_version": REFILL_STATE_SCHEMA_VERSION,
        "track_name": options.track_name,
        "started_at": epoch_iso(started_at_epoch),
        "completed_at": epoch_iso(completed_at_epoch),
        "duration_seconds": completed_at_epoch - started_at_epoch,
        "dry_run": options.dry_run,
        "policy": {
            "target_ready_docs": options.target_ready_docs,
            "pass_limit": options.pass_limit,
            "max_stages": options.max_stages,
            "llm_execution_mode_override": options.llm_execution_mode_override,
        },
        "refill_plan": plan,
        "action": action,
    }


def find_resumable_refill_batch(
    workspace: PipelineWorkspace,
    track_name: str,
) -> str | None:
    target_index = STAGE_SEQUENCE.index(STAGE_FINAL_REVIEW_PREPARED)
    candidates: list[str] = []
    for batch_name in list_track_batches(workspace, track_name):
        batch_state = workspace.load_batch_state(batch_name)
        try:
            stage_index = STAGE_SEQUENCE.index(batch_state.current_stage)
        except ValueError:
            continue
        if stage_index < target_index:
            candidates.append(batch_name)
    return min(candidates) if candidates else None


def advance_batch_to_bulk_review_ready(
    *,
    workspace: PipelineWorkspace,
    batch_name: str,
    max_stages: int,
    llm_execution_mode_override: str | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    changed = False
    stage_limit = max(1, int(max_stages))
    for _ in range(stage_limit):
        batch_state = workspace.load_batch_state(batch_name)
        current_stage = str(batch_state.current_stage)
        if current_stage == STAGE_FINAL_REVIEW_PREPARED:
            return {
                "status": "bulk_review_ready",
                "batch_name": batch_name,
                "current_stage": current_stage,
                "steps": steps,
                "changed": changed,
            }
        next_stage = workspace._next_stage_name(current_stage)
        if not next_stage:
            return {
                "status": "stopped",
                "reason": "no_next_stage",
                "batch_name": batch_name,
                "current_stage": current_stage,
                "steps": steps,
                "changed": changed,
            }
        try:
            next_index = STAGE_SEQUENCE.index(next_stage)
        except ValueError:
            next_index = len(STAGE_SEQUENCE)
        if next_index > STAGE_SEQUENCE.index(STAGE_FINAL_REVIEW_PREPARED):
            return {
                "status": "stopped",
                "reason": "next_stage_beyond_bulk_review_ready",
                "batch_name": batch_name,
                "current_stage": current_stage,
                "next_stage": next_stage,
                "steps": steps,
                "changed": changed,
            }
        result = workspace.advance_batch(
            batch_name,
            llm_execution_mode_override=(
                llm_execution_mode_override
                if llm_task_for_stage(next_stage) is not None
                else None
            ),
        )
        step = {
            "attempted_stage": next_stage,
            "advanced": result.get("advanced"),
            "current_stage": result.get("current_stage"),
            "blocking_reason": result.get("blocking_reason"),
        }
        steps.append(step)
        if result.get("advanced"):
            changed = True
            continue
        return {
            "status": "incomplete",
            "reason": result.get("blocking_reason") or "stage_not_advanced",
            "batch_name": batch_name,
            "current_stage": result.get("current_stage") or current_stage,
            "steps": steps,
            "changed": changed,
        }
    batch_state = workspace.load_batch_state(batch_name)
    return {
        "status": "incomplete",
        "reason": "max_stages_reached",
        "batch_name": batch_name,
        "current_stage": batch_state.current_stage,
        "steps": steps,
        "changed": changed,
    }


def write_refill_summary(root: Path, track_name: str, summary: dict[str, Any]) -> Path:
    state_dir = root / "data" / "state" / "refill"
    state_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    historical = state_dir / f"{track_name}.{timestamp}.json"
    latest = state_dir / f"{track_name}.last.json"
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    historical.write_text(text, encoding="utf-8")
    temporary = latest.with_suffix(".json.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(latest)
    return latest


def epoch_iso(epoch: int) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
