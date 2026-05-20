from __future__ import annotations

import json
from typing import Any

from yomi_corpus.llm.prompts import load_prompt_template, render_prompt
from yomi_corpus.llm.rendering import rendered_for_llm
from yomi_corpus.llm.schemas import LLMTaskConfig, PromptItem
from yomi_corpus.paths import resolve_repo_path


def load_jsonl_rows(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with resolve_repo_path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def build_prompt_items(task_config: LLMTaskConfig, rows: list[dict[str, Any]]) -> list[PromptItem]:
    template = load_prompt_template(task_config.prompt_template)
    items: list[PromptItem] = []
    for index, row in enumerate(rows, start=1):
        item_id, variables, metadata = build_task_variables(task_config, row, index=index)
        items.append(
            PromptItem(
                item_id=item_id,
                prompt=render_prompt(template, variables),
                metadata=metadata,
            )
        )
    return items


def build_task_variables(
    task_config: LLMTaskConfig, row: dict[str, Any], *, index: int
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    builder_name = task_config.input_builder
    if builder_name == "alphabetic_entity_judge":
        item_id = str(row.get("entity_key", f"item_{index:05d}"))
        return (
            item_id,
            {
                "entity_key": row["entity_key"],
                "surface_forms": " | ".join(row.get("surface_forms", [])),
                "occurrence_count": row.get("occurrence_count", 0),
                "unit_count": row.get("unit_count", 0),
                "example_texts": _join_examples(row.get("example_texts", [])),
            },
            {"source_row": row},
        )
    if builder_name == "non_target_judge":
        item_id = str(row.get("unit_id", f"item_{index:05d}"))
        return item_id, {"text": row["text"]}, {"source_row": row}
    if builder_name == "scope_triage":
        item_id = str(row.get("unit_id", f"item_{index:05d}"))
        return item_id, {"text": row["text"]}, {"source_row": row}
    if builder_name == "yomi_check":
        item_id = str(row.get("unit_id", f"item_{index:05d}"))
        rendered = _rendered_variable(task_config, row)
        return (
            item_id,
            _yomi_variables(task_config, row, rendered),
            _metadata(row, rendered),
        )
    if builder_name == "yomi_triage":
        item_id = str(row.get("unit_id", f"item_{index:05d}"))
        rendered = _rendered_variable(task_config, row)
        return (
            item_id,
            _yomi_variables(task_config, row, rendered),
            _metadata(row, rendered),
        )
    if builder_name == "yomi_reading":
        item_id = str(row.get("item_id", f"item_{index:05d}"))
        return (
            item_id,
            {
                "marked_text": row["marked_text"],
                "surface": row["surface"],
            },
            {"source_row": row},
        )
    if builder_name == "yomi_repair":
        item_id = str(row.get("unit_id", f"item_{index:05d}"))
        rendered = _rendered_variable(task_config, row)
        return (
            item_id,
            {
                "text": row["text"],
                "rendered": rendered,
                "note": row.get("note", ""),
            },
            _metadata(row, rendered),
        )
    raise ValueError(f"Unsupported input builder: {builder_name}")


def _join_examples(examples: list[str]) -> str:
    if not examples:
        return "(no examples)"
    return "\n".join(f"- {example}" for example in examples)


def _rendered_variable(task_config: LLMTaskConfig, row: dict[str, Any]) -> str:
    return rendered_for_llm(str(row["rendered"]), task_config.rendered_yomi_display)


def _yomi_variables(task_config: LLMTaskConfig, row: dict[str, Any], rendered: str) -> dict[str, Any]:
    text = str(row.get("text", ""))
    text_section = f"Text: {text}\n" if task_config.include_source_text else ""
    return {
        "text": text if task_config.include_source_text else "",
        "text_section": text_section,
        "rendered": rendered,
    }


def _metadata(row: dict[str, Any], rendered_prompt: str) -> dict[str, Any]:
    return {
        "source_row": row,
        "rendered_full": row.get("rendered"),
        "rendered_prompt": rendered_prompt,
    }
