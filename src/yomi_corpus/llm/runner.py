from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.llm.backend import OpenAIResponsesBackend, write_batch_requests
from yomi_corpus.llm.batch_jobs import (
    fetch_batch_job,
    list_batch_jobs,
    poll_batch_job,
    prepare_batch_job,
    submit_batch_job,
)
from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.schemas import LLMResult
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows
from yomi_corpus.paths import resolve_repo_path


def run_sync_task(
    task_config_path: str,
    input_jsonl_path: str,
    output_jsonl_path: str,
    *,
    api_key_file: str | None = None,
) -> None:
    task_config = load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    results = OpenAIResponsesBackend(api_key_file=api_key_file).run_sync(task_config, items)
    write_results_jsonl(str(resolve_repo_path(output_jsonl_path)), results)


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
