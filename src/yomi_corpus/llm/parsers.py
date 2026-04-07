from __future__ import annotations

import json
import re
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def parse_output(text: str, parser_name: str) -> Any:
    if parser_name == "json_object":
        return parse_json_object(text)
    raise ValueError(f"Unsupported parser: {parser_name}")


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    match = CODE_BLOCK_RE.search(stripped)
    if match:
        return json.loads(match.group(1))
    raise ValueError("Expected a JSON object in model output.")
