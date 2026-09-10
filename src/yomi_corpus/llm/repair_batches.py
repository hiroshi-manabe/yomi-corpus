"""Nonblocking, immutable Batch cohorts for a growing Escalated Repair queue.

Called under the pipeline's existing review-sync lock. Retry rounds use separate
roots; within a round, each exact item/request pair is submitted only once.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
import time
from pathlib import Path
from typing import Any

from yomi_corpus.llm.backend import (
    OpenAIResponsesBackend,
    build_response_create_kwargs,
    extract_output_text_from_batch_item,
    extract_usage_from_batch_item,
    tool_calls_from_batch_item,
)
from yomi_corpus.llm.parsers import parse_output
from yomi_corpus.llm.runner import ResumableLLMJobSummary
from yomi_corpus.llm.schemas import LLMTaskConfig
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows

TERMINAL = {"completed", "failed", "expired", "cancelled"}
METADATA_KEY = "yomi_repair_cohort"
RATE_LIMIT_RETRY_SECONDS = 300


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save(path: Path, value: Any) -> None:
    text = encoded(value) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        atomic_text(path, text)


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class RepairBatchClient:
    def __init__(self) -> None:
        self.client = OpenAIResponsesBackend()._client.with_options(
            timeout=30, max_retries=0
        )

    def upload(self, path: Path) -> str:
        with path.open("rb") as handle:
            return self.client.files.create(file=handle, purpose="batch").id

    def submit(self, snapshot: dict, file_id: str) -> dict:
        return self.client.batches.create(
            input_file_id=file_id,
            endpoint=snapshot["endpoint"],
            completion_window=snapshot["completion_window"],
            metadata={METADATA_KEY: snapshot["id"]},
        ).model_dump()

    def retrieve(self, batch_id: str) -> dict:
        return self.client.batches.retrieve(batch_id).model_dump()

    def reconcile(self, cohort_id: str) -> dict | None:
        for index, batch in enumerate(self.client.batches.list(limit=100)):
            if (batch.metadata or {}).get(METADATA_KEY) == cohort_id:
                return batch.model_dump()
            if index >= 999:
                break
        return None

    def download(self, file_id: str) -> str:
        return self.client.files.content(file_id).text


def collect_results(
    snapshot: dict, remote: dict, client: Any, directory: Path
) -> list[dict]:
    raw_by_id = {}
    for kind in ("output", "error"):
        file_id = remote.get(f"{kind}_file_id")
        if not file_id:
            continue
        path = directory / f"{kind}.jsonl"
        if not path.exists():
            atomic_text(path, client.download(file_id))
        for line in path.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            raw = json.loads(line)
            key = raw.get("custom_id")
            if key not in snapshot["items"] or key in raw_by_id:
                raise ValueError(f"Unknown or duplicate Batch custom_id: {key}")
            raw_by_id[key] = raw
    results = []
    for key, item in snapshot["items"].items():
        raw = raw_by_id.get(key, {})
        response = raw.get("response") or {}
        body = response.get("body") or {}
        remote_error = raw.get("error") or body.get("error") or {}
        rate_limited = response.get("status_code") == 429 or (
            isinstance(remote_error, dict)
            and remote_error.get("code") in {"rate_limit_exceeded", "rate_limit_error"}
        )
        text = extract_output_text_from_batch_item(raw) or ""
        error = None
        parsed = None
        try:
            if not raw:
                raise ValueError(
                    f"Batch {remote['status']}: missing result; {remote.get('errors')}"
                )
            if raw.get("error") or response.get("status_code") != 200:
                raise ValueError(
                    str(
                        raw.get("error")
                        or body.get("error")
                        or response.get("status_code")
                    )
                )
            if body.get("status") != "completed":
                raise ValueError(
                    f"Response status: {body.get('status')}; {body.get('incomplete_details')}"
                )
            parsed = parse_output(text, snapshot["parser"], metadata=item["metadata"])
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)
        results.append(
            {
                "request_key": key,
                "item_id": item["item_id"],
                "raw_text": text,
                "parsed": parsed,
                "parse_error": error,
                "rate_limited": rate_limited,
                "usage": extract_usage_from_batch_item(raw),
                "tool_calls": tool_calls_from_batch_item(raw),
                "metadata": {
                    **item["metadata"],
                    "repair_batch": {
                        "cohort_id": snapshot["id"],
                        "batch_id": remote["id"],
                        "processing_tier": "batch",
                    },
                },
            }
        )
    return results


def advance_rate_limit_retry(
    directory: Path, snapshot: dict, state: dict, client: Any
) -> dict:
    if time.time() < state["retry_after"]:
        return state
    previous = {row["request_key"]: row for row in read(directory / "results.json")}
    keys = {key for key, row in previous.items() if row.get("rate_limited")}
    attempt = int(state.get("transport_attempt", 0)) + 1
    retry_dir = directory / f"transport-{attempt}"
    retry_dir.mkdir(exist_ok=True)
    if not (retry_dir / "snapshot.json").exists():
        save(
            retry_dir / "snapshot.json",
            {
                **snapshot,
                "id": sha256(
                    f"{snapshot['id']}:transport:{attempt}".encode()
                ).hexdigest(),
                "items": {
                    key: item for key, item in snapshot["items"].items() if key in keys
                },
                "requests": [
                    request
                    for request in snapshot["requests"]
                    if request["custom_id"] in keys
                ],
            },
        )
    retry = advance_cohort(retry_dir, client, retry_rate_limits=False)
    if retry["state"] not in {"collected", "rate_limited"}:
        return state
    previous.update(
        {row["request_key"]: row for row in read(retry_dir / "results.json")}
    )
    save(directory / "results.json", list(previous.values()))
    state["transport_attempt"] = attempt
    if any(row.get("rate_limited") for row in previous.values()):
        state["retry_after"] = time.time() + RATE_LIMIT_RETRY_SECONDS
    else:
        state["state"] = "collected"
    save(directory / "state.json", state)
    return state


def advance_cohort(
    directory: Path, client: Any, *, retry_rate_limits: bool = True
) -> dict:
    snapshot = read(directory / "snapshot.json")
    state_path = directory / "state.json"
    state = read(state_path) if state_path.exists() else {"state": "prepared"}
    if state["state"] == "collected":
        return state
    if time.time() < state.get("request_retry_after", 0):
        return state
    try:
        if state["state"] == "rate_limited":
            return (
                advance_rate_limit_retry(directory, snapshot, state, client)
                if retry_rate_limits
                else state
            )
        if state.get("batch_id"):
            remote = client.retrieve(state["batch_id"])
        elif state["state"] == "submitting":
            # A lost submission response is not proof that submission failed.
            remote = client.reconcile(snapshot["id"])
            if remote is None:
                state["error"] = "Submission outcome unknown; awaiting reconciliation"
                save(state_path, state)
                return state
        else:
            if not state.get("input_file_id"):
                requests = directory / "requests.jsonl"
                atomic_text(
                    requests,
                    "".join(
                        encoded(request) + "\n" for request in snapshot["requests"]
                    ),
                )
                state["input_file_id"] = client.upload(requests)
                save(state_path, state)
            state["state"] = "submitting"
            save(state_path, state)
            remote = client.submit(snapshot, state["input_file_id"])
        state.update(batch_id=remote["id"], state=remote["status"], remote=remote)
        state.pop("error", None)
        save(state_path, state)
        if remote["status"] in TERMINAL:
            results = collect_results(snapshot, remote, client, directory)
            save(directory / "results.json", results)
            if any(row.get("rate_limited") for row in results):
                state["state"] = "rate_limited"
                state["retry_after"] = time.time() + RATE_LIMIT_RETRY_SECONDS
            else:
                state["state"] = "collected"
            save(state_path, state)
    except Exception as exc:
        # One unavailable remote job must not prevent polling other cohorts.
        state["error"] = f"{type(exc).__name__}: {exc}"
        if getattr(exc, "status_code", None) in {400, 401, 403, 404, 422, 429}:
            # An explicit rejection differs from losing the create response.
            if not state.get("batch_id"):
                state["state"] = "prepared"
            state["request_retry_after"] = time.time() + RATE_LIMIT_RETRY_SECONDS
        save(state_path, state)
    return state


def run_repair_batch_task(
    input_path: Path,
    output_path: Path,
    *,
    task_config: LLMTaskConfig,
    job_dir: Path,
    client: Any = None,
) -> ResumableLLMJobSummary:
    job_dir.mkdir(parents=True, exist_ok=True)
    items = build_prompt_items(task_config, load_jsonl_rows(str(input_path)))
    current = {}
    requests = {}
    for item in items:
        body = build_response_create_kwargs(task_config, item.prompt)
        identity = {
            "item_id": item.item_id,
            "endpoint": task_config.batch_endpoint,
            "body": body,
            "parser": task_config.parser,
        }
        key = sha256(encoded(identity).encode()).hexdigest()
        if key in current or any(
            previous["item_id"] == item.item_id for previous in current.values()
        ):
            raise ValueError(f"Duplicate Escalated Repair item: {item.item_id}")
        current[key] = {
            "item_id": item.item_id,
            "prompt": item.prompt,
            "metadata": item.metadata,
        }
        requests[key] = {
            "custom_id": key,
            "method": "POST",
            "url": task_config.batch_endpoint,
            "body": body,
        }
    directories = sorted(path.parent for path in job_dir.glob("cohort-*/snapshot.json"))
    known = {
        key
        for directory in directories
        for key in read(directory / "snapshot.json")["items"]
    }
    missing = sorted(set(current) - known)
    if missing:
        identifier = sha256(
            encoded({"root": str(job_dir.resolve()), "keys": missing}).encode()
        ).hexdigest()
        directory = job_dir / f"cohort-{identifier}"
        directory.mkdir(exist_ok=True)
        save(
            directory / "snapshot.json",
            {
                "schema_version": 1,
                "id": identifier,
                "endpoint": task_config.batch_endpoint,
                "completion_window": task_config.batch_completion_window,
                "parser": task_config.parser,
                "items": {key: current[key] for key in missing},
                "requests": [requests[key] for key in missing],
            },
        )
        directories.append(directory)
    results = {}
    states = []
    for directory in directories:
        previous = (
            read(directory / "state.json")
            if (directory / "state.json").exists()
            else {}
        )
        if previous.get("state") != "collected" and client is None:
            client = RepairBatchClient()
        state = advance_cohort(directory, client)
        states.append({"cohort": directory.name, **state})
        if state["state"] == "collected":
            for row in read(directory / "results.json"):
                key = row["request_key"]
                if key in current:
                    row["metadata"] = {
                        **current[key]["metadata"],
                        "repair_batch": row["metadata"]["repair_batch"],
                    }
                    results[key] = row
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(encoded(results[key]) + "\n" for key in current if key in results)
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != text:
        atomic_text(output_path, text)
    completed = len(results) == len(current)
    summary = {
        "state": "completed" if completed else "running",
        "total": len(current),
        "collected": len(results),
        "cohorts": states,
    }
    save(job_dir / "status.json", summary)
    errors = [state["error"] for state in states if state.get("error")]
    return ResumableLLMJobSummary(
        job_id=job_dir.name,
        mode="batch",
        status=summary["state"],
        total_items=len(current),
        completed_items=len(results),
        skipped_items=0,
        failed_items=sum(bool(row.get("parse_error")) for row in results.values()),
        results_jsonl=str(output_path),
        job_dir=str(job_dir),
        manifest_json=str(job_dir / "status.json"),
        status_reason=(
            None
            if completed
            else (
                "batch_cohort_error: " + errors[0][:300]
                if errors
                else "awaiting_batch_results"
            )
        ),
    )
