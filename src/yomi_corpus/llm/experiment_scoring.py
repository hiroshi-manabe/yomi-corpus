from __future__ import annotations

from collections import Counter
from typing import Any

from yomi_corpus.yomi.llm_readings import normalize_hiragana_reading


def score_output(
    *,
    task_name: str,
    eval_row: dict[str, Any],
    parsed: Any,
    parse_error: str | None = None,
) -> dict[str, Any]:
    if parse_error:
        return {
            "passed": False,
            "parse_error": parse_error,
            "expected": _expected_payload(task_name, eval_row),
            "actual": None,
            "notes": ["parse_error"],
        }

    if task_name in {
        "yomi_check",
        "yomi_review_resolution",
    }:
        expected_status = eval_row.get("expected_status")
        actual_status = parsed.get("status") if isinstance(parsed, dict) else None
        return {
            "passed": actual_status == expected_status,
            "parse_error": None,
            "expected": {"status": expected_status},
            "actual": {"status": actual_status},
            "notes": [],
        }

    if task_name == "yomi_repair":
        expected_segments = eval_row.get("expected_segments")
        if expected_segments is not None:
            normalized_expected = _normalize_repair_segments(expected_segments)
            normalized_actual = _normalize_repair_segments(parsed)
            return {
                "passed": normalized_expected is not None and normalized_expected == normalized_actual,
                "parse_error": None,
                "expected": {"segments": normalized_expected},
                "actual": {"segments": normalized_actual},
                "notes": [] if normalized_expected is not None else ["invalid_expected_segments"],
            }

        expected_rendered = eval_row.get("expected_rendered")
        actual_rendered = parsed.get("rendered") if isinstance(parsed, dict) else None
        if expected_rendered is None:
            return {
                "passed": False,
                "parse_error": None,
                "expected": {"rendered": None},
                "actual": {"rendered": actual_rendered},
                "notes": ["missing_expected_repair"],
            }
        return {
            "passed": actual_rendered == expected_rendered,
            "parse_error": None,
            "expected": {"rendered": expected_rendered},
            "actual": {"rendered": actual_rendered},
            "notes": [],
        }

    if task_name == "yomi_reading":
        expected_surface = str(eval_row.get("surface", ""))
        expected_reading = normalize_hiragana_reading(str(eval_row.get("expected_reading", "")))
        acceptable_readings = {
            normalize_hiragana_reading(str(reading))
            for reading in eval_row.get("acceptable_readings", [])
            if isinstance(reading, str) and reading
        }
        acceptable_readings.add(expected_reading)
        actual_reading = None
        notes: list[str] = []
        if isinstance(parsed, dict):
            keys = set(str(key) for key in parsed)
            if expected_surface not in keys:
                notes.append("wrong_json_keys")
            elif keys != {expected_surface}:
                notes.append("extra_json_keys")
            value = parsed.get(expected_surface)
            if isinstance(value, str):
                actual_reading = normalize_hiragana_reading(value)
            else:
                notes.append("missing_or_non_string_reading")
        else:
            notes.append("parsed_result_is_not_object")
        return {
            "passed": actual_reading in acceptable_readings and "wrong_json_keys" not in notes
            and "missing_or_non_string_reading" not in notes
            and "parsed_result_is_not_object" not in notes,
            "parse_error": None,
            "expected": {
                "surface": expected_surface,
                "reading": expected_reading,
                "acceptable_readings": sorted(acceptable_readings),
            },
            "actual": {"reading": actual_reading},
            "notes": notes,
        }

    raise ValueError(f"Unsupported scoring task: {task_name}")


def summarize_scores(scored_rows: list[dict[str, Any]]) -> dict[str, Any]:
    item_count = len(scored_rows)
    pass_count = sum(1 for row in scored_rows if row.get("passed"))
    parse_error_count = sum(1 for row in scored_rows if row.get("parse_error"))
    fail_count = item_count - pass_count

    expected_counts: Counter[str] = Counter()
    actual_counts: Counter[str] = Counter()
    for row in scored_rows:
        expected = row.get("expected") or {}
        actual = row.get("actual") or {}
        expected_status = expected.get("status")
        actual_status = actual.get("status")
        if expected_status:
            expected_counts[str(expected_status)] += 1
        if actual_status:
            actual_counts[str(actual_status)] += 1

    accuracy = (pass_count / item_count) if item_count else 0.0
    return {
        "item_count": item_count,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "parse_error_count": parse_error_count,
        "accuracy": accuracy,
        "expected_status_counts": dict(expected_counts),
        "actual_status_counts": dict(actual_counts),
    }


def _expected_payload(task_name: str, eval_row: dict[str, Any]) -> dict[str, Any]:
    if task_name == "yomi_repair":
        if "expected_segments" in eval_row:
            return {"segments": _normalize_repair_segments(eval_row.get("expected_segments"))}
        return {"rendered": eval_row.get("expected_rendered")}
    if task_name == "yomi_reading":
        payload = {
            "surface": eval_row.get("surface"),
            "reading": eval_row.get("expected_reading"),
        }
        if eval_row.get("acceptable_readings"):
            payload["acceptable_readings"] = eval_row["acceptable_readings"]
        return payload
    return {"status": eval_row.get("expected_status")}


def _normalize_repair_segments(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    normalized: list[dict[str, str]] = []
    for segment in value:
        if not isinstance(segment, dict):
            return None
        surface = segment.get("surface")
        reading = segment.get("reading")
        if not isinstance(surface, str) or not isinstance(reading, str):
            return None
        normalized.append(
            {
                "surface": surface,
                "reading": normalize_hiragana_reading(reading),
            }
        )
    return normalized
