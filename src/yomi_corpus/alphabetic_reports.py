from __future__ import annotations

import json
from pathlib import Path
import re

BOUNDARY_CHARS = " \t\u3000「」『』（）()[]{}<>＜＞【】.,!?！？、。:：;；/\\|\""
MULTISPACE_RE = re.compile(r"\s+")


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def build_unresolved_entity_rows(
    rows: list[dict],
    *,
    min_occurrences: int = 1,
    max_examples: int = 3,
    max_example_chars: int = 160,
) -> list[dict]:
    unresolved: list[dict] = []
    for row in rows:
        if row.get("resolved_status") != "unknown":
            continue
        occurrence_count = int(row.get("occurrence_count", 0))
        if occurrence_count < min_occurrences:
            continue
        unresolved.append(
            {
                "entity_key": str(row["entity_key"]),
                "strict_case": bool(row.get("strict_case", False)),
                "resolved_status": str(row.get("resolved_status", "unknown")),
                "base_list_status": str(row.get("base_list_status", "unknown")),
                "occurrence_count": occurrence_count,
                "unit_count": int(row.get("unit_count", 0)),
                "surface_forms": list(row.get("surface_forms", [])),
                "example_unit_ids": list(row.get("example_unit_ids", []))[:max_examples],
                "example_texts": [
                    shorten_example_text(
                        text,
                        entity_text_candidates=list(row.get("surface_forms", [])) + [str(row["entity_key"])],
                        max_chars=max_example_chars,
                    )
                    for text in list(row.get("example_texts", []))[:max_examples]
                ],
            }
        )

    unresolved.sort(
        key=lambda row: (-row["occurrence_count"], -row["unit_count"], row["entity_key"])
    )
    return unresolved


def shorten_example_text(
    text: str,
    *,
    entity_text_candidates: list[str],
    max_chars: int,
) -> str:
    clean_text = MULTISPACE_RE.sub(" ", text).strip()
    if len(clean_text) <= max_chars:
        return clean_text

    match = _find_first_candidate_span(clean_text, entity_text_candidates)
    if match is None:
        return _truncate_without_match(clean_text, max_chars)

    start, end = match
    window_start = max(0, start - max_chars // 2)
    window_end = min(len(clean_text), end + max_chars // 2)

    if window_end - window_start > max_chars:
        excess = (window_end - window_start) - max_chars
        shift_left = min(excess // 2, window_start)
        shift_right = excess - shift_left
        window_start += shift_left
        window_end -= shift_right

    window_start = _adjust_left_boundary(clean_text, window_start)
    window_end = _adjust_right_boundary(clean_text, window_end)
    snippet = clean_text[window_start:window_end].strip()
    if window_start > 0:
        snippet = "..." + snippet
    if window_end < len(clean_text):
        snippet = snippet + "..."
    return snippet


def _find_first_candidate_span(text: str, candidates: list[str]) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        idx = text.find(candidate)
        if idx >= 0:
            span = (idx, idx + len(candidate))
            if best is None or span[0] < best[0]:
                best = span
    return best


def _truncate_without_match(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = _adjust_right_boundary(text, max_chars - 3)
    return text[:cut].strip() + "..."


def _adjust_left_boundary(text: str, index: int) -> int:
    if index <= 0:
        return 0
    while index < len(text) and text[index] not in BOUNDARY_CHARS:
        index += 1
    while index < len(text) and text[index] in BOUNDARY_CHARS:
        index += 1
    return min(index, len(text))


def _adjust_right_boundary(text: str, index: int) -> int:
    if index >= len(text):
        return len(text)
    while index > 0 and text[index - 1] not in BOUNDARY_CHARS:
        index -= 1
    return max(index, 0)
