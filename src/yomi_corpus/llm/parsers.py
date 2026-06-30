from __future__ import annotations

import json
import re
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def parse_output(text: str, parser_name: str, *, metadata: dict[str, Any] | None = None) -> Any:
    if parser_name == "json_object":
        return parse_json_object(text)
    if parser_name == "json_array":
        return parse_json_array(text)
    if parser_name == "yomi_reading_completion_json":
        return parse_yomi_reading_completion_json(text, metadata=metadata)
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
    extracted = extract_first_json_object(stripped)
    if extracted is not None:
        return json.loads(extracted)
    raise ValueError("Expected a JSON object in model output.")


def parse_json_array(text: str) -> list[Any]:
    stripped = text.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
        raise ValueError("Expected a JSON array in model output.")
    extracted = extract_first_json_array(stripped)
    if extracted is not None:
        parsed = json.loads(extracted)
        if isinstance(parsed, list):
            return parsed
    raise ValueError("Expected a JSON array in model output.")


def extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_first_json_array(text: str) -> str | None:
    start = text.find("[")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_yomi_reading_completion_json(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return parse_json_object(text)
    except ValueError:
        pass
    surface = _expected_surface(metadata)
    stripped = text.strip()
    try:
        parsed_string = json.loads(stripped)
    except json.JSONDecodeError:
        parsed_string = None
    if isinstance(parsed_string, str):
        return {surface: parsed_string}
    candidate = "{" + json.dumps(surface, ensure_ascii=False) + ":" + stripped
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        if _looks_like_bare_reading(stripped):
            return {surface: stripped}
        raise ValueError("Expected a JSON object, a completion like \"よみ\"}, or a bare reading.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object after completing model output.")
    return parsed


def _looks_like_bare_reading(text: str) -> bool:
    return bool(text) and "\n" not in text and not any(char in text for char in "{}[]:,")


def _expected_surface(metadata: dict[str, Any] | None) -> str:
    if isinstance(metadata, dict):
        surface = metadata.get("surface")
        if isinstance(surface, str) and surface:
            return surface
        source_row = metadata.get("source_row")
        if isinstance(source_row, dict):
            surface = source_row.get("surface")
            if isinstance(surface, str) and surface:
                return surface
    raise ValueError("Missing expected surface metadata for completion-style yomi parser.")


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
