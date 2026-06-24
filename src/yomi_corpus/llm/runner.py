from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from time import sleep, time

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


@dataclass
class ProgressBar:
    label: str
    total: int
    stream: object = sys.stderr
    width: int = 28
    current: int = 0

    def render(self) -> None:
        completed = min(self.current, self.total) if self.total else self.current
        ratio = 1.0 if self.total == 0 else min(completed / self.total, 1.0)
        filled = int(self.width * ratio)
        bar = "#" * filled + "-" * (self.width - filled)
        percent = ratio * 100.0
        self.stream.write(
            f"\r[{bar}] {completed}/{self.total} {percent:5.1f}% {self.label}"
        )
        self.stream.flush()

    def update_to(self, value: int) -> None:
        self.current = value
        self.render()

    def finish(self) -> None:
        self.update_to(self.total)
        self.stream.write("\n")
        self.stream.flush()


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
    batch_wait: bool = True,
    batch_poll_interval_seconds: float = 60.0,
    batch_max_wait_seconds: float | None = None,
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
            wait=batch_wait,
            poll_interval_seconds=batch_poll_interval_seconds,
            max_wait_seconds=batch_max_wait_seconds,
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
            show_progress=show_progress,
            wait=batch_wait,
            poll_interval_seconds=batch_poll_interval_seconds,
            max_wait_seconds=batch_max_wait_seconds,
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
    progress = ProgressBar(label="LLM sync", total=len(items), current=len(completed_ids)) if show_progress else None
    if progress is not None:
        progress.render()
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
            if progress is not None:
                progress.update_to(len(completed_ids))
    if progress is not None:
        progress.finish()

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
    wait: bool = True,
    poll_interval_seconds: float = 60.0,
    max_wait_seconds: float | None = None,
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

    progress = (
        ProgressBar(label="LLM background", total=len(items), current=len(completed_ids))
        if show_progress
        else None
    )
    if progress is not None:
        progress.render()
    start_time = time()
    while True:
        completed_ids = poll_background_records_once(
            task_config=task_config,
            items=items,
            records=records,
            completed_ids=completed_ids,
            output_path=output_path,
            backend=backend,
            progress=progress,
        )
        write_background_records(responses_path, records, items)
        pending_items = len(item_ids - completed_ids)
        if pending_items == 0:
            break
        if not wait:
            break
        if max_wait_seconds is not None and time() - start_time >= max_wait_seconds:
            break
        sleep(max(poll_interval_seconds, 0.0))
    if progress is not None:
        progress.finish()

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
    atomic_write_text(
        status_path,
        json.dumps(status_payload, ensure_ascii=False, indent=2) + "\n",
    )

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
    show_progress: bool = False,
    wait: bool = True,
    poll_interval_seconds: float = 60.0,
    max_wait_seconds: float | None = None,
) -> ResumableLLMJobSummary:
    task_config = task_config_override or load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    total_items = len(rows)
    output_path = resolve_repo_path(output_jsonl_path)
    job_path = resolve_repo_path(job_dir)
    status_path = job_path / "status.json"
    parsed_results_path = job_path / "results.parsed.jsonl"
    progress = ProgressBar(label="LLM batch", total=total_items) if show_progress else None
    start_time = time()

    if not status_path.exists():
        prepare_batch_job(
            task_config_path,
            input_jsonl_path,
            str(job_path),
            task_config_override=task_config,
        )
    status = _load_json(status_path)

    while True:
        if status.get("state") == "prepared" or _has_unsubmitted_remote_batches(status):
            status = submit_batch_job(str(job_path), api_key_file=api_key_file)

        if status.get("state") in {"submitted", "running"}:
            status = poll_batch_job(str(job_path), api_key_file=api_key_file)

        if progress is not None:
            update_batch_progress(progress, status)

        if status.get("state") == "completed":
            status = fetch_batch_job(str(job_path), api_key_file=api_key_file)
            if progress is not None:
                update_batch_progress(progress, status)

        if status.get("state") in {"fetched", "failed", "expired", "cancelled"}:
            break
        if not wait:
            break
        if max_wait_seconds is not None and time() - start_time >= max_wait_seconds:
            break
        sleep(max(poll_interval_seconds, 0.0))

    if progress is not None:
        progress.stream.write("\n")
        progress.stream.flush()

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


def update_batch_progress(progress: ProgressBar, status: dict[str, object]) -> None:
    counts = _request_counts_from_status(status)
    completed = int(counts.get("completed") or 0)
    total = int(counts.get("total") or progress.total)
    remote_status = status.get("remote_status") or status.get("state") or "unknown"
    chunks = batch_chunk_state_summary(status)
    progress.total = total
    progress.label = f"LLM batch {remote_status}" + (f" ({chunks})" if chunks else "")
    progress.update_to(completed)


def batch_chunk_state_summary(status: dict[str, object]) -> str:
    remote_batches = status.get("remote_batches")
    if not isinstance(remote_batches, list):
        return ""
    counts: dict[str, int] = {}
    for batch in remote_batches:
        if not isinstance(batch, dict):
            continue
        state = str(batch.get("remote_status") or batch.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return ", ".join(f"{state}:{count}" for state, count in sorted(counts.items()))


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
    atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))


def poll_background_records_once(
    *,
    task_config: LLMTaskConfig,
    items: list[object],
    records: dict[str, dict[str, object]],
    completed_ids: set[str],
    output_path: Path,
    backend: OpenAIResponsesBackend,
    progress: ProgressBar | None = None,
) -> set[str]:
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
            if progress is not None:
                progress.update_to(len(completed_ids))
    return completed_ids


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
    for row in iter_jsonl_rows_tolerating_truncated_tail(path):
        item_id = row.get("item_id")
        if isinstance(item_id, str) and item_id:
            item_ids.add(item_id)
    return item_ids


def load_result_item_count(path: Path) -> int:
    return sum(1 for _ in iter_jsonl_rows_tolerating_truncated_tail(path))


def count_result_parse_errors(path: Path) -> int:
    count = 0
    for row in iter_jsonl_rows_tolerating_truncated_tail(path):
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
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_background_items(path: Path, items: list[object]) -> None:
    lines: list[str] = []
    for item in items:
        lines.append(
            json.dumps(
                {
                    "item_id": item.item_id,
                    "prompt": item.prompt,
                    "metadata": item.metadata,
                },
                ensure_ascii=False,
            )
        )
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def iter_jsonl_rows_tolerating_truncated_tail(path: Path):
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                return
            raise


def load_background_records(path: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for row in iter_jsonl_rows_tolerating_truncated_tail(path):
        item_id = row.get("item_id")
        if isinstance(item_id, str) and item_id:
            records[item_id] = row
    return records


def write_background_records(
    path: Path,
    records: dict[str, dict[str, object]],
    items: list[object],
) -> None:
    lines: list[str] = []
    for item in items:
        record = records.get(item.item_id)
        if record:
            lines.append(json.dumps(record, ensure_ascii=False))
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


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

            parsed = parse_output(raw_text, task_config.parser, metadata=item.metadata)
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


def _has_unsubmitted_remote_batches(status: dict[str, object]) -> bool:
    remote_batches = status.get("remote_batches")
    if not isinstance(remote_batches, list):
        return False
    return any(isinstance(batch, dict) and not batch.get("batch_id") for batch in remote_batches)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
