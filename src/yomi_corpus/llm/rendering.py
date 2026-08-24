from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from yomi_corpus.yomi.furigana import has_greek, has_han, is_han


LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")
SOURCE_PAREN_ESCAPES = {
    "（": "-LRB-",
    "）": "-RRB-",
    "(": "-lrb-",
    ")": "-rrb-",
}
ASCII_SPACE = " "
NBSP = "\u00a0"


def rendered_for_llm(rendered: str, display: str = "full") -> str:
    if display == "full":
        return rendered
    if display == "compact":
        return compact_rendered_for_llm(rendered)
    if display == "furigana_no_space":
        return furigana_no_space_rendered_for_llm(rendered)
    raise ValueError(f"Unsupported rendered_yomi_display: {display}")


def compact_rendered_for_llm(rendered: str) -> str:
    compacted: list[str] = []
    for token in rendered_tokens(rendered):
        compacted.append(compact_rendered_token_for_llm(token))
    return " ".join(compacted)


def compact_rendered_token_for_llm(token: str) -> str:
    if "/" not in token:
        return token
    surface, _reading = token.rsplit("/", 1)
    if not surface:
        return token
    if has_han(surface) or has_greek(surface) or LATIN_RE.search(surface):
        return token
    return surface


def furigana_no_space_rendered_for_llm(rendered: str) -> str:
    return "".join(furigana_no_space_token_for_llm(token) for token in rendered_tokens(rendered))


def furigana_no_space_token_for_llm(token: str) -> str:
    if "/" not in token:
        return escape_source_parentheses(token)
    surface, reading = token.rsplit("/", 1)
    if not surface:
        return token
    prefix = "|" if is_fused_digit_yomi_token(surface, reading) else ""
    if not reading:
        return escape_source_parentheses(surface)
    if (
        has_han(surface) or has_greek(surface) or LATIN_RE.search(surface)
    ) and not is_katakana_reading(reading):
        return escape_source_parentheses(surface)
    if has_han(surface):
        result = _furigana_converter().convert(surface, reading)
        # Some dictionary rows preserve the surface but provide no ruby placement.
        if result.annotated_surface and "（" in result.annotated_surface:
            return prefix + escape_source_parentheses_in_annotated(result.annotated_surface)
        return f"{prefix}{escape_source_parentheses(surface)}（{_kata_to_hira(reading)}）"
    if has_greek(surface) or LATIN_RE.search(surface):
        return f"{prefix}{escape_source_parentheses(surface)}（{reading}）"
    return escape_source_parentheses(surface)


def escape_source_parentheses(text: str) -> str:
    return "".join(SOURCE_PAREN_ESCAPES.get(char, char) for char in text)


def is_fused_digit_yomi_token(surface: str, reading: str) -> bool:
    return bool(reading) and any(_is_digit(char) for char in surface) and not all(_is_digit(char) for char in surface)


def escape_source_parentheses_in_annotated(text: str) -> str:
    output: list[str] = []
    index = 0
    for match in re.finditer(r"（[^（）]*）", text):
        prefix = text[index : match.start()]
        if prefix and is_han(prefix[-1]):
            output.append(escape_source_parentheses(prefix))
            output.append(match.group(0))
        else:
            output.append(escape_source_parentheses(text[index : match.end()]))
        index = match.end()
    output.append(escape_source_parentheses(text[index:]))
    return "".join(output)


@lru_cache(maxsize=1)
def _furigana_converter():
    from yomi_corpus.yomi.furigana import FuriganaConverter
    from yomi_corpus.paths import resolve_repo_path

    lookup = resolve_repo_path("data/external/sudachi_annotated_forms/sudachi_20251022.tsv")
    supplemental = resolve_repo_path("data/lexicon/supplemental_furigana.tsv")
    return FuriganaConverter.from_tsv_many([Path(lookup), Path(supplemental)])


def _kata_to_hira(text: str) -> str:
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9" or "０" <= char <= "９"


def is_katakana_reading(reading: str) -> bool:
    return bool(reading) and all(_is_katakana(char) or char == "ー" for char in reading)


def _is_katakana(char: str) -> bool:
    return "\u30a1" <= char <= "\u30fa"


def rendered_tokens(rendered: str) -> list[str]:
    return [token for token in rendered.split(" ") if token]


def restore_source_whitespace_tokens(text: str, rendered: str) -> tuple[str, list[str]]:
    """Insert explicit whitespace tokens into an existing rendered yomi string.

    This preserves all non-whitespace rendered tokens and their readings. It is
    meant for refreshing hand-curated eval rows after whitespace preservation was
    added to the canonical yomi format.
    """
    warnings: list[str] = []
    output: list[str] = []
    cursor = 0
    tokens = [token for token in rendered_tokens(rendered) if not is_rendered_whitespace_pair(token)]

    for token in tokens:
        surface = rendered_token_surface(token)
        if not surface:
            output.append(token)
            continue
        index = text.find(surface, cursor)
        if index < 0:
            warnings.append(f"surface not found after offset {cursor}: {surface!r}")
            return rendered, warnings
        gap = text[cursor:index]
        if gap:
            if not all(char.isspace() for char in gap):
                warnings.append(f"non-whitespace source gap at offset {cursor}: {gap!r}")
                return rendered, warnings
            output.extend(render_whitespace_pair(char) for char in gap)
        output.append(token)
        cursor = index + len(surface)

    tail = text[cursor:]
    if tail:
        if not all(char.isspace() for char in tail):
            warnings.append(f"non-whitespace source tail at offset {cursor}: {tail!r}")
            return rendered, warnings
        output.extend(render_whitespace_pair(char) for char in tail)
    return " ".join(output), warnings


def rendered_token_surface(token: str) -> str:
    if token == "///":
        return "/"
    if "/" not in token:
        return token
    surface, _reading = token.rsplit("/", 1)
    return surface


def is_rendered_whitespace_pair(token: str) -> bool:
    if "/" not in token:
        return False
    surface, reading = token.rsplit("/", 1)
    return bool(surface) and surface == reading and surface.isspace()


def render_whitespace_pair(char: str) -> str:
    surface = NBSP if char == ASCII_SPACE else char
    return f"{surface}/{surface}"
