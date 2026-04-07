from __future__ import annotations

from collections import Counter
from typing import Any


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

    if task_name in {"alphabetic_entity_judge", "classical_japanese_judge", "yomi_check"}:
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
        expected_rendered = eval_row.get("expected_rendered")
        actual_rendered = parsed.get("rendered") if isinstance(parsed, dict) else None
        return {
            "passed": actual_rendered == expected_rendered,
            "parse_error": None,
            "expected": {"rendered": expected_rendered},
            "actual": {"rendered": actual_rendered},
            "notes": [],
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
        return {"rendered": eval_row.get("expected_rendered")}
    return {"status": eval_row.get("expected_status")}
