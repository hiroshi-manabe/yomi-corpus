from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3005\u3006\u303b]")
LATIN_RE = re.compile(r"[A-Za-z\uFF21-\uFF3A\uFF41-\uFF5A]")

AUTO_ACCEPT_RULE = "no_kanji_no_alphabet_no_unresolved_non_numeric_reading_v1"


@dataclass(frozen=True)
class YomiAutoAcceptance:
    value: bool
    rule: str
    signals: list[str]


@dataclass(frozen=True)
class YomiAutoAcceptSummary:
    read: int
    written: int
    accepted: int
    rejected: int
    output_jsonl: str
    summary_json: str
    rule: str


def judge_yomi_auto_accept(unit: dict[str, Any]) -> YomiAutoAcceptance:
    text = str(unit.get("text", ""))
    yomi = (
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
    )
    rendered = yomi.get("rendered")
    signals: list[str] = []

    if not isinstance(rendered, str) or not rendered:
        return YomiAutoAcceptance(
            value=False,
            rule=AUTO_ACCEPT_RULE,
            signals=["missing_yomi"],
        )

    if KANJI_RE.search(text):
        signals.append("contains_kanji")
    else:
        signals.append("no_kanji")

    if LATIN_RE.search(text):
        signals.append("contains_alphabetic")
    else:
        signals.append("no_alphabetic")

    unresolved = unresolved_non_numeric_reading_surfaces(rendered)
    if unresolved:
        signals.append("has_unresolved_non_numeric_reading")
    else:
        signals.append("no_unresolved_non_numeric_reading")

    accepted = (
        "no_kanji" in signals
        and "no_alphabetic" in signals
        and "no_unresolved_non_numeric_reading" in signals
    )
    return YomiAutoAcceptance(
        value=accepted,
        rule=AUTO_ACCEPT_RULE,
        signals=signals,
    )


def unresolved_non_numeric_reading_surfaces(rendered: str) -> list[str]:
    unresolved: list[str] = []
    for pair in rendered.split():
        if "/" not in pair:
            unresolved.append(pair)
            continue
        surface, reading = pair.rsplit("/", 1)
        if reading:
            continue
        if surface and surface.isdecimal():
            continue
        unresolved.append(surface)
    return unresolved


def apply_yomi_auto_acceptance_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> YomiAutoAcceptSummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    read = 0
    written = 0
    accepted = 0
    rejected = 0
    with input_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read += 1
            unit = json.loads(line)
            judgment = judge_yomi_auto_accept(unit)
            unit["analysis"]["mechanical"]["yomi"]["auto_accept"] = asdict(judgment)
            if judgment.value:
                accepted += 1
            else:
                rejected += 1
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written += 1

    summary = YomiAutoAcceptSummary(
        read=read,
        written=written,
        accepted=accepted,
        rejected=rejected,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
        rule=AUTO_ACCEPT_RULE,
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
