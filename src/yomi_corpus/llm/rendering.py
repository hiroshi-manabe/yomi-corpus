from __future__ import annotations

import re

KANJI_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")


def rendered_for_llm(rendered: str, display: str = "full") -> str:
    if display == "full":
        return rendered
    if display == "compact":
        return compact_rendered_for_llm(rendered)
    raise ValueError(f"Unsupported rendered_yomi_display: {display}")


def compact_rendered_for_llm(rendered: str) -> str:
    compacted: list[str] = []
    for token in rendered.split():
        compacted.append(compact_rendered_token_for_llm(token))
    return " ".join(compacted)


def compact_rendered_token_for_llm(token: str) -> str:
    if "/" not in token:
        return token
    surface, _reading = token.rsplit("/", 1)
    if not surface:
        return token
    if KANJI_RE.search(surface) or LATIN_RE.search(surface):
        return token
    return surface
