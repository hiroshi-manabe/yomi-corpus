from __future__ import annotations

from dataclasses import dataclass
import re

from yomi_corpus.splitter import split_text_into_units


PARENTHETICAL_READING_RE = re.compile(
    r"(?P<surface>[々〆ヶ\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{1,16})"
    r"[（(](?P<reading>[ぁ-ゖァ-ヺー・]{1,32})[）)]"
)
DEFAULT_MAX_GLOSSES_PER_SENTENCE = 3


@dataclass(frozen=True)
class ParentheticalReadingMatch:
    surface: str
    reading: str
    start: int
    end: int


@dataclass(frozen=True)
class AnnotationHeavySentence:
    start: int
    end: int
    text: str
    matches: tuple[ParentheticalReadingMatch, ...]


def parenthetical_readings(text: str, *, offset: int = 0) -> tuple[ParentheticalReadingMatch, ...]:
    return tuple(
        ParentheticalReadingMatch(
            surface=match.group("surface"),
            reading=match.group("reading"),
            start=offset + match.start(),
            end=offset + match.end(),
        )
        for match in PARENTHETICAL_READING_RE.finditer(text)
    )


def annotation_heavy_sentences(
    text: str, *, max_glosses_per_sentence: int = DEFAULT_MAX_GLOSSES_PER_SENTENCE
) -> tuple[AnnotationHeavySentence, ...]:
    rows = []
    for span in split_text_into_units(text):
        matches = parenthetical_readings(span.text, offset=span.start)
        if len(matches) <= max_glosses_per_sentence:
            continue
        rows.append(
            AnnotationHeavySentence(
                start=span.start,
                end=span.end,
                text=span.text,
                matches=matches,
            )
        )
    return tuple(rows)
