from __future__ import annotations

from typing import Any

from yomi_corpus.splitter import BOUNDARY_CHARS


def character_boundary_positions(text: str) -> set[int]:
    """Return internal boundaries produced by the current character splitter."""
    return {
        index + 1
        for index, char in enumerate(text)
        if char in BOUNDARY_CHARS and index + 1 < len(text)
    }


def sudachi_boundary_positions(text: str, tokenizer: Any, split_mode: Any) -> set[int]:
    """Return internal boundaries after Sudachi sentence-punctuation tokens."""
    boundaries = {
        morpheme.end()
        for morpheme in tokenizer.tokenize(text, split_mode)
        if is_sudachi_sentence_punctuation(morpheme.part_of_speech())
        and morpheme.end() < len(text)
    }
    boundaries.update(
        index + 1
        for index, char in enumerate(text)
        if char == "\n" and index + 1 < len(text)
    )
    return boundaries


def is_sudachi_sentence_punctuation(part_of_speech: tuple[str, ...]) -> bool:
    return (
        len(part_of_speech) >= 2
        and part_of_speech[0] == "補助記号"
        and part_of_speech[1] == "句点"
    )


def compare_document_boundaries(
    text: str,
    *,
    tokenizer: Any,
    split_mode: Any,
    context_chars: int = 35,
) -> dict[str, Any]:
    current = character_boundary_positions(text)
    sudachi = sudachi_boundary_positions(text, tokenizer, split_mode)
    current_only = sorted(current - sudachi)
    sudachi_only = sorted(sudachi - current)
    return {
        "text_length": len(text),
        "current_boundary_count": len(current),
        "sudachi_boundary_count": len(sudachi),
        "current_only": [
            boundary_context(text, position, context_chars=context_chars)
            for position in current_only
        ],
        "sudachi_only": [
            boundary_context(text, position, context_chars=context_chars)
            for position in sudachi_only
        ],
    }


def boundary_context(text: str, position: int, *, context_chars: int) -> dict[str, Any]:
    start = max(0, position - context_chars)
    end = min(len(text), position + context_chars)
    return {
        "position": position,
        "preceding_character": text[position - 1] if position else "",
        "context": (
            text[start:position].replace("\n", "\\n")
            + "|"
            + text[position:end].replace("\n", "\\n")
        ),
    }
