from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.token_codec import (
    editable_rendered_to_yomi_tokens,
    yomi_tokens_to_legacy_rendered,
)


@dataclass(frozen=True)
class LearnedRewriteResult:
    rendered: str
    applications: tuple[dict[str, Any], ...]


def load_exact_yomi_rewrites(path: str | Path | None) -> dict[str, tuple[tuple[str, str], ...]]:
    if path is None:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    stat = source.stat()
    return dict(_load_exact_yomi_rewrites_cached(str(source.resolve()), stat.st_mtime_ns, stat.st_size))


@lru_cache(maxsize=8)
def _load_exact_yomi_rewrites_cached(
    path: str,
    _mtime_ns: int,
    _size: int,
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    source = Path(path)
    rewrites: dict[str, tuple[tuple[str, str], ...]] = {}
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            original_surface = str(row.get("original_surface") or "")
            replacement = tuple(
                (str(item.get("surface") or ""), str(item.get("reading") or ""))
                for item in row.get("replacement", [])
                if isinstance(item, dict)
            )
            if (
                not original_surface
                or not replacement
                or any(not surface for surface, _reading in replacement)
                or "".join(surface for surface, _reading in replacement) != original_surface
            ):
                raise ValueError(f"Invalid exact yomi rewrite at {source}:{line_number}")
            previous = rewrites.get(original_surface)
            if previous is not None and previous != replacement:
                raise ValueError(f"Conflicting exact yomi rewrites for {original_surface!r} in {source}")
            rewrites[original_surface] = replacement
    return tuple(sorted(rewrites.items()))


def apply_exact_yomi_rewrites(
    rendered: str,
    *,
    rewrites_path: str | Path | None,
) -> LearnedRewriteResult:
    rewrites = load_exact_yomi_rewrites(rewrites_path)
    if not rewrites:
        return LearnedRewriteResult(rendered=rendered, applications=())
    tokens = editable_rendered_to_yomi_tokens(rendered)
    ordered_surfaces = sorted(rewrites, key=lambda value: (-len(value), value))
    output: list[list[str]] = []
    applications: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        matched: tuple[str, int] | None = None
        for original_surface in ordered_surfaces:
            combined = ""
            end = index
            while end < len(tokens) and len(combined) < len(original_surface):
                combined += tokens[end][0]
                end += 1
            if combined == original_surface:
                matched = (original_surface, end)
                break
        if matched is None:
            output.append(tokens[index])
            index += 1
            continue
        original_surface, end = matched
        replacement = rewrites[original_surface]
        output.extend([[surface, reading] for surface, reading in replacement])
        applications.append(
            {
                "original_surface": original_surface,
                "original_tokens": tokens[index:end],
                "replacement": [[surface, reading] for surface, reading in replacement],
            }
        )
        index = end
    if not applications:
        return LearnedRewriteResult(rendered=rendered, applications=())
    return LearnedRewriteResult(
        rendered=yomi_tokens_to_legacy_rendered(output),
        applications=tuple(applications),
    )
