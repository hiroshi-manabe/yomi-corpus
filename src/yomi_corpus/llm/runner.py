from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
import sys

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
