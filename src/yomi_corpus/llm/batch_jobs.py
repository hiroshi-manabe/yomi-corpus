from __future__ import annotations

import json
from pathlib import Path
from time import time
from typing import Any

from yomi_corpus.llm.backend import (
    OpenAIResponsesBackend,
    extract_output_text_from_batch_item,
    extract_usage_from_batch_item,
    write_batch_requests,
)
from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.parsers import parse_output
from yomi_corpus.llm.schemas import LLMTaskConfig
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows
from yomi_corpus.paths import resolve_repo_path

ITEMS_FILENAME = "items.jsonl"
REQUESTS_FILENAME = "requests.jsonl"
REQUEST_CHUNKS_DIRNAME = "request_chunks"
MANIFEST_FILENAME = "manifest.json"
STATUS_FILENAME = "status.json"
RAW_RESULTS_FILENAME = "results.raw.jsonl"
PARSED_RESULTS_FILENAME = "results.parsed.jsonl"


def prepare_batch_job(
    task_config_path: str,
    input_jsonl_path: str,
    job_dir: str,
    *,
    task_config_override: LLMTaskConfig | None = None,
) -> None:
    task_config = task_config_override or load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    job_path = resolve_repo_path(job_dir)
    job_path.mkdir(parents=True, exist_ok=True)

    requests_path = job_path / REQUESTS_FILENAME
    request_chunks_dir = job_path / REQUEST_CHUNKS_DIRNAME
    items_path = job_path / ITEMS_FILENAME
    manifest_path = job_path / MANIFEST_FILENAME
    status_path = job_path / STATUS_FILENAME

    write_batch_requests(task_config, items, requests_path)
    remote_batches = _write_batch_request_chunks(task_config, items, request_chunks_dir)
    with items_path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(
                json.dumps(
                    {
                        "item_id": item.item_id,
                        "prompt": item.prompt,
                        "metadata": item.metadata,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    manifest = {
        "job_schema": 1,
        "task_config_path": str(task_config_path),
        "input_jsonl_path": str(input_jsonl_path),
        "task_name": task_config.task_name,
        "model": task_config.model,
        "input_builder": task_config.input_builder,
        "parser": task_config.parser,
        "batch_endpoint": task_config.batch_endpoint,
        "batch_completion_window": task_config.batch_completion_window,
        "batch_max_requests_per_batch": task_config.batch_max_requests_per_batch,
        "item_count": len(items),
        "remote_batch_count": len(remote_batches),
        "created_at_epoch": int(time()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    status = {
        "state": "prepared",
        "updated_at_epoch": int(time()),
        "batch_id": None,
        "input_file_id": None,
        "output_file_id": None,
        "error_file_id": None,
        "remote_status": None,
        "remote_batches": remote_batches,
    }
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def submit_batch_job(
    job_dir: str,
    backend: OpenAIResponsesBackend | None = None,
    *,
    api_key_file: str | None = None,
) -> dict[str, Any]:
    job_path = resolve_repo_path(job_dir)
    manifest = _load_json(job_path / MANIFEST_FILENAME)
    status = _load_json(job_path / STATUS_FILENAME)
    if status["state"] not in {"prepared", "running"}:
        raise ValueError(f"submit is only allowed from prepared/running state, got {status['state']}")

    backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    remote_batches = _status_remote_batches(status)
    for remote_batch in remote_batches:
        if remote_batch.get("batch_id"):
            continue
        remote = backend.submit_batch(
            job_path / str(remote_batch["requests_path"]),
            endpoint=str(manifest["batch_endpoint"]),
            completion_window=str(manifest["batch_completion_window"]),
        )
        remote_batch.update(
            {
                "state": _local_state_from_remote_status(str(remote["status"])),
                "batch_id": remote["batch_id"],
                "input_file_id": remote["input_file_id"],
                "output_file_id": remote.get("output_file_id"),
                "error_file_id": remote.get("error_file_id"),
                "remote_status": remote["status"],
                "submitted_at_epoch": remote.get("created_at"),
                "updated_at_epoch": int(time()),
            }
        )
        _sync_aggregate_batch_status(status, remote_batches)
        status["updated_at_epoch"] = int(time())
        _write_json(job_path / STATUS_FILENAME, status)
    _sync_aggregate_batch_status(status, remote_batches)
    status["updated_at_epoch"] = int(time())
    _write_json(job_path / STATUS_FILENAME, status)
    return status


def poll_batch_job(
    job_dir: str,
    backend: OpenAIResponsesBackend | None = None,
    *,
    api_key_file: str | None = None,
) -> dict[str, Any]:
    job_path = resolve_repo_path(job_dir)
    status = _load_json(job_path / STATUS_FILENAME)
    backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    remote_batches = _status_remote_batches(status)
    for remote_batch in remote_batches:
        batch_id = remote_batch.get("batch_id")
        if not batch_id:
            raise ValueError("No batch_id recorded for this job.")
        if remote_batch.get("state") in {"completed", "failed", "expired", "cancelled", "fetched"}:
            continue
        remote = backend.retrieve_batch(str(batch_id))
        remote_batch.update(
            {
                "state": _local_state_from_remote_status(str(remote["status"])),
                "updated_at_epoch": int(time()),
                "remote_status": remote["status"],
                "output_file_id": remote.get("output_file_id"),
                "error_file_id": remote.get("error_file_id"),
                "remote_snapshot": remote,
            }
        )
    _sync_aggregate_batch_status(status, remote_batches)
    status["updated_at_epoch"] = int(time())
    _write_json(job_path / STATUS_FILENAME, status)
    return status


def fetch_batch_job(
    job_dir: str,
    backend: OpenAIResponsesBackend | None = None,
    *,
    api_key_file: str | None = None,
) -> dict[str, Any]:
    job_path = resolve_repo_path(job_dir)
    manifest = _load_json(job_path / MANIFEST_FILENAME)
    status = _load_json(job_path / STATUS_FILENAME)
    remote_batches = _status_remote_batches(status)
    if any(batch.get("remote_status") != "completed" or not batch.get("output_file_id") for batch in remote_batches):
        raise ValueError("fetch requires all remote batches to be completed with output_file_id.")

    backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    raw_results_path = job_path / RAW_RESULTS_FILENAME
    parsed_results_path = job_path / PARSED_RESULTS_FILENAME

    items_by_id = _load_items_by_id(job_path / ITEMS_FILENAME)
    with raw_results_path.open("w", encoding="utf-8") as raw_dst, parsed_results_path.open(
        "w", encoding="utf-8"
    ) as parsed_dst:
        for remote_batch in remote_batches:
            chunk_raw_path = job_path / f"results.{remote_batch['chunk_id']}.raw.jsonl"
            backend.download_file(str(remote_batch["output_file_id"]), chunk_raw_path)
            remote_batch["raw_results_path"] = str(chunk_raw_path.relative_to(job_path))
            with chunk_raw_path.open(encoding="utf-8") as src:
                for line in src:
                    if not line.strip():
                        continue
                    raw_dst.write(line)
                    item = json.loads(line)
                    item_id = str(item["custom_id"])
                    raw_text = extract_output_text_from_batch_item(item) or ""
                    usage = extract_usage_from_batch_item(item)
                    parsed = None
                    parse_error = None
                    if raw_text:
                        try:
                            parsed = parse_output(raw_text, str(manifest["parser"]))
                        except Exception as exc:  # noqa: BLE001
                            parse_error = str(exc)
                    parsed_dst.write(
                        json.dumps(
                            {
                                "item_id": item_id,
                                "raw_text": raw_text,
                                "parsed": parsed,
                                "usage": usage,
                                "parse_error": parse_error,
                                "metadata": items_by_id.get(item_id, {}).get("metadata", {}),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            remote_batch["state"] = "fetched"

    status.update(
        {
            "state": "fetched",
            "updated_at_epoch": int(time()),
            "fetched_at_epoch": int(time()),
        }
    )
    _sync_aggregate_batch_status(status, remote_batches)
    status["state"] = "fetched"
    _write_json(job_path / STATUS_FILENAME, status)
    return status


def list_batch_jobs(root_dir: str) -> list[dict[str, Any]]:
    root_path = resolve_repo_path(root_dir)
    jobs: list[dict[str, Any]] = []
    if not root_path.exists():
        return jobs

    for status_path in sorted(root_path.rglob(STATUS_FILENAME)):
        job_dir = status_path.parent
        manifest_path = job_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            continue
        status = _load_json(status_path)
        manifest = _load_json(manifest_path)
        jobs.append(
            {
                "job_dir": str(job_dir),
                "task_name": manifest.get("task_name"),
                "model": manifest.get("model"),
                "item_count": manifest.get("item_count"),
                "state": status.get("state"),
                "remote_status": status.get("remote_status"),
                "batch_id": status.get("batch_id"),
            }
        )
    return jobs


def _load_items_by_id(path: Path) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            items[str(payload["item_id"])] = payload
    return items


def _write_batch_request_chunks(
    task_config: LLMTaskConfig,
    items: list[Any],
    output_dir: Path,
) -> list[dict[str, Any]]:
    if task_config.batch_max_requests_per_batch <= 0:
        raise ValueError("batch_max_requests_per_batch must be positive.")
    output_dir.mkdir(parents=True, exist_ok=True)
    remote_batches: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(items), task_config.batch_max_requests_per_batch), start=1):
        chunk_items = items[start : start + task_config.batch_max_requests_per_batch]
        chunk_id = f"batch_{index:04d}"
        requests_path = output_dir / f"{chunk_id}.requests.jsonl"
        write_batch_requests(task_config, chunk_items, requests_path)
        remote_batches.append(
            {
                "chunk_id": chunk_id,
                "state": "prepared",
                "requests_path": str(requests_path.parent.name + "/" + requests_path.name),
                "item_count": len(chunk_items),
                "item_ids": [item.item_id for item in chunk_items],
                "batch_id": None,
                "input_file_id": None,
                "output_file_id": None,
                "error_file_id": None,
                "remote_status": None,
            }
        )
    if not remote_batches:
        requests_path = output_dir / "batch_0001.requests.jsonl"
        requests_path.write_text("", encoding="utf-8")
        remote_batches.append(
            {
                "chunk_id": "batch_0001",
                "state": "prepared",
                "requests_path": str(requests_path.parent.name + "/" + requests_path.name),
                "item_count": 0,
                "item_ids": [],
                "batch_id": None,
                "input_file_id": None,
                "output_file_id": None,
                "error_file_id": None,
                "remote_status": None,
            }
        )
    return remote_batches


def _status_remote_batches(status: dict[str, Any]) -> list[dict[str, Any]]:
    remote_batches = status.get("remote_batches")
    if isinstance(remote_batches, list) and all(isinstance(batch, dict) for batch in remote_batches):
        return remote_batches
    return [
        {
            "chunk_id": "batch_0001",
            "state": status.get("state", "prepared"),
            "requests_path": REQUESTS_FILENAME,
            "item_count": None,
            "item_ids": [],
            "batch_id": status.get("batch_id"),
            "input_file_id": status.get("input_file_id"),
            "output_file_id": status.get("output_file_id"),
            "error_file_id": status.get("error_file_id"),
            "remote_status": status.get("remote_status"),
            "remote_snapshot": status.get("remote_snapshot"),
        }
    ]


def _sync_aggregate_batch_status(
    status: dict[str, Any],
    remote_batches: list[dict[str, Any]],
) -> None:
    status["remote_batches"] = remote_batches
    first = remote_batches[0] if remote_batches else {}
    status["batch_id"] = first.get("batch_id")
    status["input_file_id"] = first.get("input_file_id")
    status["output_file_id"] = first.get("output_file_id")
    status["error_file_id"] = first.get("error_file_id")
    status["remote_status"] = _aggregate_remote_status(remote_batches)
    status["state"] = _aggregate_local_state(remote_batches)
    status["remote_snapshot"] = {
        "request_counts": _aggregate_request_counts(remote_batches),
        "usage": _aggregate_usage(remote_batches),
        "remote_batches": [
            {
                "chunk_id": batch.get("chunk_id"),
                "batch_id": batch.get("batch_id"),
                "state": batch.get("state"),
                "remote_status": batch.get("remote_status"),
                "request_counts": (batch.get("remote_snapshot") or {}).get("request_counts")
                if isinstance(batch.get("remote_snapshot"), dict)
                else None,
            }
            for batch in remote_batches
        ],
    }


def _aggregate_local_state(remote_batches: list[dict[str, Any]]) -> str:
    states = {str(batch.get("state") or "prepared") for batch in remote_batches}
    if states == {"fetched"}:
        return "fetched"
    if states <= {"completed", "fetched"}:
        return "completed"
    if states & {"failed"}:
        return "failed"
    if states & {"expired"}:
        return "expired"
    if states & {"cancelled"}:
        return "cancelled"
    if states & {"running", "submitted"}:
        return "running"
    return "prepared"


def _aggregate_remote_status(remote_batches: list[dict[str, Any]]) -> str | None:
    statuses = {str(batch.get("remote_status")) for batch in remote_batches if batch.get("remote_status")}
    if not statuses:
        return None
    if statuses == {"completed"}:
        return "completed"
    if "failed" in statuses:
        return "failed"
    if "expired" in statuses:
        return "expired"
    if "cancelled" in statuses or "cancelling" in statuses:
        return "cancelled"
    if statuses & {"in_progress", "validating", "finalizing", "submitted"}:
        return "in_progress"
    return sorted(statuses)[0]


def _aggregate_request_counts(remote_batches: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"total": 0, "completed": 0, "failed": 0}
    for batch in remote_batches:
        snapshot = batch.get("remote_snapshot")
        request_counts = snapshot.get("request_counts") if isinstance(snapshot, dict) else None
        if not isinstance(request_counts, dict):
            totals["total"] += int(batch.get("item_count") or 0)
            if batch.get("state") in {"completed", "fetched"}:
                totals["completed"] += int(batch.get("item_count") or 0)
            continue
        for key in totals:
            try:
                totals[key] += int(request_counts.get(key) or 0)
            except (TypeError, ValueError):
                continue
    return totals


def _aggregate_usage(remote_batches: list[dict[str, Any]]) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for batch in remote_batches:
        snapshot = batch.get("remote_snapshot")
        usage = snapshot.get("usage") if isinstance(snapshot, dict) else None
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, dict):
                continue
            try:
                totals[key] = totals.get(key, 0) + int(value)
            except (TypeError, ValueError):
                continue
    return totals or None


def _local_state_from_remote_status(remote_status: str) -> str:
    mapping = {
        "submitted": "submitted",
        "validating": "running",
        "in_progress": "running",
        "finalizing": "running",
        "completed": "completed",
        "failed": "failed",
        "expired": "expired",
        "cancelling": "cancelled",
        "cancelled": "cancelled",
    }
    return mapping.get(remote_status, "submitted")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
