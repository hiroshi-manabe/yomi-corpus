"""Independent, low-priority preparation and resumable Batch collection."""

from __future__ import annotations

from dataclasses import asdict
import fcntl
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import uuid

from yomi_corpus.llm.backend import (
    extract_output_text_from_batch_item,
    extract_usage_from_batch_item,
    tool_calls_from_batch_item,
)
from yomi_corpus.llm.config import apply_llm_profile, load_llm_task_config
from yomi_corpus.llm.credentials import resolve_openai_api_key
from yomi_corpus.llm.response_cache import (
    ResponseCache,
    encode,
    load_cache_config,
    request_identity,
)
from yomi_corpus.llm.schemas import LLMTaskConfig, PromptItem
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows
from yomi_corpus.pipeline import PipelineWorkspace, default_llm_policy
from yomi_corpus.review_sync import (
    aggregate_document_queue_summary,
    load_review_sync_config,
)

TERMINAL = {"completed", "failed", "expired", "cancelled"}


def active_jobs(cache):
    return cache.active_jobs()


def upcoming(workspace, track, horizon):
    store = workspace.processing_order_store(track)
    manifest = store.load_manifest()
    cursor = int(manifest["cursor"])
    count = min(horizon, max(0, int(manifest["document_count"]) - cursor + 1))
    return manifest, store.read_slots(cursor, count) if count else []


def source_key(manifest, line):
    return f"{manifest['source_content_sha256']}:{line}"


def priority_reason(root, track, workspace):
    lock_path = root / "data/state/refill" / f"{track}.lock"
    # ReviewSyncLock uses exclusive file creation, not flock. Never create or
    # remove its sentinel here; the owning worker handles stale lock recovery.
    if lock_path.exists():
        return "refill_active"
    counts = aggregate_document_queue_summary(
        root=root, workspace=workspace, track_name=track
    )
    target = load_review_sync_config(track).bulk_review_target_ready_docs
    if counts["pool_counts"].get("bulk-ready", 0) < target:
        return "ready_pool_below_target"
    return None


class BatchClient:
    def __init__(self):
        from openai import OpenAI

        key, _ = resolve_openai_api_key()
        self.client = OpenAI(api_key=key, timeout=60, max_retries=0)

    def upload(self, path):
        with path.open("rb") as handle:
            return self.client.files.create(file=handle, purpose="batch").id

    def submit(self, job):
        return self.client.batches.create(
            input_file_id=job["input_file_id"],
            endpoint=job["endpoint"],
            completion_window=job["completion_window"],
            metadata={"yomi_speculative_job": job["id"]},
        ).model_dump()

    def reconcile(self, job):
        # An unresolved outcome is never blindly resubmitted.
        for index, batch in enumerate(self.client.batches.list(limit=100)):
            if (batch.metadata or {}).get("yomi_speculative_job") == job["id"]:
                return batch.model_dump()
            if index >= 999:
                break
        return None

    def retrieve(self, identifier):
        return self.client.batches.retrieve(identifier).model_dump()

    def download(self, identifier):
        return self.client.files.content(identifier).text


def import_batch_output(cache, job, text):
    task = LLMTaskConfig(**job["task"])
    for line in text.split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        key = row.get("custom_id")
        if key not in job["items"]:
            raise ValueError(f"Unknown Batch item: {key}")
        item = PromptItem(**job["items"][key])
        body = (row.get("response") or {}).get("body") or {}
        snapshot = {
            "status": body.get("status"),
            "response_id": body.get("id"),
            "raw_text": extract_output_text_from_batch_item(row),
            "usage": extract_usage_from_batch_item(row),
            "tool_calls": tool_calls_from_batch_item(row),
        }
        valid = False
        error = None
        try:
            if (
                row.get("error")
                or (row.get("response") or {}).get("status_code") != 200
            ):
                raise ValueError(str(row.get("error") or "Non-200 Batch response"))
            cache.store(task, item, snapshot, job["id"])
            valid = True
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)
        cache.save_attempt(
            f"{job['id']}:{key}",
            key,
            {
                "job_id": job["id"],
                "key": key,
                "model": task.model,
                "processing_tier": "batch",
                "valid": valid,
                "error": error,
                "snapshot": snapshot,
                "remote_error": row.get("error"),
            },
        )


def collect(cache, client):
    for job in active_jobs(cache):
        try:
            if job["state"] == "prepared":
                continue
            remote = (
                client.retrieve(job["batch_id"])
                if job.get("batch_id")
                else client.reconcile(job)
            )
            if remote is None:
                job["error"] = (
                    "Submission outcome unknown; reconciliation pending. No automatic resubmission."
                )
                cache.save_job(job)
                continue
            job.update(
                batch_id=remote["id"],
                remote_status=remote["status"],
                updated=time.time(),
            )
            if remote["status"] in TERMINAL:
                for key in ("output_file_id", "error_file_id"):
                    if remote.get(key):
                        import_batch_output(cache, job, client.download(remote[key]))
                job["state"] = "fetched"
                job["remote_errors"] = remote.get("errors")
            else:
                job["state"] = "submitted"
            job.pop("error", None)
            cache.save_job(job)
        except Exception as exc:
            job["error"] = str(exc)
            cache.save_job(job)


def submit_prepared(cache, client, job, directory):
    path = directory / f"{job['id']}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        task = LLMTaskConfig(**job["task"])
        for key, raw_item in job["items"].items():
            _, canonical = request_identity(task, PromptItem(**raw_item))
            handle.write(
                encode(
                    {
                        "custom_id": key,
                        "method": "POST",
                        "url": job["endpoint"],
                        "body": json.loads(canonical)["body"],
                    }
                )
                + "\n"
            )
    if not job.get("input_file_id"):
        job["input_file_id"] = client.upload(path)
        cache.save_job(job)
    job["state"] = "submitting"
    cache.save_job(job)
    remote = client.submit(job)
    job.update(batch_id=remote["id"], state="submitted", remote_status=remote["status"])
    cache.save_job(job)
    path.unlink(missing_ok=True)


def prepare_child(root, track, lines, output):
    from yomi_corpus.mechanical_preflight import (
        MechanicalPreflightOptions,
        run_mechanical_preflight,
    )
    from yomi_corpus.queue_preflight import implementation_digest

    before = implementation_digest(root)
    queue = output.with_suffix(".queue.jsonl")
    report = run_mechanical_preflight(
        root,
        MechanicalPreflightOptions(
            track_name=track,
            target_documents=len(lines),
            source_line_nos=tuple(lines),
            queue_output_path=str(queue),
            workspace_parent_path=str(output.parent / "workspaces"),
        ),
    )
    if report["status"] != "passed":
        raise RuntimeError(
            f"Preparation failed: {report.get('error')}; {report['report_path']}"
        )
    if before != implementation_digest(root):
        raise RuntimeError(
            "Code/config changed during preparation; retry with a fresh snapshot"
        )
    policy = report["prepared"].get("llm_policy") or default_llm_policy(track)
    task = apply_llm_profile(
        load_llm_task_config("config/llm/yomi_reading.toml"), policy["yomi_reading"]
    )
    items = build_prompt_items(task, load_jsonl_rows(str(queue)))
    output.write_text(
        encode(
            {
                "task": asdict(task),
                "items": [asdict(item) for item in items],
                "report": report,
                "implementation_digest": before,
            }
        ),
        encoding="utf-8",
    )


def prepare_process(root, track, lines, output, timeout):
    # The service manager kills the entire control group on stop, including this child.
    subprocess.run(
        [
            sys.executable,
            str(root / "speculative-worker"),
            track,
            "--prepare-lines",
            ",".join(map(str, lines)),
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
        timeout=timeout,
    )
    return json.loads(output.read_text())


def run_pass(root, track, *, collect_only=False, chunks=None):
    config = load_cache_config(root, track)
    for key in (
        "horizon",
        "chunk_size",
        "max_chunks_per_pass",
        "max_pending_jobs",
        "max_requests_per_job",
        "preparation_timeout_seconds",
        "retention_days",
    ):
        if not isinstance(config.get(key), int) or config[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    cache = ResponseCache(root / config["database"])
    state_dir = root / "data/state/speculative" / track
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_name = "collector.lock" if collect_only else "preparation.lock"
    with (state_dir / lock_name).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"reason": "already_running"}
        if collect_only:
            if active_jobs(cache):
                collect(cache, BatchClient())
            cache.prune(int(config["retention_days"]))
            return cache.summary()
        if not config.get("submit_enabled"):
            return {"reason": "submission_disabled", **cache.summary()}
        # Only this worker owns these disposable trees; no paid jobs live here.
        for abandoned in state_dir.glob("prepare-*"):
            if abandoned.is_dir():
                shutil.rmtree(abandoned)
        workspace = PipelineWorkspace(root)
        reason = None
        for _ in range(
            chunks if chunks is not None else int(config["max_chunks_per_pass"])
        ):
            reason = priority_reason(root, track, workspace)
            if reason:
                break
            jobs = active_jobs(cache)
            manifest, lines = upcoming(workspace, track, int(config["horizon"]))
            eligible = {source_key(manifest, line) for line in lines}
            prepared = [job for job in jobs if job["state"] == "prepared"]
            for job in prepared:
                if not set(job["documents"]).issubset(eligible):
                    job["state"] = "abandoned"
                    cache.save_job(job)
                    cache.forget_job_documents(job)
                else:
                    submit_prepared(cache, BatchClient(), job, state_dir)
            if len(active_jobs(cache)) >= int(config["max_pending_jobs"]):
                reason = "pending_job_limit"
                break
            documents = {row["key"]: row for row in cache.records("documents")}
            done = {
                key
                for key, row in documents.items()
                if not row.get("error")
                or row.get("failures", 0) >= 3
                or row.get("retry_after", 0) > time.time()
            }
            selected = [
                line for line in lines if source_key(manifest, line) not in done
            ][: int(config["chunk_size"])]
            if not selected:
                reason = "horizon_prepared"
                break
            try:
                with tempfile.TemporaryDirectory(
                    prefix="prepare-", dir=state_dir
                ) as tmp:
                    payload = prepare_process(
                        root,
                        track,
                        selected,
                        Path(tmp) / "prepared.json",
                        int(config["preparation_timeout_seconds"]),
                    )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                for line in selected:
                    key = source_key(manifest, line)
                    cache.save_document(
                        key,
                        {
                            "key": key,
                            "error": str(exc),
                            "retry_after": time.time() + 3600,
                            "failures": documents.get(key, {}).get("failures", 0) + 1,
                        },
                    )
                print(
                    encode({"preparation_failed": selected, "error": str(exc)}),
                    flush=True,
                )
                continue
            current, current_lines = upcoming(workspace, track, int(config["horizon"]))
            if current["source_content_sha256"] != manifest[
                "source_content_sha256"
            ] or not set(selected).issubset(current_lines):
                reason = "queue_changed"
                break
            task = LLMTaskConfig(**payload["task"])
            pending_keys = {key for job in active_jobs(cache) for key in job["items"]}
            items = {}
            for raw_item in payload["items"]:
                item = PromptItem(**raw_item)
                key, _ = request_identity(task, item)
                if key not in pending_keys and not cache.has(key):
                    items[key] = raw_item
            keys = [source_key(manifest, line) for line in selected]
            if len(items) > int(config["max_requests_per_job"]):
                raise ValueError(
                    "Preparation chunk exceeds request limit; reduce chunk_size"
                )
            job = {
                "id": uuid.uuid4().hex,
                "state": "prepared",
                "task": payload["task"],
                "endpoint": task.batch_endpoint,
                "completion_window": task.batch_completion_window,
                "items": items,
                "documents": keys,
                "created": time.time(),
                "provenance": {
                    "order_generation": manifest["order_generation"],
                    "cursor": manifest["cursor"],
                    "source_lines": selected,
                    "implementation_digest": payload["implementation_digest"],
                    "report": payload["report"],
                },
            }
            # Job first, checkpoints second: a restart can safely deduplicate requests.
            if items:
                cache.save_job(job)
            for key in keys:
                cache.save_document(
                    key,
                    {
                        "key": key,
                        "created": time.time(),
                        "job_id": job["id"] if items else None,
                    },
                )
            if items:
                submit_prepared(cache, BatchClient(), job, state_dir)
            print(
                encode(
                    {
                        "prepared_documents": len(selected),
                        "new_requests": len(items),
                        "job": job["id"],
                    }
                ),
                flush=True,
            )
        return {"reason": reason or "pass_limit", **cache.summary()}
