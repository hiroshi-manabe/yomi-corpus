from __future__ import annotations

import re
from typing import Any, Iterable

from yomi_corpus.yomi.furigana import is_variation_selector


YOMI_TOKEN_SCHEMA_VERSION = 1
_ASCII_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")


class YomiTokenError(ValueError):
    pass


def split_ascii_rendered_tokens(rendered: str) -> list[str]:
    """Split serialized tokens without consuming Unicode source whitespace."""
    return [
        token
        for token in _ASCII_WHITESPACE_RE.split(rendered.strip(" \t\r\n"))
        if token
    ]


def normalize_yomi_tokens(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        raise YomiTokenError("yomi tokens must be an array")
    normalized: list[list[str]] = []
    for index, token in enumerate(value):
        if not isinstance(token, list) or len(token) != 2:
            raise YomiTokenError(f"yomi token {index} must be a two-item array")
        surface, reading = token
        if not isinstance(surface, str) or not isinstance(reading, str):
            raise YomiTokenError(f"yomi token {index} values must be strings")
        if not surface:
            raise YomiTokenError(f"yomi token {index} has an empty surface")
        if all(is_variation_selector(char) for char in surface):
            if not normalized:
                raise YomiTokenError(
                    f"yomi token {index} has a variation selector without a base surface"
                )
            normalized[-1][0] += surface
            continue
        normalized.append([surface, reading])
    return normalized


def canonicalize_whitespace_readings(tokens: Iterable[Iterable[str]]) -> list[list[str]]:
    normalized = normalize_yomi_tokens([list(token) for token in tokens])
    return [
        [surface, "" if surface.isspace() else reading]
        for surface, reading in normalized
    ]


def yomi_tokens_from_mapping(yomi: dict[str, Any], *, text: str | None = None) -> list[list[str]]:
    if "tokens" in yomi:
        tokens = normalize_yomi_tokens(yomi["tokens"])
        validate_yomi_token_surfaces(tokens, text=text)
        return tokens
    rendered = yomi.get("rendered")
    if not isinstance(rendered, str) or not rendered.strip():
        return []
    return legacy_rendered_to_yomi_tokens(rendered, text=text)


def set_canonical_yomi_tokens(yomi: dict[str, Any], tokens: Iterable[Iterable[str]]) -> None:
    normalized = canonicalize_whitespace_readings(tokens)
    yomi["token_schema_version"] = YOMI_TOKEN_SCHEMA_VERSION
    yomi["tokens"] = normalized
    yomi.pop("rendered", None)


def validate_yomi_token_surfaces(tokens: list[list[str]], *, text: str | None) -> None:
    if text is None:
        return
    surface_text = "".join(surface for surface, _reading in tokens)
    if surface_text != text:
        raise YomiTokenError(
            f"token surfaces do not reproduce source text: got {surface_text!r}, expected {text!r}"
        )


def legacy_rendered_to_yomi_tokens(rendered: str, *, text: str | None = None) -> list[list[str]]:
    raw_tokens = split_ascii_rendered_tokens(rendered)
    tokens: list[list[str]] = []
    cursor = 0
    for index, raw_token in enumerate(raw_tokens):
        if (
            raw_token.startswith("/")
            and raw_token != "/"
            and "/" not in raw_token[1:]
        ):
            # Older Sudachi output could expand one compatibility character
            # into visible + empty-surface morphemes, serialized as e.g.
            # ``⑴/⑴ /イチ /``. Recover the useful reading on the visible token.
            continuation_reading = raw_token[1:]
            if tokens and continuation_reading:
                previous_surface, previous_reading = tokens[-1]
                if previous_reading in {"", previous_surface, "キゴウ"}:
                    tokens[-1][1] = continuation_reading
            continue
        if raw_token == "/" and text is not None:
            remaining = text[cursor:]
            if remaining and remaining[0].isspace() and not has_following_explicit_whitespace_token(raw_tokens, index):
                tokens.append([remaining[0], ""])
                cursor += 1
            # A bare slash with no corresponding source whitespace is a
            # phantom empty token produced by the legacy serializer.
            continue
        surface, reading = split_legacy_rendered_token(
            raw_token,
            remaining_text=None if text is None else text[cursor:],
        )
        if text is not None:
            source_surface = text[cursor : cursor + len(surface)]
            if equivalent_source_surface(surface, source_surface):
                surface = source_surface
            else:
                raise YomiTokenError(
                    f"legacy token {index} surface {surface!r} does not match source at offset {cursor}"
                )
            cursor += len(surface)
        tokens.append([surface, reading])
    normalized = normalize_yomi_tokens(tokens)
    validate_yomi_token_surfaces(normalized, text=text)
    return normalized


def has_following_explicit_whitespace_token(raw_tokens: list[str], index: int) -> bool:
    for candidate in raw_tokens[index + 1 :]:
        if candidate == "/":
            continue
        surface, _reading = split_legacy_rendered_token(candidate)
        return bool(surface and all(char.isspace() for char in surface))
    return False


def split_legacy_rendered_token(token: str, *, remaining_text: str | None = None) -> tuple[str, str]:
    separators = [index for index, char in enumerate(token) if char == "/" and index > 0]
    if remaining_text is not None:
        matches = [
            (token[:index], token[index + 1 :])
            for index in separators
            if equivalent_source_surface(token[:index], remaining_text[:index])
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            longest = max(matches, key=lambda pair: len(pair[0]))
            if sum(len(surface) == len(longest[0]) for surface, _reading in matches) == 1:
                return longest
            raise YomiTokenError(f"ambiguous legacy token {token!r}")
    if not separators:
        return token, ""
    separator = separators[-1]
    return token[:separator], token[separator + 1 :]


def equivalent_source_surface(encoded: str, source: str) -> bool:
    if len(encoded) != len(source):
        return False
    return all(
        left == right or (left == "\u00a0" and right == " ")
        for left, right in zip(encoded, source, strict=True)
    )


def yomi_tokens_to_legacy_rendered(tokens: Iterable[Iterable[str]]) -> str:
    normalized = normalize_yomi_tokens([list(token) for token in tokens])
    return " ".join(f"{surface}/{reading}" for surface, reading in normalized)


def yomi_tokens_to_editable_rendered(tokens: Iterable[Iterable[str]]) -> str:
    normalized = normalize_yomi_tokens([list(token) for token in tokens])
    return " ".join(
        f"{escape_editable_component(surface)}/{escape_editable_component(reading)}"
        for surface, reading in normalized
    )


def escape_editable_component(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("/", "\\/")
        .replace(" ", "\\s")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\u3000", "\\u3000")
    )


def editable_rendered_to_yomi_tokens(rendered: str, *, text: str | None = None) -> list[list[str]]:
    if "\\" not in rendered:
        return legacy_rendered_to_yomi_tokens(rendered, text=text)
    raw_tokens = split_ascii_rendered_tokens(rendered)
    tokens = normalize_yomi_tokens(
        [list(split_editable_rendered_token(token)) for token in raw_tokens]
    )
    validate_yomi_token_surfaces(tokens, text=text)
    return tokens


def split_editable_rendered_token(token: str) -> tuple[str, str]:
    surface: list[str] = []
    reading: list[str] = []
    target = surface
    escaped = False
    separated = False
    escape_values = {"s": " ", "t": "\t", "r": "\r", "n": "\n"}
    index = 0
    while index < len(token):
        char = token[index]
        if escaped:
            if char == "u" and index + 4 < len(token):
                codepoint = token[index + 1 : index + 5]
                if all(candidate in "0123456789abcdefABCDEF" for candidate in codepoint):
                    target.append(chr(int(codepoint, 16)))
                    index += 5
                    escaped = False
                    continue
            target.append(escape_values.get(char, char))
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "/" and not separated:
            target = reading
            separated = True
            index += 1
            continue
        target.append(char)
        index += 1
    if escaped:
        raise YomiTokenError(f"editable token {token!r} ends with an incomplete escape")
    if not separated:
        raise YomiTokenError(f"editable token {token!r} has no surface/reading separator")
    if not surface:
        raise YomiTokenError(f"editable token {token!r} has an empty surface")
    return "".join(surface), "".join(reading)
