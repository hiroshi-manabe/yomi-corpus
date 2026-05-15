from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


YOMI_UNIT_MODE_SENTENCE = "sentence"
YOMI_UNIT_MODE_COMMA_SPAN = "comma_span"
YOMI_UNIT_MODES = frozenset({YOMI_UNIT_MODE_SENTENCE, YOMI_UNIT_MODE_COMMA_SPAN})


@dataclass(frozen=True)
class YomiTriageQueueSummary:
    read: int
    queued: int
    skipped_auto_accepted: int
    unit_mode: str
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
    unit_mode: str
    output_jsonl: str
    summary_json: str


def build_yomi_triage_queue_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    unit_mode: str = YOMI_UNIT_MODE_SENTENCE,
) -> YomiTriageQueueSummary:
    validate_yomi_unit_mode(unit_mode)
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
            items = build_yomi_triage_items(unit, unit_mode=unit_mode)
            for item in items:
                dst.write(json.dumps(item, ensure_ascii=False) + "\n")
            queued += len(items)

    summary = YomiTriageQueueSummary(
        read=read,
        queued=queued,
        skipped_auto_accepted=skipped_auto_accepted,
        unit_mode=unit_mode,
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
    unit_mode: str = YOMI_UNIT_MODE_SENTENCE,
) -> YomiTriageApplySummary:
    validate_yomi_unit_mode(unit_mode)
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
            elif unit_mode == YOMI_UNIT_MODE_SENTENCE:
                (
                    judgment,
                    llm_ok_delta,
                    llm_review_delta,
                    llm_skip_delta,
                    parse_error_delta,
                    missing_delta,
                    used_result_id,
                ) = build_sentence_judgment_from_results(unit_id, results)
                llm_ok += llm_ok_delta
                llm_review += llm_review_delta
                llm_skip += llm_skip_delta
                parse_error_review += parse_error_delta
                missing_result_review += missing_delta
                if used_result_id:
                    used_result_ids.add(used_result_id)
            else:
                (
                    judgment,
                    llm_ok_delta,
                    llm_review_delta,
                    llm_skip_delta,
                    parse_error_delta,
                    missing_delta,
                    used_span_ids,
                ) = build_span_aggregate_judgment(unit, results)
                llm_ok += llm_ok_delta
                llm_review += llm_review_delta
                llm_skip += llm_skip_delta
                parse_error_review += parse_error_delta
                missing_result_review += missing_delta
                used_result_ids.update(used_span_ids)
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
        unit_mode=unit_mode,
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


def build_sentence_judgment_from_results(
    item_id: str,
    results: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], int, int, int, int, int, str | None]:
    result = results.get(item_id)
    if result is None:
        return (
            {
                "status": "Review",
                "source": "missing_llm_result",
                "parse_error": "Missing yomi triage LLM result.",
                "raw_text": None,
                "result_item_id": None,
            },
            0,
            0,
            0,
            0,
            1,
            None,
        )

    status, source = yomi_triage_status_from_result(result)
    return (
        {
            "status": status,
            "source": source,
            "parse_error": result.get("parse_error"),
            "raw_text": result.get("raw_text"),
            "result_item_id": result.get("item_id"),
        },
        1 if source != "parse_error" and status == "OK" else 0,
        1 if source != "parse_error" and status == "Review" else 0,
        1 if source != "parse_error" and status == "Skip" else 0,
        1 if source == "parse_error" else 0,
        0,
        item_id,
    )


def build_span_aggregate_judgment(
    unit: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], int, int, int, int, int, set[str]]:
    span_judgments = []
    llm_ok = 0
    llm_review = 0
    llm_skip = 0
    parse_error_review = 0
    missing_result_review = 0
    used_result_ids: set[str] = set()

    for item in build_yomi_triage_items(unit, unit_mode=YOMI_UNIT_MODE_COMMA_SPAN):
        span_id = str(item["unit_id"])
        (
            base_judgment,
            ok_delta,
            review_delta,
            skip_delta,
            parse_error_delta,
            missing_delta,
            used_result_id,
        ) = build_sentence_judgment_from_results(span_id, results)
        llm_ok += ok_delta
        llm_review += review_delta
        llm_skip += skip_delta
        parse_error_review += parse_error_delta
        missing_result_review += missing_delta
        if used_result_id:
            used_result_ids.add(used_result_id)
        span_judgments.append(
            {
                "span_id": span_id,
                "span_seq": item.get("span_seq"),
                "span_count": item.get("span_count"),
                "text": item.get("text"),
                "rendered": item.get("rendered"),
                **base_judgment,
            }
        )

    aggregate_status = aggregate_span_statuses(
        [str(span["status"]) for span in span_judgments]
    )
    return (
        {
            "status": aggregate_status,
            "source": "span_aggregate",
            "parse_error": None,
            "raw_text": None,
            "result_item_id": None,
            "unit_mode": YOMI_UNIT_MODE_COMMA_SPAN,
            "spans": span_judgments,
        },
        llm_ok,
        llm_review,
        llm_skip,
        parse_error_review,
        missing_result_review,
        used_result_ids,
    )


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


def build_yomi_triage_items(
    unit: dict[str, Any],
    *,
    unit_mode: str = YOMI_UNIT_MODE_SENTENCE,
) -> list[dict[str, Any]]:
    validate_yomi_unit_mode(unit_mode)
    if unit_mode == YOMI_UNIT_MODE_SENTENCE:
        return [build_yomi_triage_item(unit)]

    base = build_yomi_triage_item(unit)
    text_spans = split_text_at_japanese_commas(base["text"])
    rendered_spans = split_rendered_at_japanese_commas(base["rendered"])
    if len(text_spans) != len(rendered_spans):
        item = dict(base)
        item["unit_mode"] = unit_mode
        item["parent_unit_id"] = base["unit_id"]
        item["span_id"] = base["unit_id"]
        item["span_seq"] = 1
        item["span_count"] = 1
        item["span_split_fallback"] = True
        return [item]

    items = []
    for index, (text, rendered) in enumerate(zip(text_spans, rendered_spans, strict=True), start=1):
        span_id = f"{base['unit_id']}:s{index:04d}"
        item = {
            **base,
            "unit_id": span_id,
            "span_id": span_id,
            "parent_unit_id": base["unit_id"],
            "span_seq": index,
            "span_count": len(text_spans),
            "unit_mode": unit_mode,
            "text": text,
            "rendered": rendered,
            "parent_text": base["text"],
            "parent_rendered": base["rendered"],
        }
        if index > 1:
            item["previous_span_text"] = text_spans[index - 2]
        if index < len(text_spans):
            item["next_span_text"] = text_spans[index]
        items.append(item)
    return items


def split_text_at_japanese_commas(text: str) -> list[str]:
    if not text:
        return [text]
    spans = []
    start = 0
    for index, char in enumerate(text):
        if char != "、":
            continue
        spans.append(text[start : index + 1])
        start = index + 1
    if start < len(text):
        spans.append(text[start:])
    return spans or [text]


def split_rendered_at_japanese_commas(rendered: str) -> list[str]:
    if not rendered:
        return [rendered]
    spans: list[list[str]] = [[]]
    for pair in rendered.split():
        spans[-1].append(pair)
        surface = pair.rsplit("/", 1)[0] if "/" in pair else pair
        if surface == "、":
            spans.append([])
    if not spans[-1]:
        spans.pop()
    return [" ".join(span) for span in spans] or [rendered]


def aggregate_span_statuses(statuses: list[str]) -> str:
    if any(status == "Skip" for status in statuses):
        return "Skip"
    if any(status == "Review" for status in statuses):
        return "Review"
    return "OK"


def validate_yomi_unit_mode(unit_mode: str) -> None:
    if unit_mode not in YOMI_UNIT_MODES:
        raise ValueError(f"Unsupported yomi triage unit mode: {unit_mode}")
