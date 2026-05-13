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
