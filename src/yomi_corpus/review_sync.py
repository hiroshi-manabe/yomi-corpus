from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from hashlib import sha256
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import (
    STAGE_FINAL_REVIEW_APPLIED,
    STAGE_FINAL_REVIEW_PREPARED,
    STAGE_SEQUENCE,
    STAGE_YOMI_FINALIZED,
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    PipelineWorkspace,
)
from yomi_corpus.document_review_state import (
    POOL_LABEL_BULK_READY,
    STATE_STRONG_PENDING,
    document_review_queue_summary,
    load_document_review_state,
)
from yomi_corpus.review_site import collect_review_pack_entries, publish_review_site
from yomi_corpus.review_transport import (
    PUBLISH_MODE_NONE,
    PUBLISH_MODE_GH_PAGES,
    PUBLISH_MODE_LOCAL,
    PUBLISH_MODES,
)


SYNC_STAGE_ALLOWLIST = {
    STAGE_FINAL_REVIEW_APPLIED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    STAGE_YOMI_FINALIZED,
}

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ReviewSyncOptions:
    track_name: str
    repo: str = "hiroshi-manabe/yomi-corpus"
    pages_url: str | None = None
    close_issues: bool = True
    publish_mode: str = PUBLISH_MODE_LOCAL
    max_stages: int = 10
    bulk_review_target_ready_docs: int = 0
    refill_pass_limit: int = 0
    dry_run: bool = False


class ReviewSyncLock(AbstractContextManager["ReviewSyncLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "ReviewSyncLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            existing = ""
            try:
                existing = self.path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
            detail = f" Existing lock: {existing}" if existing else ""
            raise SystemExit(f"Review sync lock already exists: {self.path}.{detail}") from exc
        payload = {"pid": os.getpid(), "created_at": now_iso()}
        os.write(self.fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def run_review_sync_pass(root: Path, options: ReviewSyncOptions) -> dict[str, Any]:
    workspace = PipelineWorkspace(root)
    lock_path = root / "data" / "state" / "review_sync" / f"{options.track_name}.lock"
    with ReviewSyncLock(lock_path):
        summary = _run_review_sync_pass_unlocked(root=root, workspace=workspace, options=options)
    if not options.dry_run:
        summary_path = write_review_sync_summary(root, options.track_name, summary)
        summary["summary_json"] = str(summary_path)
    return summary


def _run_review_sync_pass_unlocked(
    *,
    root: Path,
    workspace: PipelineWorkspace,
    options: ReviewSyncOptions,
) -> dict[str, Any]:
    started_at = now_iso()
    stage_results: list[dict[str, Any]] = []
    close_results: list[dict[str, Any]] = []
    refill_results: list[dict[str, Any]] = []
    changed = False
    dry_run_plan: list[dict[str, Any]] = []

    for _ in range(max(1, options.max_stages)):
        status = workspace.status(options.track_name)
        batch_name = str(status.get("current_batch_name") or status.get("batch_name") or "")
        next_stage = str(status.get("next_stage") or "")
        if not batch_name:
            dry_run_plan.append({"action": "stop", "reason": "no_current_batch"})
            break
        if not should_run_stage(root=root, batch_name=batch_name, next_stage=next_stage):
            dry_run_plan.append(
                {
                    "action": "stop",
                    "batch_name": batch_name,
                    "current_stage": status.get("current_stage"),
                    "next_stage": next_stage or None,
                    "reason": "next_stage_not_sync_managed",
                }
            )
            break
        dry_run_plan.append(
            {
                "action": "advance",
                "batch_name": batch_name,
                "next_stage": next_stage,
            }
        )
        if options.dry_run:
            break

        before_fingerprint = review_sync_fingerprint(root=root, batch_name=batch_name)
        result = workspace.advance(track_name=options.track_name)
        after_status = workspace.status(options.track_name)
        after_batch_name = str(
            after_status.get("current_batch_name")
            or after_status.get("batch_name")
            or batch_name
        )
        after_fingerprint = review_sync_fingerprint(root=root, batch_name=after_batch_name)
        stage_changed = before_fingerprint != after_fingerprint
        result["attempted_stage"] = next_stage
        result["stage_changed"] = stage_changed
        stage_results.append(result)
        if result.get("advanced") or stage_changed:
            changed = True
            close_results.extend(
                close_issues_after_stage(
                    root=root,
                    repo=options.repo,
                    attempted_stage=next_stage,
                    result=result,
                    enabled=options.close_issues,
                )
            )
        partial_results = maintain_strong_repair_for_reviewed_documents(
            root=root,
            workspace=workspace,
            batch_name=batch_name,
            allow_queue=attempted_final_review_changed(
                next_stage=next_stage,
                result=result,
                stage_changed=stage_changed,
            ),
        )
        for partial_result in partial_results:
            partial_result["partial_document_workflow"] = True
            stage_results.append(partial_result)
            close_results.extend(
                close_issues_after_stage(
                    root=root,
                    repo=options.repo,
                    attempted_stage=str(partial_result.get("attempted_stage") or ""),
                    result=partial_result,
                    enabled=options.close_issues,
                )
            )
        if partial_results:
            after_partial_fingerprint = review_sync_fingerprint(
                root=root,
                batch_name=batch_name,
            )
            if after_partial_fingerprint != after_fingerprint:
                changed = True
        if not result.get("advanced"):
            break
        if result.get("blocking_reason"):
            break

    final_status = workspace.status(options.track_name)
    final_batch_name = str(
        final_status.get("current_batch_name")
        or final_status.get("batch_name")
        or ""
    )
    document_queue_summary = current_document_queue_summary(
        root=root,
        batch_name=final_batch_name,
    )
    refill_plan = build_bulk_review_refill_plan(
        document_queue_summary=document_queue_summary,
        target_ready_docs=options.bulk_review_target_ready_docs,
        pass_limit=options.refill_pass_limit,
    )
    if int(refill_plan.get("planned_prepare_documents") or 0) > 0:
        refill_plan["source_selection"] = workspace.preview_next_source_documents(
            track_name=options.track_name,
            target_documents=int(refill_plan["planned_prepare_documents"]),
        )
    if refill_plan.get("enabled"):
        refill_result = maintain_bulk_review_refill(
            workspace=workspace,
            options=options,
            refill_plan=refill_plan,
        )
        if refill_result is not None:
            refill_results.append(refill_result)
            if refill_result.get("changed"):
                changed = True
            final_status = workspace.status(options.track_name)
            final_batch_name = str(
                final_status.get("current_batch_name")
                or final_status.get("batch_name")
                or ""
            )
            document_queue_summary = current_document_queue_summary(
                root=root,
                batch_name=final_batch_name,
            )
            refill_plan = build_bulk_review_refill_plan(
                document_queue_summary=document_queue_summary,
                target_ready_docs=options.bulk_review_target_ready_docs,
                pass_limit=options.refill_pass_limit,
            )
            if int(refill_plan.get("planned_prepare_documents") or 0) > 0:
                refill_plan["source_selection"] = workspace.preview_next_source_documents(
                    track_name=options.track_name,
                    target_documents=int(refill_plan["planned_prepare_documents"]),
                )

    site_stale = False
    if not options.dry_run and options.publish_mode != PUBLISH_MODE_NONE:
        site_stale = review_site_needs_publish(root)
        if site_stale:
            changed = True

    publish_result: dict[str, Any] | None = None
    if changed and options.publish_mode != PUBLISH_MODE_NONE and not options.dry_run:
        publish_result = publish_review_artifacts(
            root,
            push_gh_pages=options.publish_mode == PUBLISH_MODE_GH_PAGES,
        )

    completed_at = now_iso()
    return {
        "schema_version": 1,
        "track_name": options.track_name,
        "repo": options.repo,
        "pages_url": options.pages_url,
        "publish_mode": options.publish_mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "dry_run": options.dry_run,
        "refill_policy": {
            "bulk_review_target_ready_docs": options.bulk_review_target_ready_docs,
            "refill_pass_limit": options.refill_pass_limit,
        },
        "refill_plan": refill_plan,
        "changed": changed,
        "site_stale": site_stale,
        "stage_results": stage_results,
        "refill_results": refill_results,
        "close_results": close_results,
        "publish_result": publish_result,
        "dry_run_plan": dry_run_plan,
        "final_status": final_status,
        "document_queue_summary": document_queue_summary,
    }


def build_bulk_review_refill_plan(
    *,
    document_queue_summary: dict[str, Any] | None,
    target_ready_docs: int,
    pass_limit: int,
) -> dict[str, Any]:
    target = max(0, int(target_ready_docs or 0))
    limit = max(0, int(pass_limit or 0))
    pool_counts = (
        document_queue_summary.get("pool_counts", {})
        if isinstance(document_queue_summary, dict)
        else {}
    )
    if not isinstance(pool_counts, dict):
        pool_counts = {}
    ready = int(pool_counts.get(POOL_LABEL_BULK_READY) or 0)
    deficit = max(0, target - ready)
    planned = min(deficit, limit) if limit > 0 else 0
    return {
        "enabled": target > 0,
        "status": "disabled" if target <= 0 else ("needs_refill" if deficit > 0 else "satisfied"),
        "bulk_review_ready_docs": ready,
        "target_ready_docs": target,
        "deficit": deficit,
        "pass_limit": limit,
        "planned_prepare_documents": planned,
        "will_prepare": False,
        "reason": (
            "dry-run-safe plan; non-dry-run sync can prepare and advance these documents"
            if target > 0 and planned > 0
            else None
        ),
    }


def maintain_bulk_review_refill(
    *,
    workspace: PipelineWorkspace,
    options: ReviewSyncOptions,
    refill_plan: dict[str, Any],
) -> dict[str, Any] | None:
    status = workspace.status(options.track_name)
    current_stage = str(status.get("current_stage") or "")
    current_batch = str(status.get("current_batch_name") or status.get("batch_name") or "")
    if batch_is_before_bulk_review_ready(current_stage):
        if options.dry_run:
            return {
                "action": "continue_existing_batch",
                "batch_name": current_batch,
                "current_stage": current_stage,
                "target_stage": STAGE_FINAL_REVIEW_PREPARED,
                "changed": False,
                "dry_run": True,
            }
        advance_result = advance_current_batch_to_bulk_review_ready(
            workspace=workspace,
            track_name=options.track_name,
            max_stages=options.max_stages,
        )
        return {
            "action": "continue_existing_batch",
            "batch_name": current_batch,
            "changed": bool(advance_result.get("changed")),
            "dry_run": False,
            **advance_result,
        }

    planned = int(refill_plan.get("planned_prepare_documents") or 0)
    if planned <= 0:
        return None
    if options.dry_run:
        return {
            "action": "prepare_next_batch",
            "status": "planned",
            "planned_prepare_documents": planned,
            "source_selection": refill_plan.get("source_selection"),
            "target_stage": STAGE_FINAL_REVIEW_PREPARED,
            "changed": False,
            "dry_run": True,
        }

    prepare_result = workspace.prepare_next_batch(
        track_name=options.track_name,
        target_documents=planned,
    )
    advance_result = advance_current_batch_to_bulk_review_ready(
        workspace=workspace,
        track_name=options.track_name,
        max_stages=options.max_stages,
    )
    return {
        "action": "prepare_next_batch",
        "planned_prepare_documents": planned,
        "source_selection": refill_plan.get("source_selection"),
        "prepare_result": prepare_result,
        "changed": True,
        "dry_run": False,
        **advance_result,
    }


def batch_is_before_bulk_review_ready(stage_name: str) -> bool:
    try:
        current_index = STAGE_SEQUENCE.index(stage_name)
        target_index = STAGE_SEQUENCE.index(STAGE_FINAL_REVIEW_PREPARED)
    except ValueError:
        return False
    return current_index < target_index


def advance_current_batch_to_bulk_review_ready(
    *,
    workspace: PipelineWorkspace,
    track_name: str,
    max_stages: int,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    changed = False
    prepared_to_bulk_steps = STAGE_SEQUENCE.index(STAGE_FINAL_REVIEW_PREPARED)
    stage_limit = max(1, max_stages, prepared_to_bulk_steps)
    for _ in range(stage_limit):
        status = workspace.status(track_name)
        current_stage = str(status.get("current_stage") or "")
        if current_stage == STAGE_FINAL_REVIEW_PREPARED:
            return {
                "status": "bulk_review_ready",
                "batch_name": status.get("current_batch_name") or status.get("batch_name"),
                "current_stage": current_stage,
                "target_stage": STAGE_FINAL_REVIEW_PREPARED,
                "steps": steps,
                "changed": changed,
            }
        next_stage = str(status.get("next_stage") or "")
        if not next_stage:
            return {
                "status": "stopped",
                "reason": "no_next_stage",
                "batch_name": status.get("current_batch_name") or status.get("batch_name"),
                "current_stage": current_stage,
                "target_stage": STAGE_FINAL_REVIEW_PREPARED,
                "steps": steps,
                "changed": changed,
            }
        if next_stage not in STAGE_SEQUENCE or STAGE_SEQUENCE.index(next_stage) > STAGE_SEQUENCE.index(
            STAGE_FINAL_REVIEW_PREPARED
        ):
            return {
                "status": "stopped",
                "reason": "next_stage_beyond_bulk_review_ready",
                "batch_name": status.get("current_batch_name") or status.get("batch_name"),
                "current_stage": current_stage,
                "next_stage": next_stage,
                "target_stage": STAGE_FINAL_REVIEW_PREPARED,
                "steps": steps,
                "changed": changed,
            }
        result = workspace.advance(track_name=track_name)
        step = {
            "attempted_stage": next_stage,
            "advanced": result.get("advanced"),
            "current_stage": result.get("current_stage"),
            "blocking_reason": result.get("blocking_reason"),
        }
        steps.append(step)
        if result.get("advanced"):
            changed = True
            if result.get("current_stage") == STAGE_FINAL_REVIEW_PREPARED:
                return {
                    "status": "bulk_review_ready",
                    "batch_name": result.get("batch_name"),
                    "current_stage": result.get("current_stage"),
                    "target_stage": STAGE_FINAL_REVIEW_PREPARED,
                    "steps": steps,
                    "changed": changed,
                }
        if not result.get("advanced"):
            return {
                "status": "incomplete",
                "reason": result.get("blocking_reason") or "stage_not_advanced",
                "batch_name": result.get("batch_name"),
                "current_stage": result.get("current_stage"),
                "target_stage": STAGE_FINAL_REVIEW_PREPARED,
                "steps": steps,
                "changed": changed,
            }
    status = workspace.status(track_name)
    return {
        "status": "incomplete",
        "reason": "max_stages_reached",
        "batch_name": status.get("current_batch_name") or status.get("batch_name"),
        "current_stage": status.get("current_stage"),
        "target_stage": STAGE_FINAL_REVIEW_PREPARED,
        "steps": steps,
        "changed": changed,
    }


def should_run_stage(*, root: Path, batch_name: str, next_stage: str) -> bool:
    if next_stage in SYNC_STAGE_ALLOWLIST:
        return True
    if next_stage == STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED:
        queue_jsonl = root / "data" / "units" / batch_name / "yomi_strong_repair_queue.jsonl"
        return count_nonempty_lines(queue_jsonl) == 0
    return False


def attempted_final_review_changed(
    *,
    next_stage: str,
    result: dict[str, Any],
    stage_changed: bool,
) -> bool:
    if next_stage != STAGE_FINAL_REVIEW_APPLIED:
        return False
    if not stage_changed:
        return False
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return False
    return bool(artifacts.get("final_review_apply_summary_json"))


def maintain_strong_repair_for_reviewed_documents(
    *,
    root: Path,
    workspace: PipelineWorkspace,
    batch_name: str,
    allow_queue: bool,
) -> list[dict[str, Any]]:
    reviewed_units = root / "data" / "units" / batch_name / "units.yomi.reviewed.jsonl"
    if not reviewed_units.exists():
        return []
    results: list[dict[str, Any]] = []
    if allow_queue or has_strong_pending_documents(root=root, batch_name=batch_name):
        queue_result = workspace._queue_yomi_strong_repair(batch_name)
        queue_result["attempted_stage"] = STAGE_YOMI_STRONG_REPAIR_QUEUED
        results.append(queue_result)
        queued = int(
            queue_result.get("artifacts", {}).get("yomi_strong_repair_queued") or 0
        )
        if queued > 0:
            repair_result = workspace._run_yomi_strong_repair(batch_name)
            repair_result["attempted_stage"] = STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED
            results.append(repair_result)

    strong_review_pack = root / "data" / "units" / batch_name / "yomi_strong_repair_review_pack.json"
    if strong_review_pack.exists():
        review_result = workspace._apply_strong_repair_review(batch_name)
        review_result["attempted_stage"] = STAGE_YOMI_FINALIZED
        results.append(review_result)
    return results


def has_strong_pending_documents(*, root: Path, batch_name: str) -> bool:
    state_path = root / "data" / "pipeline" / "document_states" / f"{batch_name}.json"
    if not state_path.exists():
        return False
    try:
        state = load_document_review_state(state_path)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return any(
        document.get("state") == STATE_STRONG_PENDING
        and int(document.get("strong_repair_item_count") or 0) > 0
        for document in state.get("documents", [])
    )


def current_document_queue_summary(*, root: Path, batch_name: str) -> dict[str, Any] | None:
    if not batch_name:
        return None
    state_path = root / "data" / "pipeline" / "document_states" / f"{batch_name}.json"
    if not state_path.exists():
        return None
    try:
        return document_review_queue_summary(load_document_review_state(state_path))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def review_sync_fingerprint(*, root: Path, batch_name: str) -> dict[str, str | None]:
    paths = [
        root / "data" / "pipeline" / "document_states" / f"{batch_name}.json",
        root / "data" / "units" / batch_name / "final_review_apply_summary.json",
        root / "data" / "units" / batch_name / "final_review_pack.json",
        root
        / "data"
        / "review_packs"
        / "yomi_final"
        / f"yomi_final_{batch_name}_v1.json",
        root / "data" / "units" / batch_name / "yomi_strong_repair_queue.jsonl",
        root / "data" / "units" / batch_name / "yomi_strong_repair_queue_summary.json",
        root / "data" / "units" / batch_name / "yomi_strong_repair_results.jsonl",
        root / "data" / "units" / batch_name / "yomi_strong_repair_usage_summary.json",
        root / "data" / "units" / batch_name / "yomi_strong_repair_apply_summary.json",
        root / "data" / "units" / batch_name / "units.yomi.strong_repaired.jsonl",
        root / "data" / "units" / batch_name / "yomi_strong_repair_review_pack.json",
        root
        / "data"
        / "review_packs"
        / "yomi_strong_repair"
        / f"yomi_strong_repair_{batch_name}_v1.json",
        root / "data" / "state" / "yomi_final" / "last_review_inbox_import_summary.json",
        root / "data" / "state" / "yomi_strong_repair" / "last_review_inbox_import_summary.json",
    ]
    return {str(path.relative_to(root)): file_fingerprint(path) for path in paths}


def file_fingerprint(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def review_site_needs_publish(root: Path) -> bool:
    docs_pack_dir = root / "docs" / "review" / "packs"
    entries = collect_review_pack_entries(root / "data" / "review_packs")
    if entries and not (root / "docs" / "review" / "manifest.json").exists():
        return True
    for entry in entries:
        source_path = entry.get("source_path")
        filename = entry.get("site_filename")
        if not isinstance(source_path, Path) or not filename:
            return True
        destination = docs_pack_dir / str(filename)
        if file_fingerprint(source_path) != file_fingerprint(destination):
            return True
    return False


def close_issues_after_stage(
    *,
    root: Path,
    repo: str,
    attempted_stage: str,
    result: dict[str, Any],
    enabled: bool,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    summary_key = None
    if attempted_stage == STAGE_FINAL_REVIEW_APPLIED:
        summary_key = "final_review_issue_import_summary_json"
    elif attempted_stage == STAGE_YOMI_FINALIZED:
        summary_key = "yomi_strong_repair_review_issue_import_summary_json"
    if not summary_key:
        return []
    summary_path_raw = artifacts.get(summary_key)
    if not summary_path_raw:
        return []
    summary_path = Path(str(summary_path_raw))
    if not summary_path.is_absolute():
        summary_path = root / summary_path
    if not summary_path.exists():
        return []
    import_summary = read_json(summary_path)
    issue_numbers = closable_issue_numbers(import_summary)
    return [close_github_issue(repo=repo, issue_number=number) for number in issue_numbers]


def closable_issue_numbers(import_summary: dict[str, Any]) -> list[int]:
    if import_summary.get("status") not in {"ok", None}:
        return []
    imported: set[int] = set()
    problematic: set[int] = set()
    for row in import_summary.get("summaries") or []:
        issue_number = issue_number_from_source(row.get("source"))
        if issue_number:
            imported.add(issue_number)
    for row in import_summary.get("skipped") or []:
        reason = str(row.get("reason") or "")
        if reason == "duplicate_submission_id":
            continue
        issue_number = issue_number_from_source(row.get("source"))
        if issue_number:
            problematic.add(issue_number)
    return sorted(imported - problematic)


def issue_number_from_source(source: object) -> int | None:
    if not isinstance(source, dict):
        return None
    raw = source.get("issue_number")
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def close_github_issue(*, repo: str, issue_number: int) -> dict[str, Any]:
    if shutil.which("gh") is None:
        return {
            "issue_number": issue_number,
            "status": "failed",
            "error": "gh command not found",
        }
    command = [
        "gh",
        "api",
        "-X",
        "PATCH",
        f"repos/{repo}/issues/{issue_number}",
        "-f",
        "state=closed",
        "-f",
        "state_reason=completed",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return {
            "issue_number": issue_number,
            "status": "failed",
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip(),
        }
    return {"issue_number": issue_number, "status": "closed"}


def publish_review_artifacts(root: Path, *, push_gh_pages: bool) -> dict[str, Any]:
    manifest = publish_review_site(
        web_review_dir=root / "web" / "review",
        docs_dir=root / "docs",
        review_pack_root=root / "data" / "review_packs",
    )
    result: dict[str, Any] = {
        "status": "generated",
        "manifest_json": str(root / "docs" / "review" / "manifest.json"),
        "active_queue_count": len(manifest.get("current_review_queues", [])),
    }
    if not push_gh_pages:
        return result
    completed = subprocess.run(
        [str(root / "publish-review")],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    result.update(
        {
            "publish_gh_pages": True,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "status": "published" if completed.returncode == 0 else "publish_failed",
        }
    )
    return result


def write_review_sync_summary(root: Path, track_name: str, summary: dict[str, Any]) -> Path:
    state_dir = root / "data" / "state" / "review_sync"
    state_dir.mkdir(parents=True, exist_ok=True)
    latest_path = state_dir / f"{track_name}.last.json"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history_path = state_dir / f"{track_name}.{timestamp}.json"
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    latest_path.write_text(text, encoding="utf-8")
    history_path.write_text(text, encoding="utf-8")
    return latest_path


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def count_nonempty_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def run_review_sync_loop(
    root: Path,
    options: ReviewSyncOptions,
    *,
    interval_seconds: float,
) -> None:
    while True:
        summary = run_review_sync_pass(root, options)
        print(json.dumps(compact_console_summary(summary), ensure_ascii=False, indent=2))
        time.sleep(interval_seconds)


def compact_console_summary(summary: dict[str, Any]) -> dict[str, Any]:
    final_status = summary.get("final_status") if isinstance(summary.get("final_status"), dict) else {}
    document_queue_summary = (
        summary.get("document_queue_summary")
        if isinstance(summary.get("document_queue_summary"), dict)
        else {}
    )
    refill_plan = summary.get("refill_plan") if isinstance(summary.get("refill_plan"), dict) else {}
    return {
        "track_name": summary.get("track_name"),
        "changed": summary.get("changed"),
        "site_stale": summary.get("site_stale"),
        "document_queue_counts": document_queue_summary.get("queue_counts"),
        "document_pool_counts": document_queue_summary.get("pool_counts"),
        "refill_plan": refill_plan or None,
        "stages": [
            {
                "attempted_stage": row.get("attempted_stage"),
                "advanced": row.get("advanced"),
                "stage_changed": row.get("stage_changed"),
                "blocking_reason": row.get("blocking_reason"),
            }
            for row in summary.get("stage_results", [])
            if isinstance(row, dict)
        ],
        "refill": [
            {
                "action": row.get("action"),
                "status": row.get("status"),
                "batch_name": refill_result_batch_name(row),
                "planned_prepare_documents": row.get("planned_prepare_documents"),
                "changed": row.get("changed"),
                "step_count": len(row.get("steps", [])) if isinstance(row.get("steps"), list) else 0,
                "reason": row.get("reason"),
            }
            for row in summary.get("refill_results", [])
            if isinstance(row, dict)
        ],
        "closed_issues": [
            row.get("issue_number")
            for row in summary.get("close_results", [])
            if isinstance(row, dict) and row.get("status") == "closed"
        ],
        "next_stage": final_status.get("next_stage") if isinstance(final_status, dict) else None,
        "blocking_reason": final_status.get("blocking_reason") if isinstance(final_status, dict) else None,
        "summary_json": summary.get("summary_json"),
    }


def refill_result_batch_name(row: dict[str, Any]) -> object:
    if row.get("batch_name"):
        return row.get("batch_name")
    prepare_result = row.get("prepare_result")
    if isinstance(prepare_result, dict):
        return prepare_result.get("batch_name")
    return None
