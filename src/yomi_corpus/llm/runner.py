from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
import sys
from time import time

from yomi_corpus.llm.backend import OpenAIResponsesBackend, write_batch_requests
from yomi_corpus.llm.batch_jobs import (
    fetch_batch_job,
    list_batch_jobs,
    poll_batch_job,
    prepare_batch_job,
    submit_batch_job,
)
from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.schemas import LLMResult, LLMTaskConfig
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows
from yomi_corpus.paths import resolve_repo_path


@dataclass(frozen=True)
class ResumableLLMJobSummary:
    job_id: str | None
    mode: str
    status: str
    total_items: int
    completed_items: int
    skipped_items: int
    failed_items: int
    results_jsonl: str
    job_dir: str | None
    manifest_json: str | None
    remote_status: str | None = None
    remote_batch_id: str | None = None


LLM_EXECUTION_MODE_SYNC = "sync"
LLM_EXECUTION_MODE_BACKGROUND = "background"
LLM_EXECUTION_MODE_BATCH = "batch"
LLM_EXECUTION_MODES = frozenset(
    {LLM_EXECUTION_MODE_SYNC, LLM_EXECUTION_MODE_BACKGROUND, LLM_EXECUTION_MODE_BATCH}
)

BACKGROUND_RESPONSES_FILENAME = "responses.jsonl"
BACKGROUND_STATUS_FILENAME = "background_status.json"
BACKGROUND_ITEMS_FILENAME = "items.jsonl"


def run_llm_task(
    task_config_path: str,
    input_jsonl_path: str,
    output_jsonl_path: str,
    *,
    execution_mode: str,
    api_key_file: str | None = None,
    task_config_override: LLMTaskConfig | None = None,
    job_dir: str | None = None,
    show_progress: bool = False,
) -> ResumableLLMJobSummary:
    if execution_mode == LLM_EXECUTION_MODE_SYNC:
        return run_sync_task(
            task_config_path,
            input_jsonl_path,
            output_jsonl_path,
            api_key_file=api_key_file,
            task_config_override=task_config_override,
            job_dir=job_dir,
            show_progress=show_progress,
        )
    if execution_mode == LLM_EXECUTION_MODE_BACKGROUND:
        if not job_dir:
            raise ValueError("job_dir is required for background execution mode.")
        return run_background_task(
            task_config_path,
            input_jsonl_path,
            output_jsonl_path,
            api_key_file=api_key_file,
            task_config_override=task_config_override,
            job_dir=job_dir,
            show_progress=show_progress,
        )
    if execution_mode == LLM_EXECUTION_MODE_BATCH:
        if not job_dir:
            raise ValueError("job_dir is required for batch execution mode.")
        return run_batch_task(
            task_config_path,
            input_jsonl_path,
            output_jsonl_path,
            api_key_file=api_key_file,
            task_config_override=task_config_override,
            job_dir=job_dir,
        )
    raise ValueError(f"Unsupported LLM execution mode: {execution_mode}")


def run_sync_task(
    task_config_path: str,
    input_jsonl_path: str,
    output_jsonl_path: str,
    *,
    api_key_file: str | None = None,
    task_config_override: LLMTaskConfig | None = None,
    job_dir: str | None = None,
    show_progress: bool = False,
) -> ResumableLLMJobSummary:
    task_config = task_config_override or load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    item_ids = {item.item_id for item in items}
    output_path = resolve_repo_path(output_jsonl_path)
    job_path = resolve_repo_path(job_dir) if job_dir else None
    if job_path:
        job_path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolve_repo_path(input_jsonl_path), job_path / "input.jsonl")

    completed_ids = load_result_item_ids(output_path) & item_ids
    backend = OpenAIResponsesBackend(api_key_file=api_key_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    skipped_items = 0
    failed_items = 0
    with output_path.open("a", encoding="utf-8") as handle:
        for item in items:
            if item.item_id in completed_ids:
                skipped_items += 1
                continue
            result = backend.run_item(task_config, item)
            handle.write(json.dumps(result_to_json_row(result), ensure_ascii=False) + "\n")
            handle.flush()
            completed_ids.add(item.item_id)
            if result.parse_error:
                failed_items += 1
            if show_progress:
                print(
                    f"LLM sync progress: {len(completed_ids)}/{len(items)} completed",
                    file=sys.stderr,
                    flush=True,
                )

    summary = ResumableLLMJobSummary(
        job_id=None if job_path is None else job_path.name,
        mode="sync",
        status="completed",
        total_items=len(items),
        completed_items=len(completed_ids),
        skipped_items=skipped_items,
        failed_items=failed_items,
        results_jsonl=str(output_path),
        job_dir=None if job_path is None else str(job_path),
        manifest_json=None if job_path is None else str(job_path / "manifest.json"),
    )
    if job_path:
        write_job_manifest(
            job_path / "manifest.json",
            summary=summary,
            task_config=task_config,
            task_config_path=task_config_path,
            input_jsonl_path=input_jsonl_path,
        )
    return summary


def run_background_task(
    task_config_path: str,
    input_jsonl_path: str,
    output_jsonl_path: str,
    *,
    api_key_file: str | None = None,
    task_config_override: LLMTaskConfig | None = None,
    job_dir: str,
    show_progress: bool = False,
) -> ResumableLLMJobSummary:
    task_config = task_config_override or load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    items_by_id = {item.item_id: item for item in items}
    item_ids = set(items_by_id)
    output_path = resolve_repo_path(output_jsonl_path)
    job_path = resolve_repo_path(job_dir)
    job_path.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolve_repo_path(input_jsonl_path), job_path / "input.jsonl")
    write_background_items(job_path / BACKGROUND_ITEMS_FILENAME, items)

    responses_path = job_path / BACKGROUND_RESPONSES_FILENAME
    status_path = job_path / BACKGROUND_STATUS_FILENAME
    completed_ids = load_result_item_ids(output_path) & item_ids
    records = load_background_records(responses_path)
    backend = OpenAIResponsesBackend(api_key_file=api_key_file)

    for item in items:
        if item.item_id in completed_ids or item.item_id in records:
            continue
        snapshot = backend.submit_background_item(task_config, item)
        response_id = snapshot.get("response_id")
        if not response_id:
            raise ValueError(f"Background response for item {item.item_id} did not include an id.")
        records[item.item_id] = {
            "item_id": item.item_id,
            "response_id": str(response_id),
            "status": snapshot.get("status"),
            "submitted_at_epoch": int(time()),
            "updated_at_epoch": int(time()),
            "metadata": item.metadata,
        }
        write_background_records(responses_path, records, items)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for item in items:
            if item.item_id in completed_ids:
                continue
            record = records.get(item.item_id)
            if not record:
                continue
            status = str(record.get("status") or "")
            if status in {"failed", "cancelled", "incomplete"}:
                result = background_failure_result(item, record)
            else:
                snapshot = backend.retrieve_response(str(record["response_id"]))
                status = str(snapshot.get("status") or "")
                record.update(
                    {
                        "status": status,
                        "updated_at_epoch": int(time()),
                        "completed_at": snapshot.get("completed_at"),
                        "error": snapshot.get("error"),
                        "incomplete_details": snapshot.get("incomplete_details"),
                    }
                )
                if status == "completed":
                    result = background_completed_result(task_config, item, snapshot)
                elif status in {"failed", "cancelled", "incomplete"}:
                    result = background_failure_result(item, record)
                else:
                    result = None
            if result is None:
                continue
            handle.write(json.dumps(result_to_json_row(result), ensure_ascii=False) + "\n")
            handle.flush()
            completed_ids.add(item.item_id)
            if show_progress:
                print(
                    f"LLM background progress: {len(completed_ids)}/{len(items)} completed",
                    file=sys.stderr,
                    flush=True,
                )
    write_background_records(responses_path, records, items)

    failed_items = count_result_parse_errors(output_path)
    pending_items = len(item_ids - completed_ids)
    status = "completed" if pending_items == 0 else "running"
    status_payload = {
        "state": status,
        "updated_at_epoch": int(time()),
        "total_items": len(items),
        "completed_items": len(completed_ids),
        "failed_items": failed_items,
        "pending_items": pending_items,
    }
    status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = ResumableLLMJobSummary(
        job_id=job_path.name,
        mode="background",
        status=status,
        total_items=len(items),
        completed_items=len(completed_ids),
        skipped_items=0,
        failed_items=failed_items,
        results_jsonl=str(output_path),
        job_dir=str(job_path),
        manifest_json=str(job_path / "manifest.json"),
        remote_status=status,
    )
    write_job_manifest(
        job_path / "manifest.json",
        summary=summary,
        task_config=task_config,
        task_config_path=task_config_path,
        input_jsonl_path=input_jsonl_path,
    )
    return summary


def run_batch_task(
    task_config_path: str,
    input_jsonl_path: str,
    output_jsonl_path: str,
    *,
    api_key_file: str | None = None,
    task_config_override: LLMTaskConfig | None = None,
    job_dir: str,
) -> ResumableLLMJobSummary:
    task_config = task_config_override or load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    total_items = len(rows)
    output_path = resolve_repo_path(output_jsonl_path)
    job_path = resolve_repo_path(job_dir)
    status_path = job_path / "status.json"
    parsed_results_path = job_path / "results.parsed.jsonl"

    if not status_path.exists():
        prepare_batch_job(
            task_config_path,
            input_jsonl_path,
            str(job_path),
            task_config_override=task_config,
        )
    status = _load_json(status_path)

    if status.get("state") == "prepared":
        status = submit_batch_job(str(job_path), api_key_file=api_key_file)

    if status.get("state") in {"submitted", "running"}:
        status = poll_batch_job(str(job_path), api_key_file=api_key_file)

    if status.get("state") == "completed":
        status = fetch_batch_job(str(job_path), api_key_file=api_key_file)

    if status.get("state") == "fetched":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(parsed_results_path, output_path)

    request_counts = _request_counts_from_status(status)
    completed_items = int(request_counts.get("completed") or 0)
    failed_items = int(request_counts.get("failed") or 0)
    if status.get("state") == "fetched":
        completed_items = load_result_item_count(output_path)
    remote_status = status.get("remote_status")
    remote_batch_id = status.get("batch_id")

    return ResumableLLMJobSummary(
        job_id=job_path.name,
        mode="batch",
        status="completed" if status.get("state") == "fetched" else str(status.get("state")),
        total_items=total_items,
        completed_items=completed_items,
        skipped_items=0,
        failed_items=failed_items,
        results_jsonl=str(output_path),
        job_dir=str(job_path),
        manifest_json=str(job_path / "manifest.json"),
        remote_status=str(remote_status) if remote_status is not None else None,
        remote_batch_id=str(remote_batch_id) if remote_batch_id is not None else None,
    )


def prepare_batch_task(
    task_config_path: str,
    input_jsonl_path: str,
    requests_jsonl_path: str,
    manifest_json_path: str,
) -> None:
    task_config = load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    requests_path = resolve_repo_path(requests_jsonl_path)
    manifest_path = resolve_repo_path(manifest_json_path)
    write_batch_requests(task_config, items, requests_path)
    manifest = {
        "task_name": task_config.task_name,
        "model": task_config.model,
        "mode": "batch_prepare",
        "input_builder": task_config.input_builder,
        "parser": task_config.parser,
        "requests_jsonl": str(Path(requests_jsonl_path)),
        "item_count": len(items),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_results_jsonl(path: str, results: list[LLMResult]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(
                json.dumps(
                    {
                        "item_id": result.item_id,
                        "raw_text": result.raw_text,
                        "parsed": result.parsed,
                        "parse_error": result.parse_error,
                        "usage": result.usage,
                        "metadata": result.metadata,
                    },
                    ensure_ascii=False,
            )
                + "\n"
            )


def load_result_item_ids(path: Path) -> set[str]:
    item_ids: set[str] = set()
    if not path.exists():
        return item_ids
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = row.get("item_id")
            if isinstance(item_id, str) and item_id:
                item_ids.add(item_id)
    return item_ids


def load_result_item_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def count_result_parse_errors(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("parse_error"):
                count += 1
    return count


def result_to_json_row(result: LLMResult) -> dict[str, object]:
    return {
        "item_id": result.item_id,
        "raw_text": result.raw_text,
        "parsed": result.parsed,
        "parse_error": result.parse_error,
        "usage": result.usage,
        "metadata": result.metadata,
    }


def write_job_manifest(
    path: Path,
    *,
    summary: ResumableLLMJobSummary,
    task_config: LLMTaskConfig,
    task_config_path: str,
    input_jsonl_path: str,
) -> None:
    payload = {
        **asdict(summary),
        "task_name": task_config.task_name,
        "input_builder": task_config.input_builder,
        "parser": task_config.parser,
        "model": task_config.model,
        "reasoning_effort": task_config.reasoning_effort,
        "verbosity": task_config.verbosity,
        "task_config_path": task_config_path,
        "input_jsonl": input_jsonl_path,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_background_items(path: Path, items: list[object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
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


def load_background_records(path: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = row.get("item_id")
            if isinstance(item_id, str) and item_id:
                records[item_id] = row
    return records


def write_background_records(
    path: Path,
    records: dict[str, dict[str, object]],
    items: list[object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            record = records.get(item.item_id)
            if record:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def background_completed_result(
    task_config: LLMTaskConfig,
    item: object,
    snapshot: dict[str, object],
) -> LLMResult:
    raw_text = str(snapshot.get("raw_text") or "")
    parsed = None
    parse_error = None
    if not raw_text:
        parse_error = "Completed background response did not include output text."
    else:
        try:
            from yomi_corpus.llm.parsers import parse_output

            parsed = parse_output(raw_text, task_config.parser)
        except Exception as exc:  # noqa: BLE001
            parse_error = str(exc)
    return LLMResult(
        item_id=item.item_id,
        raw_text=raw_text,
        parsed=parsed,
        parse_error=parse_error,
        usage=snapshot.get("usage"),
        metadata=item.metadata,
    )


def background_failure_result(item: object, record: dict[str, object]) -> LLMResult:
    status = str(record.get("status") or "failed")
    detail = record.get("error") or record.get("incomplete_details") or status
    return LLMResult(
        item_id=item.item_id,
        raw_text="",
        parsed=None,
        parse_error=f"Background response ended with status {status}: {detail}",
        usage=None,
        metadata=item.metadata,
    )


def _request_counts_from_status(status: dict[str, object]) -> dict[str, object]:
    remote_snapshot = status.get("remote_snapshot")
    if isinstance(remote_snapshot, dict):
        request_counts = remote_snapshot.get("request_counts")
        if isinstance(request_counts, dict):
            return request_counts
    return {}


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
