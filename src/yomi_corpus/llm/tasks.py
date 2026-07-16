from __future__ import annotations

import json
from typing import Any

from yomi_corpus.llm.prompts import load_prompt_template, render_prompt
from yomi_corpus.llm.rendering import rendered_for_llm
from yomi_corpus.llm.schemas import LLMTaskConfig, PromptItem
from yomi_corpus.paths import resolve_repo_path

YOMI_READING_CONTEXT_CLIP_THRESHOLD = 200
YOMI_READING_CONTEXT_SIDE_CHARS = 80
YOMI_READING_CONTEXT_OMISSION = "…"


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
        marked_text, context_metadata = yomi_reading_marked_text(row)
        return (
            item_id,
            {
                "marked_text": marked_text,
                "surface": row["surface"],
            },
            {
                "surface": row["surface"],
                "source_row": row,
                "prompt_context": context_metadata,
            },
        )
    if builder_name == "yomi_repair":
        item_id = str(row.get("item_id") or row.get("unit_id") or f"item_{index:05d}")
        rendered = rendered_for_llm(
            str(row.get("rendered_yomi") or row.get("rendered") or ""),
            task_config.rendered_yomi_display,
        )
        return (
            item_id,
            {
                "text": row["text"],
                "current_yomi": rendered,
                "rejected_span": rejected_span_for_repair(row),
                "rejected_readings": rejected_readings_for_repair(row),
                "note": row.get("note", ""),
            },
            {
                **_metadata(row, rendered),
                "repair_scope": row.get("repair_scope"),
                "target_escalations": row.get("target_escalations", []),
            },
        )
    raise ValueError(f"Unsupported input builder: {builder_name}")


def yomi_reading_marked_text(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    text = str(row.get("text") or "")
    original_marked_text = str(row["marked_text"])
    metadata: dict[str, Any] = {
        "clipped": False,
        "original_text_chars": len(text),
        "clip_threshold_chars": YOMI_READING_CONTEXT_CLIP_THRESHOLD,
        "side_context_chars": YOMI_READING_CONTEXT_SIDE_CHARS,
    }
    if len(text) <= YOMI_READING_CONTEXT_CLIP_THRESHOLD:
        return original_marked_text, metadata

    try:
        target_start = int(row["target_start"])
        target_end = int(row["target_end"])
    except (KeyError, TypeError, ValueError):
        metadata["clip_reason"] = "missing_target_offsets"
        return original_marked_text, metadata
    if not (0 <= target_start < target_end <= len(text)):
        metadata["clip_reason"] = "invalid_target_offsets"
        return original_marked_text, metadata

    context_start = max(0, target_start - YOMI_READING_CONTEXT_SIDE_CHARS)
    context_end = min(len(text), target_end + YOMI_READING_CONTEXT_SIDE_CHARS)
    left_clipped = context_start > 0
    right_clipped = context_end < len(text)
    marked_text = "".join(
        [
            YOMI_READING_CONTEXT_OMISSION if left_clipped else "",
            text[context_start:target_start],
            "**",
            text[target_start:target_end],
            "**",
            text[target_end:context_end],
            YOMI_READING_CONTEXT_OMISSION if right_clipped else "",
        ]
    )
    metadata.update(
        {
            "clipped": True,
            "context_start": context_start,
            "context_end": context_end,
            "left_clipped": left_clipped,
            "right_clipped": right_clipped,
            "prompt_text_chars": context_end - context_start,
        }
    )
    return marked_text, metadata


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


def rejected_span_for_repair(row: dict[str, Any]) -> str:
    targets = [target for target in row.get("target_escalations", []) if isinstance(target, dict)]
    span = "".join(str(target.get("surface", "")) for target in targets)
    return span or str(row.get("rejected_span") or "")


def rejected_readings_for_repair(row: dict[str, Any]) -> str:
    parts: list[str] = []
    targets = [target for target in row.get("target_escalations", []) if isinstance(target, dict)]
    for target in targets:
        surface = str(target.get("surface") or "")
        readings = target.get("rejected_readings")
        if isinstance(readings, list):
            for reading in readings:
                if not isinstance(reading, dict):
                    continue
                value = reading.get("reading")
                if surface and isinstance(value, str) and value:
                    parts.append(f"{surface}={value}")
        elif surface:
            value = target.get("current_reading_hiragana")
            if isinstance(value, str) and value:
                parts.append(f"{surface}={value}")
    return "; ".join(parts) if parts else str(row.get("rejected_readings") or "")
