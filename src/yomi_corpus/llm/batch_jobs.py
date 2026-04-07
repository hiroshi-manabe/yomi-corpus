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
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows
from yomi_corpus.paths import resolve_repo_path

ITEMS_FILENAME = "items.jsonl"
REQUESTS_FILENAME = "requests.jsonl"
MANIFEST_FILENAME = "manifest.json"
STATUS_FILENAME = "status.json"
RAW_RESULTS_FILENAME = "results.raw.jsonl"
PARSED_RESULTS_FILENAME = "results.parsed.jsonl"


def prepare_batch_job(task_config_path: str, input_jsonl_path: str, job_dir: str) -> None:
    task_config = load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    job_path = resolve_repo_path(job_dir)
    job_path.mkdir(parents=True, exist_ok=True)

    requests_path = job_path / REQUESTS_FILENAME
    items_path = job_path / ITEMS_FILENAME
    manifest_path = job_path / MANIFEST_FILENAME
    status_path = job_path / STATUS_FILENAME

    write_batch_requests(task_config, items, requests_path)
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
        "item_count": len(items),
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
    if status["state"] not in {"prepared"}:
        raise ValueError(f"submit is only allowed from prepared state, got {status['state']}")

    backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    remote = backend.submit_batch(
        job_path / REQUESTS_FILENAME,
        endpoint=str(manifest["batch_endpoint"]),
        completion_window=str(manifest["batch_completion_window"]),
    )
    status.update(
        {
            "state": _local_state_from_remote_status(str(remote["status"])),
            "updated_at_epoch": int(time()),
            "batch_id": remote["batch_id"],
            "input_file_id": remote["input_file_id"],
            "output_file_id": remote.get("output_file_id"),
            "error_file_id": remote.get("error_file_id"),
            "remote_status": remote["status"],
            "submitted_at_epoch": remote.get("created_at"),
        }
    )
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
    batch_id = status.get("batch_id")
    if not batch_id:
        raise ValueError("No batch_id recorded for this job.")

    backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    remote = backend.retrieve_batch(str(batch_id))
    status.update(
        {
            "state": _local_state_from_remote_status(str(remote["status"])),
            "updated_at_epoch": int(time()),
            "remote_status": remote["status"],
            "output_file_id": remote.get("output_file_id"),
            "error_file_id": remote.get("error_file_id"),
            "remote_snapshot": remote,
        }
    )
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
    output_file_id = status.get("output_file_id")
    if status.get("remote_status") != "completed" or not output_file_id:
        raise ValueError("fetch requires a completed batch with output_file_id.")

    backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    raw_results_path = job_path / RAW_RESULTS_FILENAME
    parsed_results_path = job_path / PARSED_RESULTS_FILENAME
    backend.download_file(str(output_file_id), raw_results_path)

    items_by_id = _load_items_by_id(job_path / ITEMS_FILENAME)
    with raw_results_path.open(encoding="utf-8") as src, parsed_results_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line in src:
            if not line.strip():
                continue
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
            dst.write(
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

    status.update(
        {
            "state": "fetched",
            "updated_at_epoch": int(time()),
            "fetched_at_epoch": int(time()),
        }
    )
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
