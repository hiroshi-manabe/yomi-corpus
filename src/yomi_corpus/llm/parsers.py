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
    if label not in {"OK", "FIX", "SKIP"}:
        raise ValueError("Expected exactly one of OK, FIX, or SKIP.")
    return {"status": label}
