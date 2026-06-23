from __future__ import annotations


def format_stage_summary(summary: dict[str, object]) -> str:
    current_stage = summary.get("current_stage") or "-"
    next_stage = summary.get("next_stage") or "-"
    return f"current_stage: {current_stage}\nnext_stage: {next_stage}"
