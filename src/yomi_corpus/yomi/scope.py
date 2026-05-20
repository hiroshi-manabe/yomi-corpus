from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScopeTriageQueueSummary:
    read: int
    queued: int
    output_jsonl: str
    summary_json: str


@dataclass(frozen=True)
class ScopeTriageApplySummary:
    read_units: int
    llm_result_count: int
    keep: int
    skip: int
    parse_error_keep: int
    missing_result_keep: int
    output_jsonl: str
    summary_json: str


def build_scope_triage_queue_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> ScopeTriageQueueSummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    read = 0
    queued = 0
    with input_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read += 1
            unit = json.loads(line)
            item = {
                "unit_id": str(unit.get("unit_id", "")),
                "text": str(unit.get("text", "")),
            }
            dst.write(json.dumps(item, ensure_ascii=False) + "\n")
            queued += 1

    summary = ScopeTriageQueueSummary(
        read=read,
        queued=queued,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def apply_scope_triage_results_file(
    *,
    units_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> ScopeTriageApplySummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    results = load_scope_results(results_jsonl)
    read_units = 0
    keep = 0
    skip = 0
    parse_error_keep = 0
    missing_result_keep = 0

    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id", ""))
            result = results.get(unit_id)
            judgment = build_scope_judgment(unit_id, result)
            if judgment["status"] == "Skip":
                skip += 1
            elif judgment["source"] == "parse_error":
                keep += 1
                parse_error_keep += 1
            elif judgment["source"] == "missing_llm_result":
                keep += 1
                missing_result_keep += 1
            else:
                keep += 1
            unit.setdefault("analysis", {}).setdefault("llm", {})["scope_triage"] = judgment
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")

    summary = ScopeTriageApplySummary(
        read_units=read_units,
        llm_result_count=len(results),
        keep=keep,
        skip=skip,
        parse_error_keep=parse_error_keep,
        missing_result_keep=missing_result_keep,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def load_scope_results(results_jsonl: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not results_jsonl.exists():
        return results
    with results_jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = str(row.get("item_id", ""))
            if item_id:
                results[item_id] = row
    return results


def build_scope_judgment(unit_id: str, result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {
            "status": "Keep",
            "source": "missing_llm_result",
            "parse_error": "Missing scope triage LLM result.",
            "raw_text": None,
            "result_item_id": None,
        }
    parsed = result.get("parsed")
    if isinstance(parsed, dict) and parsed.get("status") in {"Keep", "Skip"}:
        return {
            "status": str(parsed["status"]),
            "source": "llm",
            "parse_error": result.get("parse_error"),
            "raw_text": result.get("raw_text"),
            "result_item_id": result.get("item_id") or unit_id,
        }
    return {
        "status": "Keep",
        "source": "parse_error",
        "parse_error": result.get("parse_error") or "Expected Keep or Skip.",
        "raw_text": result.get("raw_text"),
        "result_item_id": result.get("item_id") or unit_id,
    }
