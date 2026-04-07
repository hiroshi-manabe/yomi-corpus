from __future__ import annotations

from dataclasses import dataclass


BOUNDARY_CHARS = {"。", "！", "？", "\n"}


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    text: str


def split_text_into_units(text: str) -> list[TextSpan]:
    spans: list[TextSpan] = []
    start = 0
    index = 0

    while index < len(text):
        char = text[index]
        if char in BOUNDARY_CHARS:
            end = index + 1
            _append_span(text, start, end, spans)
            start = end
        index += 1

    _append_span(text, start, len(text), spans)
    return spans


def _append_span(text: str, start: int, end: int, spans: list[TextSpan]) -> None:
    if start >= end:
        return
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return

    left_trim = len(raw) - len(raw.lstrip())
    right_trim = len(raw) - len(raw.rstrip())
    adj_start = start + left_trim
    adj_end = end - right_trim
    if adj_start >= adj_end:
        return
    spans.append(TextSpan(start=adj_start, end=adj_end, text=text[adj_start:adj_end]))
