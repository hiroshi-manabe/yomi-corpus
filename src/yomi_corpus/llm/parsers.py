from __future__ import annotations

import json
import re
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def parse_output(text: str, parser_name: str) -> Any:
    if parser_name == "json_object":
        return parse_json_object(text)
    if parser_name == "yomi_triage_label":
        return parse_yomi_triage_label(text)
    if parser_name == "yomi_triage_reasoned_label":
        return parse_yomi_triage_reasoned_label(text)
    if parser_name == "scope_triage_label":
        return parse_scope_triage_label(text)
    raise ValueError(f"Unsupported parser: {parser_name}")


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    match = CODE_BLOCK_RE.search(stripped)
    if match:
        return json.loads(match.group(1))
    raise ValueError("Expected a JSON object in model output.")


def parse_yomi_triage_label(text: str) -> dict[str, str]:
    label = text.strip()
    if label not in {"OK", "Review", "Skip"}:
        raise ValueError("Expected exactly one of OK, Review, or Skip.")
    return {"status": label}


def parse_yomi_triage_reasoned_label(text: str) -> dict[str, str]:
    reason: str | None = None
    answer: str | None = None
    for line in text.strip().splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        normalized_key = key.strip().lower()
        if normalized_key == "reason":
            reason = value.strip()
        elif normalized_key == "answer":
            answer = value.strip()
    if answer not in {"OK", "Review", "Skip"}:
        raise ValueError("Expected an Answer line with OK, Review, or Skip.")
    return {"status": answer, "reason": reason or ""}


def parse_scope_triage_label(text: str) -> dict[str, str]:
    label = text.strip()
    if label not in {"Keep", "Skip"}:
        raise ValueError("Expected exactly one of Keep or Skip.")
    return {"status": label}
