from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import (
    STAGE_FINAL_REVIEW_APPLIED,
    STAGE_YOMI_FINALIZED,
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    PipelineWorkspace,
)
from yomi_corpus.review_site import publish_review_site
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

        result = workspace.advance(track_name=options.track_name)
        result["attempted_stage"] = next_stage
        stage_results.append(result)
        if result.get("advanced"):
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
        if not result.get("advanced"):
            break
        if result.get("blocking_reason"):
            break

    publish_result: dict[str, Any] | None = None
    if changed and options.publish_mode != PUBLISH_MODE_NONE and not options.dry_run:
        publish_result = publish_review_artifacts(
            root,
            push_gh_pages=options.publish_mode == PUBLISH_MODE_GH_PAGES,
        )

    completed_at = now_iso()
    final_status = workspace.status(options.track_name)
    return {
        "schema_version": 1,
        "track_name": options.track_name,
        "repo": options.repo,
        "pages_url": options.pages_url,
        "publish_mode": options.publish_mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "dry_run": options.dry_run,
        "changed": changed,
        "stage_results": stage_results,
        "close_results": close_results,
        "publish_result": publish_result,
        "dry_run_plan": dry_run_plan,
        "final_status": final_status,
    }


def should_run_stage(*, root: Path, batch_name: str, next_stage: str) -> bool:
    if next_stage in SYNC_STAGE_ALLOWLIST:
        return True
    if next_stage == STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED:
        queue_jsonl = root / "data" / "units" / batch_name / "yomi_strong_repair_queue.jsonl"
        return count_nonempty_lines(queue_jsonl) == 0
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
    return {
        "track_name": summary.get("track_name"),
        "changed": summary.get("changed"),
        "stages": [
            {
                "attempted_stage": row.get("attempted_stage"),
                "advanced": row.get("advanced"),
                "blocking_reason": row.get("blocking_reason"),
            }
            for row in summary.get("stage_results", [])
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
