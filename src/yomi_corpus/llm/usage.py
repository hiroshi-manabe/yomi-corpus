from __future__ import annotations

from typing import Any


def normalize_usage(payload: Any) -> dict[str, int] | None:
    if payload is None:
        return None

    usage = _object_to_dict(payload)
    if not isinstance(usage, dict):
        return None

    input_tokens = _coerce_int(usage.get("input_tokens"), usage.get("prompt_tokens"))
    output_tokens = _coerce_int(usage.get("output_tokens"), usage.get("completion_tokens"))
    total_tokens = _coerce_int(usage.get("total_tokens"))

    input_details = _object_to_dict(usage.get("input_tokens_details")) or _object_to_dict(usage.get("prompt_tokens_details")) or {}
    output_details = _object_to_dict(usage.get("output_tokens_details")) or _object_to_dict(
        usage.get("completion_tokens_details")
    ) or {}

    cached_input_tokens = _coerce_int(input_details.get("cached_tokens"))
    reasoning_tokens = _coerce_int(output_details.get("reasoning_tokens"))

    normalized = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens or (input_tokens + output_tokens),
    }
    if not any(normalized.values()):
        return None
    return normalized


def usage_from_response(response: Any) -> dict[str, int] | None:
    return normalize_usage(getattr(response, "usage", None))


def usage_from_batch_item(item: dict[str, Any]) -> dict[str, int] | None:
    response = item.get("response") or {}
    body = response.get("body") or {}
    for container in (body, response):
        usage = normalize_usage(container.get("usage"))
        if usage:
            return usage
    return None


def _coerce_int(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _object_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value
