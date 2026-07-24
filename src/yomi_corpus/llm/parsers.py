from __future__ import annotations

import json
import re
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
ARRAY_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\[.*\])\s*```", re.DOTALL)


def parse_output(text: str, parser_name: str, *, metadata: dict[str, Any] | None = None) -> Any:
    if parser_name == "json_object":
        return parse_json_object(text)
    if parser_name == "json_array":
        return parse_json_array(text)
    if parser_name == "yomi_repair_json_array":
        return parse_yomi_repair_json_array(text, metadata=metadata)
    if parser_name == "yomi_reading_completion_json":
        return parse_yomi_reading_completion_json(text, metadata=metadata)
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
    match = ARRAY_CODE_BLOCK_RE.search(stripped)
    if match:
        parsed = json.loads(match.group(1))
        if isinstance(parsed, list):
            return parsed
    for extracted in extract_json_arrays(stripped):
        try:
            parsed = json.loads(extracted)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    raise ValueError("Expected a JSON array in model output.")


def parse_yomi_repair_json_array(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> list[Any]:
    parsed = parse_json_array(text)
    validate_yomi_repair_surface(parsed, metadata=metadata)
    return parsed


def validate_yomi_repair_surface(
    parsed: object,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    expected = _expected_rejected_span(metadata)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("Yomi repair must return a non-empty JSON array.")
    surfaces: list[str] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Each yomi repair item must be a JSON object.")
        surface = item.get("surface")
        if not isinstance(surface, str) or not surface:
            raise ValueError("Each yomi repair item must have a non-empty surface.")
        if any(char.isspace() for char in surface):
            raise ValueError(
                "Yomi repair items must not span whitespace; return separate items "
                "for each whitespace-delimited component."
            )
        surfaces.append(surface)
    if not repair_surfaces_respect_whitespace_boundaries(surfaces, expected):
        raise ValueError(
            f"Yomi repair surface mismatch: expected {expected!r}, got {''.join(surfaces)!r}."
        )


def repair_surfaces_respect_whitespace_boundaries(
    surfaces: list[str],
    expected: str,
) -> bool:
    components = [part for part in re.split(r"\s+", expected) if part]
    if not components:
        return False
    surface_index = 0
    for component in components:
        consumed = ""
        while len(consumed) < len(component) and surface_index < len(surfaces):
            surface = surfaces[surface_index]
            if not component.startswith(surface, len(consumed)):
                return False
            consumed += surface
            surface_index += 1
        if consumed != component:
            return False
    return surface_index == len(surfaces)


def extract_first_json_object(text: str) -> str | None:
    return next(iter(extract_json_objects(text)), None)


def extract_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        extracted = extract_json_object_at(text, start)
        if extracted is not None:
            objects.append(extracted)
    return objects


def extract_json_object_at(text: str, start: int) -> str | None:
    if start < 0 or start >= len(text) or text[start] != "{":
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
    return next(iter(extract_json_arrays(text)), None)


def extract_json_arrays(text: str) -> list[str]:
    arrays: list[str] = []
    for start, char in enumerate(text):
        if char != "[":
            continue
        extracted = extract_json_array_at(text, start)
        if extracted is not None:
            arrays.append(extracted)
    return arrays


def extract_json_array_at(text: str, start: int) -> str | None:
    if start < 0 or start >= len(text) or text[start] != "[":
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
    surface = _expected_surface(metadata)
    for extracted in extract_json_objects(text.strip()):
        try:
            parsed = json.loads(extracted)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and surface in parsed:
            return parsed
    try:
        return parse_json_object(text)
    except ValueError:
        pass
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


def _expected_rejected_span(metadata: dict[str, Any] | None) -> str:
    if isinstance(metadata, dict):
        rejected_span = metadata.get("rejected_span")
        if isinstance(rejected_span, str) and rejected_span:
            return rejected_span
        source_row = metadata.get("source_row")
        if isinstance(source_row, dict):
            rejected_span = source_row.get("rejected_span")
            if isinstance(rejected_span, str) and rejected_span:
                return rejected_span
            targets = source_row.get("target_escalations")
            if isinstance(targets, list):
                joined = "".join(
                    str(target.get("surface") or "")
                    for target in targets
                    if isinstance(target, dict)
                )
                if joined:
                    return joined
    raise ValueError("Missing rejected span metadata for yomi repair parser.")


def parse_scope_triage_label(text: str) -> dict[str, str]:
    label = text.strip()
    if label not in {"Keep", "Skip", "Exclude"}:
        raise ValueError("Expected exactly one of Keep, Skip, or Exclude.")
    return {"status": label}
