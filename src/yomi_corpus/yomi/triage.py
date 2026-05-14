from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class YomiTriageQueueSummary:
    read: int
    queued: int
    skipped_auto_accepted: int
    output_jsonl: str
    summary_json: str


@dataclass(frozen=True)
class YomiTriageApplySummary:
    read_units: int
    llm_result_count: int
    auto_accepted_ok: int
    llm_ok: int
    llm_review: int
    llm_skip: int
    parse_error_review: int
    missing_result_review: int
    output_jsonl: str
    summary_json: str


def build_yomi_triage_queue_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> YomiTriageQueueSummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    read = 0
    queued = 0
    skipped_auto_accepted = 0
    with input_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read += 1
            unit = json.loads(line)
            if is_yomi_auto_accepted(unit):
                skipped_auto_accepted += 1
                continue
            item = build_yomi_triage_item(unit)
            dst.write(json.dumps(item, ensure_ascii=False) + "\n")
            queued += 1

    summary = YomiTriageQueueSummary(
        read=read,
        queued=queued,
        skipped_auto_accepted=skipped_auto_accepted,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def apply_yomi_triage_results_file(
    *,
    units_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> YomiTriageApplySummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    results = load_yomi_triage_results(results_jsonl)
    used_result_ids: set[str] = set()
    read_units = 0
    auto_accepted_ok = 0
    llm_ok = 0
    llm_review = 0
    llm_skip = 0
    parse_error_review = 0
    missing_result_review = 0

    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id", ""))
            if is_yomi_auto_accepted(unit):
                judgment = {
                    "status": "OK",
                    "source": "auto_accept",
                    "parse_error": None,
                    "raw_text": None,
                    "result_item_id": None,
                }
                auto_accepted_ok += 1
            else:
                result = results.get(unit_id)
                if result is None:
                    judgment = {
                        "status": "Review",
                        "source": "missing_llm_result",
                        "parse_error": "Missing yomi triage LLM result.",
                        "raw_text": None,
                        "result_item_id": None,
                    }
                    missing_result_review += 1
                else:
                    used_result_ids.add(unit_id)
                    status, source = yomi_triage_status_from_result(result)
                    judgment = {
                        "status": status,
                        "source": source,
                        "parse_error": result.get("parse_error"),
                        "raw_text": result.get("raw_text"),
                        "result_item_id": result.get("item_id"),
                    }
                    if source == "parse_error":
                        parse_error_review += 1
                    elif status == "OK":
                        llm_ok += 1
                    elif status == "Review":
                        llm_review += 1
                    elif status == "Skip":
                        llm_skip += 1
            unit.setdefault("analysis", {}).setdefault("llm", {})["yomi_triage"] = judgment
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")

    summary = YomiTriageApplySummary(
        read_units=read_units,
        llm_result_count=len(results),
        auto_accepted_ok=auto_accepted_ok,
        llm_ok=llm_ok,
        llm_review=llm_review,
        llm_skip=llm_skip,
        parse_error_review=parse_error_review,
        missing_result_review=missing_result_review,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def load_yomi_triage_results(results_jsonl: Path) -> dict[str, dict[str, Any]]:
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


def yomi_triage_status_from_result(result: dict[str, Any]) -> tuple[str, str]:
    parsed = result.get("parsed")
    if isinstance(parsed, dict) and parsed.get("status") in {"OK", "Review", "Skip"}:
        return str(parsed["status"]), "llm"
    return "Review", "parse_error"


def is_yomi_auto_accepted(unit: dict[str, Any]) -> bool:
    return bool(
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
        .get("auto_accept", {})
        .get("value")
    )


def build_yomi_triage_item(unit: dict[str, Any]) -> dict[str, Any]:
    yomi = (
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
    )
    return {
        "unit_id": str(unit.get("unit_id", "")),
        "text": str(unit.get("text", "")),
        "rendered": str(yomi.get("rendered", "")),
        "auto_accept": yomi.get("auto_accept", {}),
    }
