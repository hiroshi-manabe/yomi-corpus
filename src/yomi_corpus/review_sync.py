from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import tomllib
from hashlib import sha256
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from yomi_corpus.decoder_refresh_policy import (
    DECODER_REFRESH_MODES,
    DECODER_REFRESH_MODE_NEVER,
)
from yomi_corpus.pipeline import (
    LLM_EXECUTION_MODES,
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
)
from yomi_corpus.yomi.final_review import (
    FINALIZED_CORRECTION_STAGE,
    apply_finalized_correction_submissions_file,
)
from yomi_corpus.yomi.final_review_issue_import import import_open_issue_inbox

DEFAULT_REVIEW_SYNC_CONFIG = "config/review_sync/default.toml"
RUNTIME_STATUS_SCHEMA_VERSION = 1
DEFAULT_RUNTIME_STATUS_INTERVAL_SECONDS = 300
DEFAULT_RUNTIME_STATUS_GRACE_SECONDS = 90
DEFAULT_RUNTIME_STATUS_NORMAL_POLL_SECONDS = 60
DEFAULT_RUNTIME_STATUS_NEAR_POLL_SECONDS = 15
DEFAULT_RUNTIME_STATUS_HIDDEN_POLL_SECONDS = 300


SYNC_STAGE_ALLOWLIST = {
    STAGE_FINAL_REVIEW_APPLIED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    STAGE_YOMI_FINALIZED,
}

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_review_sync_config(
    track_name: str,
    path: str | Path = DEFAULT_REVIEW_SYNC_CONFIG,
) -> ReviewSyncConfig:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parents[2] / config_path
    if not config_path.exists():
        return ReviewSyncConfig()
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    tracks = payload.get("tracks", {})
    track = tracks.get(track_name)
    if not isinstance(track, dict):
        return ReviewSyncConfig()
    section = track.get("decoder_refresh", {})
    if not isinstance(section, dict):
        section = {}
    mode = str(section.get("mode") or DECODER_REFRESH_MODE_NEVER).replace("_", "-")
    if mode not in DECODER_REFRESH_MODES:
        raise ValueError(f"Unsupported decoder refresh mode for {track_name}: {mode}")
    refill_section = track.get("bulk_review_refill", {})
    if not isinstance(refill_section, dict):
        refill_section = {}
    runtime_section = track.get("runtime_status", {})
    if not isinstance(runtime_section, dict):
        runtime_section = {}
    refill_worker_section = track.get("refill_worker", {})
    if not isinstance(refill_worker_section, dict):
        refill_worker_section = {}
    refill_llm_execution_mode = str(
        refill_worker_section.get("llm_execution_mode") or ""
    ) or None
    if (
        refill_llm_execution_mode is not None
        and refill_llm_execution_mode not in LLM_EXECUTION_MODES
    ):
        raise ValueError(
            f"Unsupported refill LLM execution mode for {track_name}: "
            f"{refill_llm_execution_mode}"
        )
    return ReviewSyncConfig(
        mode=mode,
        min_new_batches=max(1, int(section.get("min_new_batches") or 1)),
        min_interval_minutes=max(0.0, float(section.get("min_interval_minutes") or 0.0)),
        skip_kenlm=bool(section.get("skip_kenlm", False)),
        bulk_review_target_ready_docs=max(
            0, int(refill_section.get("target_ready_docs") or 0)
        ),
        refill_pass_limit=max(0, int(refill_section.get("pass_limit") or 0)),
        refill_max_stages=max(1, int(refill_worker_section.get("max_stages") or 20)),
        refill_aligned_batch_size=max(
            0, int(refill_worker_section.get("aligned_batch_size") or 0)
        ),
        refill_llm_execution_mode=refill_llm_execution_mode,
        runtime_status_interval_seconds=max(
            1,
            int(
                runtime_section.get("interval_seconds")
                or DEFAULT_RUNTIME_STATUS_INTERVAL_SECONDS
            ),
        ),
        runtime_status_grace_seconds=max(
            0,
            int(
                runtime_section.get("grace_seconds")
                or DEFAULT_RUNTIME_STATUS_GRACE_SECONDS
            ),
        ),
        runtime_status_normal_poll_seconds=max(
            1,
            int(
                runtime_section.get("normal_poll_seconds")
                or DEFAULT_RUNTIME_STATUS_NORMAL_POLL_SECONDS
            ),
        ),
        runtime_status_near_poll_seconds=max(
            1,
            int(
                runtime_section.get("near_poll_seconds")
                or DEFAULT_RUNTIME_STATUS_NEAR_POLL_SECONDS
            ),
        ),
        runtime_status_hidden_poll_seconds=max(
            1,
            int(
                runtime_section.get("hidden_poll_seconds")
                or DEFAULT_RUNTIME_STATUS_HIDDEN_POLL_SECONDS
            ),
        ),
    )


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
    refill_aligned_batch_size: int = 0
    dry_run: bool = False
    runtime_status_interval_seconds: int = DEFAULT_RUNTIME_STATUS_INTERVAL_SECONDS
    runtime_status_grace_seconds: int = DEFAULT_RUNTIME_STATUS_GRACE_SECONDS
    runtime_status_normal_poll_seconds: int = DEFAULT_RUNTIME_STATUS_NORMAL_POLL_SECONDS
    runtime_status_near_poll_seconds: int = DEFAULT_RUNTIME_STATUS_NEAR_POLL_SECONDS
    runtime_status_hidden_poll_seconds: int = DEFAULT_RUNTIME_STATUS_HIDDEN_POLL_SECONDS


@dataclass(frozen=True)
class ReviewSyncConfig:
    mode: str = DECODER_REFRESH_MODE_NEVER
    min_new_batches: int = 1
    min_interval_minutes: float = 0.0
    skip_kenlm: bool = False
    bulk_review_target_ready_docs: int = 0
    refill_pass_limit: int = 0
    refill_max_stages: int = 20
    refill_aligned_batch_size: int = 0
    refill_llm_execution_mode: str | None = None
    runtime_status_interval_seconds: int = DEFAULT_RUNTIME_STATUS_INTERVAL_SECONDS
    runtime_status_grace_seconds: int = DEFAULT_RUNTIME_STATUS_GRACE_SECONDS
    runtime_status_normal_poll_seconds: int = DEFAULT_RUNTIME_STATUS_NORMAL_POLL_SECONDS
    runtime_status_near_poll_seconds: int = DEFAULT_RUNTIME_STATUS_NEAR_POLL_SECONDS
    runtime_status_hidden_poll_seconds: int = DEFAULT_RUNTIME_STATUS_HIDDEN_POLL_SECONDS


class ReviewSyncLock(AbstractContextManager["ReviewSyncLock"]):
    def __init__(self, path: Path, *, label: str = "Review sync") -> None:
        self.path = path
        self.label = label
        self.fd: int | None = None

    def __enter__(self) -> "ReviewSyncLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                stale_reason = review_sync_lock_stale_reason(self.path)
                existing = read_lock_text(self.path)
                if stale_reason is not None:
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                    else:
                        continue
                detail = f" Existing lock: {existing}" if existing else ""
                reason = f" Reason: {stale_reason}." if stale_reason else ""
                raise SystemExit(
                    f"{self.label} lock already exists: {self.path}.{reason}{detail}"
                ) from exc
        payload = {"pid": os.getpid(), "created_at": now_iso()}
        os.write(self.fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def read_lock_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def review_sync_lock_stale_reason(path: Path) -> str | None:
    text = read_lock_text(path)
    if not text:
        return "empty_or_unreadable_lock"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "malformed_lock_json"
    if not isinstance(payload, dict):
        return "malformed_lock_payload"
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return "missing_lock_pid"
    if pid <= 0:
        return "invalid_lock_pid"
    if process_is_alive(pid):
        return None
    return "lock_pid_not_running"


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_review_sync_pass(root: Path, options: ReviewSyncOptions) -> dict[str, Any]:
    workspace = PipelineWorkspace(root)
    lock_path = root / "data" / "state" / "review_sync" / f"{options.track_name}.lock"
    try:
        with ReviewSyncLock(lock_path):
            summary = _run_review_sync_pass_unlocked(root=root, workspace=workspace, options=options)
    except Exception as exc:
        if not options.dry_run:
            try:
                final_status = workspace.status(options.track_name)
            except Exception:
                final_status = {}
            try:
                queue_summary = aggregate_document_queue_summary(
                    root=root,
                    workspace=workspace,
                    track_name=options.track_name,
                )
            except Exception:
                queue_summary = {}
            update_runtime_status(
                root=root,
                options=options,
                started_at_epoch=int(time.time()),
                completed_at_epoch=int(time.time()),
                final_status=final_status,
                document_queue_summary=queue_summary,
                workflow_changed=False,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            if options.publish_mode != PUBLISH_MODE_NONE:
                try:
                    publish_review_artifacts(
                        root,
                        push_gh_pages=options.publish_mode == PUBLISH_MODE_GH_PAGES,
                    )
                except Exception:
                    pass
        raise
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
    started_at_epoch = int(time.time())
    stage_results: list[dict[str, Any]] = []
    close_results: list[dict[str, Any]] = []
    refill_results: list[dict[str, Any]] = []
    sweep_results: list[dict[str, Any]] = []
    finalized_correction_result: dict[str, Any] | None = None
    intermediate_publish_results: list[dict[str, Any]] = []
    changed = False
    dry_run_plan: list[dict[str, Any]] = []

    def publish_applied_review_state(
        attempted_stage: str,
        result: dict[str, Any],
    ) -> None:
        if intermediate_publish_results or options.dry_run:
            return
        if options.publish_mode == PUBLISH_MODE_NONE:
            return
        if not review_submission_was_imported(
            root=root,
            attempted_stage=attempted_stage,
            result=result,
        ):
            return
        publish_result = publish_review_artifacts(
            root,
            push_gh_pages=options.publish_mode == PUBLISH_MODE_GH_PAGES,
        )
        intermediate_publish_results.append(
            {
                "reason": "review_submission_imported",
                "attempted_stage": attempted_stage,
                "batch_name": result.get("batch_name"),
                **publish_result,
            }
        )

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
        publish_applied_review_state(next_stage, result)
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
            publish_applied_review_state(
                str(partial_result.get("attempted_stage") or ""),
                partial_result,
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

    sweep_results = sweep_actionable_batches(
        root=root,
        workspace=workspace,
        options=options,
        max_stages=max(1, options.max_stages),
        dry_run=options.dry_run,
        on_stage_result=publish_applied_review_state,
    )
    for sweep_result in sweep_results:
        stage_results.append(sweep_result)
        if sweep_result.get("advanced") or sweep_result.get("stage_changed"):
            changed = True
            close_results.extend(
                close_issues_after_stage(
                    root=root,
                    repo=options.repo,
                    attempted_stage=str(sweep_result.get("attempted_stage") or ""),
                    result=sweep_result,
                    enabled=options.close_issues,
                )
            )

    final_status = workspace.status(options.track_name)
    document_queue_summary = aggregate_document_queue_summary(
        root=root,
        workspace=workspace,
        track_name=options.track_name,
    )
    refill_plan = build_bulk_review_refill_plan(
        document_queue_summary=document_queue_summary,
        target_ready_docs=options.bulk_review_target_ready_docs,
        pass_limit=options.refill_pass_limit,
        next_track_doc_seq=workspace.next_track_doc_seq(options.track_name),
        aligned_batch_size=options.refill_aligned_batch_size,
    )
    if int(refill_plan.get("planned_prepare_documents") or 0) > 0:
        refill_plan["source_selection"] = workspace.preview_next_source_documents(
            track_name=options.track_name,
            target_documents=int(refill_plan["planned_prepare_documents"]),
        )
    # Refill is intentionally executed by the independent refill worker.  The
    # latency-sensitive Issue synchronizer only reports demand.

    if not options.dry_run:
        finalized_correction_result = sync_finalized_corrections(
            root=root,
            repo=options.repo,
            track_name=options.track_name,
            close_issues=options.close_issues,
        )
        if finalized_correction_result.get("changed"):
            changed = True
        close_results.extend(finalized_correction_result.get("close_results") or [])
        already_closed = {
            int(row["issue_number"])
            for row in close_results
            if isinstance(row, dict) and row.get("issue_number")
        }
        close_results.extend(
            reconcile_applied_final_review_issues(
                root=root,
                repo=options.repo,
                enabled=options.close_issues,
                exclude_issue_numbers=already_closed,
            )
        )

    completed_at = now_iso()
    runtime_status_result: dict[str, Any] | None = None
    if not options.dry_run:
        runtime_status_result = update_runtime_status(
            root=root,
            options=options,
            started_at_epoch=started_at_epoch,
            completed_at_epoch=int(time.time()),
            final_status=final_status,
            document_queue_summary=document_queue_summary,
            workflow_changed=changed,
        )
        if runtime_status_result["publish_required"]:
            changed = True

    site_stale = False
    if not options.dry_run and options.publish_mode != PUBLISH_MODE_NONE:
        site_stale = review_site_needs_publish(root, options.track_name)
        if site_stale:
            changed = True

    publish_result: dict[str, Any] | None = None
    if changed and options.publish_mode != PUBLISH_MODE_NONE and not options.dry_run:
        publish_result = publish_review_artifacts(
            root,
            push_gh_pages=options.publish_mode == PUBLISH_MODE_GH_PAGES,
        )

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
            "refill_aligned_batch_size": options.refill_aligned_batch_size,
        },
        "refill_plan": refill_plan,
        "finalized_correction_result": finalized_correction_result,
        "changed": changed,
        "site_stale": site_stale,
        "stage_results": stage_results,
        "sweep_results": sweep_results,
        "refill_results": refill_results,
        "close_results": close_results,
        "publish_result": publish_result,
        "intermediate_publish_results": intermediate_publish_results,
        "runtime_status_result": runtime_status_result,
        "dry_run_plan": dry_run_plan,
        "final_status": final_status,
        "document_queue_summary": document_queue_summary,
    }


def sync_finalized_corrections(
    *,
    root: Path,
    repo: str,
    track_name: str,
    close_issues: bool,
) -> dict[str, Any]:
    submission_store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
    state_dir = root / "data" / "state" / FINALIZED_CORRECTION_STAGE
    import_summary_path = state_dir / "last_review_inbox_import_summary.json"
    apply_summary_path = state_dir / "last_apply_summary.json"
    import_summary = import_open_issue_inbox(
        repo=repo,
        review_pack_root=root / "data" / "review_packs",
        submission_store_dir=submission_store_dir,
        review_stage=FINALIZED_CORRECTION_STAGE,
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    import_summary_path.write_text(
        json.dumps(import_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    apply_summary = apply_finalized_correction_submissions_file(
        root=root,
        submission_store_dir=submission_store_dir,
        track_name=track_name,
        summary_json=apply_summary_path,
    )
    changed = int(apply_summary.get("applied_count") or 0) > 0
    close_results = close_finalized_correction_issues(
        repo=repo,
        import_summary=import_summary,
        apply_summary=apply_summary,
        enabled=close_issues,
    )
    return {
        "review_stage": FINALIZED_CORRECTION_STAGE,
        "changed": changed,
        "import_summary_json": str(import_summary_path),
        "apply_summary_json": str(apply_summary_path),
        "import_summary": import_summary,
        "apply_summary": apply_summary,
        "close_results": close_results,
    }


def close_finalized_correction_issues(
    *,
    repo: str,
    import_summary: dict[str, Any],
    apply_summary: dict[str, Any],
    enabled: bool,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    applied_submission_ids = finalized_correction_applied_submission_ids(apply_summary)
    problematic_submission_ids = finalized_correction_problematic_submission_ids(apply_summary)
    imported_by_issue: dict[int, set[str]] = {}
    problematic_issues: set[int] = set()
    for row in import_summary.get("summaries") or []:
        if not isinstance(row, dict):
            continue
        issue_number = issue_number_from_source(row.get("source"))
        submission_id = str(row.get("submission_id") or "")
        if issue_number and submission_id:
            imported_by_issue.setdefault(issue_number, set()).add(submission_id)
    for row in import_summary.get("skipped") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("reason") or "") == "duplicate_submission_id":
            continue
        issue_number = issue_number_from_source(row.get("source"))
        if issue_number:
            problematic_issues.add(issue_number)
    closable: list[int] = []
    for issue_number, submission_ids in imported_by_issue.items():
        if issue_number in problematic_issues:
            continue
        if submission_ids & problematic_submission_ids:
            continue
        if submission_ids and submission_ids <= applied_submission_ids:
            closable.append(issue_number)
    return [close_github_issue(repo=repo, issue_number=number) for number in sorted(closable)]


def reconcile_applied_final_review_issues(
    *,
    root: Path,
    repo: str,
    enabled: bool,
    exclude_issue_numbers: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Close Issues whose imported submissions were applied before an interruption."""
    if not enabled:
        return []
    import_summary_path = root / "data" / "state" / "yomi_final" / "last_review_inbox_import_summary.json"
    if not import_summary_path.exists():
        return []
    import_summary = read_json(import_summary_path)
    submissions_by_issue: dict[int, set[str]] = {}
    problematic_issues: set[int] = set()
    for row in import_summary.get("summaries") or []:
        if not isinstance(row, dict):
            continue
        issue_number = issue_number_from_source(row.get("source"))
        stored_path = row.get("stored_path")
        if issue_number and stored_path:
            submissions_by_issue.setdefault(issue_number, set()).add(
                str(Path(str(stored_path)).resolve())
            )
    for row in import_summary.get("skipped") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("reason") or "") == "duplicate_submission_id":
            continue
        issue_number = issue_number_from_source(row.get("source"))
        if issue_number:
            problematic_issues.add(issue_number)

    applied_paths: set[str] = set()
    for summary_path in (root / "data" / "units").glob("*/final_review_apply_summary.json"):
        try:
            apply_summary = read_json(summary_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for submission_path in apply_summary.get("submission_paths") or []:
            applied_paths.add(str(Path(str(submission_path)).resolve()))

    excluded = exclude_issue_numbers or set()
    closable = [
        issue_number
        for issue_number, paths in submissions_by_issue.items()
        if issue_number not in excluded
        and issue_number not in problematic_issues
        and paths
        and paths <= applied_paths
    ]
    return [close_github_issue(repo=repo, issue_number=number) for number in sorted(closable)]


def finalized_correction_applied_submission_ids(apply_summary: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for batch in apply_summary.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        for row in (batch.get("applied") or []) + (batch.get("accepted") or []):
            if isinstance(row, dict) and row.get("submission_id"):
                ids.add(str(row["submission_id"]))
    return ids


def finalized_correction_problematic_submission_ids(apply_summary: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in apply_summary.get("pre_group_skipped") or []:
        if isinstance(row, dict) and row.get("submission_id"):
            ids.add(str(row["submission_id"]))
    for batch in apply_summary.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        for row in batch.get("skipped") or []:
            if isinstance(row, dict) and row.get("submission_id"):
                ids.add(str(row["submission_id"]))
    return ids


def sweep_actionable_batches(
    *,
    root: Path,
    workspace: PipelineWorkspace,
    options: ReviewSyncOptions,
    max_stages: int,
    dry_run: bool = False,
    on_stage_result: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    track_state = workspace.load_track_state(options.track_name)
    current_batch_name = track_state.current_batch_name
    for batch_name in list_track_batches(workspace, options.track_name):
        if batch_name == current_batch_name:
            continue
        for _ in range(max_stages):
            batch_state = workspace.load_batch_state(batch_name)
            if batch_state.current_stage == STAGE_YOMI_FINALIZED:
                break
            if not dry_run:
                before_partial_fingerprint = review_sync_fingerprint(
                    root=root,
                    batch_name=batch_name,
                )
                partial_results = maintain_strong_repair_for_reviewed_documents(
                    root=root,
                    workspace=workspace,
                    batch_name=batch_name,
                    allow_queue=False,
                )
                after_partial_fingerprint = review_sync_fingerprint(
                    root=root,
                    batch_name=batch_name,
                )
                for partial_result in partial_results:
                    partial_result["partial_document_workflow"] = True
                    partial_result["sweep_batch"] = True
                    partial_result["stage_changed"] = (
                        before_partial_fingerprint != after_partial_fingerprint
                    )
                    results.append(partial_result)
                    if on_stage_result is not None:
                        on_stage_result(
                            str(partial_result.get("attempted_stage") or ""),
                            partial_result,
                        )
                batch_state = workspace.load_batch_state(batch_name)
            next_stage = workspace._next_stage_name(batch_state.current_stage)
            if not next_stage:
                break
            if not should_run_stage(root=root, batch_name=batch_name, next_stage=next_stage):
                break
            if dry_run:
                results.append(
                    {
                        "track_name": options.track_name,
                        "batch_name": batch_name,
                        "advanced": False,
                        "current_stage": batch_state.current_stage,
                        "next_stage": next_stage,
                        "attempted_stage": next_stage,
                        "stage_changed": False,
                        "sweep_batch": True,
                        "dry_run": True,
                    }
                )
                break
            before_fingerprint = review_sync_fingerprint(root=root, batch_name=batch_name)
            result = workspace.advance_batch(batch_name)
            after_fingerprint = review_sync_fingerprint(root=root, batch_name=batch_name)
            stage_changed = before_fingerprint != after_fingerprint
            result["attempted_stage"] = next_stage
            result["stage_changed"] = stage_changed
            result["sweep_batch"] = True
            results.append(result)
            if on_stage_result is not None:
                on_stage_result(next_stage, result)
            if not result.get("advanced"):
                break
            if result.get("blocking_reason"):
                break
    return results


def list_track_batches(workspace: PipelineWorkspace, track_name: str) -> list[str]:
    rows: list[tuple[str, str]] = []
    batches_root = workspace.batches_root()
    if not batches_root.exists():
        return []
    for path in sorted(batches_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("track_name")) != track_name:
            continue
        batch_name = str(payload.get("batch_name") or path.stem)
        updated_at = str(payload.get("updated_at") or "")
        rows.append((batch_name, updated_at))
    return [batch_name for batch_name, _ in sorted(rows)]


def build_bulk_review_refill_plan(
    *,
    document_queue_summary: dict[str, Any] | None,
    target_ready_docs: int,
    pass_limit: int,
    next_track_doc_seq: int | None = None,
    aligned_batch_size: int = 0,
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
    alignment = max(0, int(aligned_batch_size or 0))
    if alignment > 0 and limit > 0 and alignment > limit:
        raise ValueError("aligned_batch_size must not exceed pass_limit")
    planned = min(deficit, limit) if limit > 0 else 0
    alignment_bridge = False
    if planned > 0 and alignment > 0 and next_track_doc_seq is not None:
        next_seq = max(1, int(next_track_doc_seq))
        planned = alignment - ((next_seq - 1) % alignment)
        alignment_bridge = planned < alignment
    return {
        "enabled": target > 0,
        "status": "disabled" if target <= 0 else ("needs_refill" if deficit > 0 else "satisfied"),
        "bulk_review_ready_docs": ready,
        "target_ready_docs": target,
        "deficit": deficit,
        "pass_limit": limit,
        "aligned_batch_size": alignment,
        "next_track_doc_seq": next_track_doc_seq,
        "alignment_bridge": alignment_bridge,
        "planned_prepare_documents": planned,
        "will_prepare": False,
        "reason": (
            "dry-run-safe plan; non-dry-run sync can prepare and advance these documents"
            if target > 0 and planned > 0
            else None
        ),
    }


def should_run_stage(*, root: Path, batch_name: str, next_stage: str) -> bool:
    if next_stage in SYNC_STAGE_ALLOWLIST:
        return True
    if next_stage == STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED:
        if strong_repair_apply_confirmed(root=root, batch_name=batch_name):
            return True
        queue_jsonl = root / "data" / "units" / batch_name / "yomi_strong_repair_queue.jsonl"
        return count_nonempty_lines(queue_jsonl) == 0
    return False


def strong_repair_apply_confirmed(*, root: Path, batch_name: str) -> bool:
    batch_dir = root / "data" / "units" / batch_name
    queue_jsonl = batch_dir / "yomi_strong_repair_queue.jsonl"
    apply_summary_json = batch_dir / "yomi_strong_repair_apply_summary.json"
    if not apply_summary_json.exists():
        return False
    try:
        summary = read_json(apply_summary_json)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not summary.get("confirmed"):
        return False
    try:
        queued_items = int(summary.get("queued_items") or 0)
    except (TypeError, ValueError):
        return False
    return queued_items == count_nonempty_lines(queue_jsonl)


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


def review_submission_was_imported(
    *,
    root: Path,
    attempted_stage: str,
    result: dict[str, Any],
) -> bool:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return False
    summary_key = {
        STAGE_FINAL_REVIEW_APPLIED: "final_review_issue_import_summary_json",
        STAGE_YOMI_FINALIZED: "yomi_strong_repair_review_issue_import_summary_json",
    }.get(attempted_stage)
    if summary_key is None:
        return False
    summary_path_raw = artifacts.get(summary_key)
    if not summary_path_raw:
        return False
    summary_path = Path(str(summary_path_raw))
    if not summary_path.is_absolute():
        summary_path = root / summary_path
    try:
        summary = read_json(summary_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(summary.get("summaries"))


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


def aggregate_document_queue_summary(
    *,
    root: Path,
    workspace: PipelineWorkspace,
    track_name: str,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    missing_state_batches: list[str] = []
    for batch_name in list_track_batches(workspace, track_name):
        try:
            batch_state = workspace.load_batch_state(batch_name)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            missing_state_batches.append(batch_name)
            continue
        if batch_state.current_stage == STAGE_YOMI_FINALIZED:
            continue
        summary = current_document_queue_summary(root=root, batch_name=batch_name)
        if summary is None:
            missing_state_batches.append(batch_name)
            continue
        summaries.append(summary)

    def sum_counts(key: str) -> dict[str, int]:
        names = sorted(
            {
                str(name)
                for summary in summaries
                for name in (summary.get(key) or {}).keys()
            }
        )
        return {
            name: sum(int((summary.get(key) or {}).get(name) or 0) for summary in summaries)
            for name in names
        }

    return {
        "schema_version": 1,
        "track_name": track_name,
        "scope": "all_unfinished_batches",
        "batch_count": len(summaries),
        "batch_names": [str(summary.get("batch_name") or "") for summary in summaries],
        "missing_state_batches": missing_state_batches,
        "document_count": sum(int(summary.get("document_count") or 0) for summary in summaries),
        "state_counts": sum_counts("state_counts"),
        "queue_counts": sum_counts("queue_counts"),
        "pool_counts": sum_counts("pool_counts"),
    }


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
        root
        / "data"
        / "units"
        / batch_name
        / "yomi_strong_repair_effective_results.jsonl",
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


def runtime_status_path(root: Path, track_name: str) -> Path:
    return root / "data" / "state" / "review_sync" / f"{track_name}.runtime_status.json"


def update_runtime_status(
    *,
    root: Path,
    options: ReviewSyncOptions,
    started_at_epoch: int,
    completed_at_epoch: int,
    final_status: dict[str, Any],
    document_queue_summary: dict[str, Any],
    workflow_changed: bool,
    error_message: str | None = None,
) -> dict[str, Any]:
    path = runtime_status_path(root, options.track_name)
    previous: dict[str, Any] = {}
    if path.exists():
        try:
            previous = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            previous = {}

    interval = max(1, int(options.runtime_status_interval_seconds))
    grace = max(0, int(options.runtime_status_grace_seconds))
    previous_schedule = previous.get("schedule") if isinstance(previous.get("schedule"), dict) else {}
    schedule_changed = (
        int(previous_schedule.get("interval_seconds") or 0) != interval
        or int(previous_schedule.get("grace_seconds") or 0) != grace
    )
    anchor = int(previous_schedule.get("anchor_epoch") or started_at_epoch)
    if schedule_changed:
        anchor = started_at_epoch
    elapsed = max(0, started_at_epoch - anchor)
    slot_number = int(round(elapsed / interval))
    expected_start = anchor + slot_number * interval
    drift_seconds = abs(started_at_epoch - expected_start)
    published_anchor = started_at_epoch if drift_seconds > grace else anchor

    queue_counts = document_queue_summary.get("queue_counts")
    if not isinstance(queue_counts, dict):
        queue_counts = {}
    pool_counts = document_queue_summary.get("pool_counts")
    if not isinstance(pool_counts, dict):
        pool_counts = {}
    actionable = int(queue_counts.get("bulk_review_selectable") or 0) + int(
        queue_counts.get("escalated_repair_selectable") or 0
    )
    status = "error" if error_message else "waiting_for_review" if actionable else "idle"
    state_payload = {
        "current_batch_name": final_status.get("current_batch_name")
        or final_status.get("batch_name"),
        "current_stage": final_status.get("current_stage"),
        "next_stage": final_status.get("next_stage"),
        "active_queue_count": actionable,
        "queue_counts": queue_counts,
        "pool_counts": pool_counts,
    }
    semantic_payload = {
        "status": status,
        "schedule": {
            "anchor_epoch": published_anchor,
            "interval_seconds": interval,
            "grace_seconds": grace,
        },
        "state": state_payload,
        "message": error_message or "",
    }
    previous_semantic = {
        "status": previous.get("status"),
        "schedule": {
            "anchor_epoch": previous_schedule.get("anchor_epoch"),
            "interval_seconds": previous_schedule.get("interval_seconds"),
            "grace_seconds": previous_schedule.get("grace_seconds"),
        },
        "state": previous.get("state"),
        "message": previous.get("message") or "",
    }
    publish_required = (
        not previous
        or workflow_changed
        or schedule_changed
        or semantic_payload != previous_semantic
        or drift_seconds > grace
        or bool(error_message)
    )
    if not publish_required:
        return {
            "path": str(path),
            "publish_required": False,
            "state_revision": int(previous.get("state_revision") or 0),
            "drift_seconds": drift_seconds,
        }

    revision = int(previous.get("state_revision") or 0) + 1
    payload = {
        "schema_version": RUNTIME_STATUS_SCHEMA_VERSION,
        "track_name": options.track_name,
        "state_revision": revision,
        "generated_at": datetime.fromtimestamp(completed_at_epoch, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "generated_at_epoch": completed_at_epoch,
        "last_successful_sync": previous.get("last_successful_sync")
        if error_message
        else datetime.fromtimestamp(completed_at_epoch, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "last_successful_sync_epoch": previous.get("last_successful_sync_epoch")
        if error_message
        else completed_at_epoch,
        **semantic_payload,
        "schedule": {
            **semantic_payload["schedule"],
            "next_expected_epoch": published_anchor + interval,
        },
        "observed": {
            "started_at_epoch": started_at_epoch,
            "completed_at_epoch": completed_at_epoch,
            "drift_seconds": drift_seconds,
        },
        "client_polling": {
            "normal_seconds": max(1, int(options.runtime_status_normal_poll_seconds)),
            "near_seconds": max(1, int(options.runtime_status_near_poll_seconds)),
            "hidden_seconds": max(1, int(options.runtime_status_hidden_poll_seconds)),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "publish_required": True,
        "state_revision": revision,
        "drift_seconds": drift_seconds,
    }


def review_site_needs_publish(root: Path, track_name: str = "dev") -> bool:
    docs_pack_dir = root / "docs" / "review" / "packs"
    docs_review_dir = root / "docs" / "review"
    web_review_dir = root / "web" / "review"
    entries = collect_review_pack_entries(root / "data" / "review_packs")
    if entries and not (docs_review_dir / "manifest.json").exists():
        return True
    for source_path in web_review_dir.rglob("*") if web_review_dir.exists() else []:
        if source_path.is_file():
            # Publication adds content-hash query strings to the generated index.
            if source_path.relative_to(web_review_dir) == Path("index.html"):
                continue
            destination = docs_review_dir / source_path.relative_to(web_review_dir)
            if file_fingerprint(source_path) != file_fingerprint(destination):
                return True
    runtime_source = runtime_status_path(root, track_name)
    runtime_destination = docs_review_dir / "runtime-status.json"
    if file_fingerprint(runtime_source) != file_fingerprint(runtime_destination):
        return True
    acknowledgment_source = (
        root / "data" / "state" / "issue_watch" / f"{track_name}.acknowledgments.json"
    )
    acknowledgment_destination = docs_review_dir / "issue-acknowledgments.json"
    if file_fingerprint(acknowledgment_source) != file_fingerprint(acknowledgment_destination):
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
    failed_submission_ids: set[str] = set()
    if attempted_stage == STAGE_YOMI_FINALIZED:
        apply_summary_raw = artifacts.get("yomi_strong_repair_review_apply_summary_json")
        if apply_summary_raw:
            apply_summary_path = Path(str(apply_summary_raw))
            if not apply_summary_path.is_absolute():
                apply_summary_path = root / apply_summary_path
            if apply_summary_path.exists():
                apply_summary = read_json(apply_summary_path)
                failed_submission_ids = {
                    str(row.get("submission_id") or "")
                    for row in apply_summary.get("manual_segment_overrides", {}).get("invalid", [])
                    if isinstance(row, dict) and row.get("submission_id")
                }
    issue_numbers = closable_issue_numbers(
        import_summary,
        failed_submission_ids=failed_submission_ids,
    )
    return [close_github_issue(repo=repo, issue_number=number) for number in issue_numbers]


def closable_issue_numbers(
    import_summary: dict[str, Any],
    *,
    failed_submission_ids: set[str] | None = None,
) -> list[int]:
    if import_summary.get("status") not in {"ok", None}:
        return []
    imported: dict[int, set[str]] = {}
    problematic: set[int] = set()
    for row in import_summary.get("summaries") or []:
        issue_number = issue_number_from_source(row.get("source"))
        if issue_number:
            imported.setdefault(issue_number, set()).add(str(row.get("submission_id") or ""))
    for row in import_summary.get("skipped") or []:
        reason = str(row.get("reason") or "")
        if reason == "duplicate_submission_id":
            issue_number = issue_number_from_source(row.get("source"))
            if issue_number:
                imported.setdefault(issue_number, set()).add(
                    str(row.get("submission_id") or "")
                )
            continue
        issue_number = issue_number_from_source(row.get("source"))
        if issue_number:
            problematic.add(issue_number)
    failed = failed_submission_ids or set()
    return sorted(
        issue_number
        for issue_number, submission_ids in imported.items()
        if issue_number not in problematic and not (submission_ids & failed)
    )


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
        project_root=root,
    )
    result: dict[str, Any] = {
        "status": "generated",
        "manifest_json": str(root / "docs" / "review" / "manifest.json"),
        "active_queue_count": len(manifest.get("current_review_queues", [])),
    }
    if not push_gh_pages:
        return result
    completed = subprocess.run(
        [str(root / "publish-review"), "--no-generate"],
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
                "batch_name": row.get("batch_name"),
                "advanced": row.get("advanced"),
                "stage_changed": row.get("stage_changed"),
                "blocking_reason": row.get("blocking_reason"),
            }
            for row in summary.get("stage_results", [])
            if isinstance(row, dict)
        ],
        "sweep": [
            {
                "batch_name": row.get("batch_name"),
                "attempted_stage": row.get("attempted_stage"),
                "advanced": row.get("advanced"),
                "stage_changed": row.get("stage_changed"),
                "blocking_reason": row.get("blocking_reason"),
            }
            for row in summary.get("sweep_results", [])
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
