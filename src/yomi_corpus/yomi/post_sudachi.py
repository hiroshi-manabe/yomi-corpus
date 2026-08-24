from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from yomi_corpus.yomi.strategies import (
    RenderedPair,
    infer_space_spanning_component_readings,
    is_symbolic_sudachi_kaomoji,
    render_middle_dot_spanning_sudachi_token,
    render_parenthesis_spanning_sudachi_token,
    split_surface_preserving_spaces,
    split_mixed_arabic_numeric_token,
    token_contains_internal_middle_dot,
    token_contains_parenthesis,
    token_contains_space,
)
from yomi_corpus.yomi.numeric_surfaces import is_numeric_digit_surface
from yomi_corpus.yomi.types import SudachiToken


NORMALIZER_VERSION = 1

UPPERCASE_LATIN_LETTER_READINGS = {
    "A": "エー",
    "B": "ビー",
    "C": "シー",
    "D": "ディー",
    "E": "イー",
    "F": "エフ",
    "G": "ジー",
    "H": "エイチ",
    "I": "アイ",
    "J": "ジェー",
    "K": "ケー",
    "L": "エル",
    "M": "エム",
    "N": "エヌ",
    "O": "オー",
    "P": "ピー",
    "Q": "キュー",
    "R": "アール",
    "S": "エス",
    "T": "ティー",
    "U": "ユー",
    "V": "ブイ",
    "W": "ダブリュー",
    "X": "エックス",
    "Y": "ワイ",
    "Z": "ゼット",
}


@dataclass(frozen=True)
class SudachiNormalizationApplication:
    rule_id: str
    raw_token_indexes: tuple[int, ...]
    source_start: int
    source_end: int
    before: tuple[tuple[str, str], ...]
    after: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class NormalizedSudachiResult:
    normalizer_version: int
    tokens: tuple[SudachiToken, ...]
    token_sources: tuple[tuple[int, ...], ...]
    applications: tuple[SudachiNormalizationApplication, ...]

    def metadata(self) -> dict[str, Any]:
        return {
            "normalizer_version": self.normalizer_version,
            "token_sources": [list(indexes) for indexes in self.token_sources],
            "applications": [asdict(application) for application in self.applications],
        }


def normalize_sudachi_tokens(
    tokens: Iterable[SudachiToken],
    *,
    text: str,
) -> NormalizedSudachiResult:
    raw_tokens = list(tokens)
    _validate_source_partition(raw_tokens, text=text, stage="raw Sudachi")

    output: list[SudachiToken] = []
    token_sources: list[tuple[int, ...]] = []
    applications: list[SudachiNormalizationApplication] = []
    source_cursor = 0

    for raw_index, raw_token in enumerate(raw_tokens):
        source_start = source_cursor
        source_cursor += len(raw_token.surface)
        normalized_token = normalize_sudachi_token_reading(raw_token)
        pairs, rule_id = _normalize_structural_token(normalized_token)
        normalized_tokens = [
            _token_from_pair(
                pair,
                template=normalized_token,
                split_numeric_component=rule_id == "split_mixed_arabic_numeric_token",
                normalization_locked=(
                    rule_id is not None or normalized_token != raw_token
                ),
            )
            for pair in pairs
        ]

        if normalized_tokens != [raw_token]:
            applications.append(
                _application(
                    rule_id=rule_id or "normalize_uppercase_latin_letter_reading",
                    raw_token_indexes=(raw_index,),
                    source_start=source_start,
                    source_end=source_cursor,
                    before=[raw_token],
                    after=normalized_tokens,
                )
            )
        output.extend(normalized_tokens)
        token_sources.extend((raw_index,) for _token in normalized_tokens)

    output, token_sources, lexical_applications = _normalize_lexical_boundaries(
        output,
        token_sources,
        raw_tokens=raw_tokens,
    )
    applications.extend(lexical_applications)
    _validate_source_partition(output, text=text, stage="normalized Sudachi")
    return NormalizedSudachiResult(
        normalizer_version=NORMALIZER_VERSION,
        tokens=tuple(output),
        token_sources=tuple(token_sources),
        applications=tuple(applications),
    )


def normalize_sudachi_token_reading(token: SudachiToken) -> SudachiToken:
    normalized_surface = token.surface
    if len(token.surface) == 1 and "Ａ" <= token.surface <= "Ｚ":
        normalized_surface = chr(ord(token.surface) - ord("Ａ") + ord("A"))
    replacement = UPPERCASE_LATIN_LETTER_READINGS.get(normalized_surface)
    if len(normalized_surface) != 1 or replacement is None:
        return token
    return SudachiToken(
        surface=token.surface,
        pos=token.pos,
        dictionary_form=token.dictionary_form,
        normalized_form=token.normalized_form,
        reading=replacement,
        normalization_locked=True,
    )


def serialize_sudachi_token(token: SudachiToken) -> dict[str, Any]:
    return {
        "surface": token.surface,
        "pos": token.pos,
        "dictionary_form": token.dictionary_form,
        "normalized_form": token.normalized_form,
        "reading": token.reading,
        "normalization_locked": token.normalization_locked,
    }


def normalized_sudachi_token_rows(yomi: dict[str, Any]) -> list[dict[str, Any]]:
    sudachi = yomi.get("sudachi")
    if not isinstance(sudachi, dict):
        return []
    normalized = sudachi.get("normalized")
    if isinstance(normalized, dict) and isinstance(normalized.get("tokens"), list):
        return [row for row in normalized["tokens"] if isinstance(row, dict)]
    tokens = sudachi.get("tokens")
    if not isinstance(tokens, list):
        return []
    return [row for row in tokens if isinstance(row, dict)]


def _normalize_structural_token(
    token: SudachiToken,
) -> tuple[list[RenderedPair], str | None]:
    if is_symbolic_sudachi_kaomoji(token):
        return [RenderedPair(surface=token.surface, reading="カオモジ")], (
            "normalize_symbolic_sudachi_kaomoji"
        )
    if token_contains_space(token):
        parts = split_surface_preserving_spaces(token.surface)
        component_surfaces = [part for part in parts if not part.isspace()]
        component_readings = iter(
            infer_space_spanning_component_readings(
                full_reading=token.reading,
                component_surfaces=component_surfaces,
            )
        )
        return [
            RenderedPair(surface=part, reading="" if part.isspace() else next(component_readings))
            for part in parts
        ], "split_space_spanning_sudachi_token"
    if token_contains_internal_middle_dot(token):
        return render_middle_dot_spanning_sudachi_token(token), (
            "split_middle_dot_spanning_sudachi_token"
        )
    if token_contains_parenthesis(token):
        return render_parenthesis_spanning_sudachi_token(token), (
            "split_parenthesis_spanning_sudachi_token"
        )
    numeric_parts = split_mixed_arabic_numeric_token(token)
    if numeric_parts is not None:
        return numeric_parts, "split_mixed_arabic_numeric_token"
    return [RenderedPair(surface=token.surface, reading=token.reading)], None


def _token_from_pair(
    pair: RenderedPair,
    *,
    template: SudachiToken,
    split_numeric_component: bool,
    normalization_locked: bool,
) -> SudachiToken:
    surface = pair.surface
    effective_lock = normalization_locked or template.normalization_locked
    if surface.isspace():
        return SudachiToken(
            surface=surface,
            pos="空白,*,*,*,*,*",
            dictionary_form=surface,
            normalized_form=surface,
            reading="",
            normalization_locked=effective_lock,
        )
    if _is_structural_separator(surface):
        return SudachiToken(
            surface=surface,
            pos="補助記号,一般,*,*,*,*",
            dictionary_form=surface,
            normalized_form=surface,
            reading=surface,
            normalization_locked=effective_lock,
        )
    if split_numeric_component and is_numeric_digit_surface(surface):
        return SudachiToken(
            surface=surface,
            pos="名詞,数詞,*,*,*,*",
            dictionary_form=surface,
            normalized_form=surface,
            reading="",
            normalization_locked=effective_lock,
        )
    component_pos = (
        "名詞,普通名詞,一般,*,*,*"
        if template.pos.startswith("補助記号,") and surface != template.surface
        else template.pos
    )
    return SudachiToken(
        surface=surface,
        pos=component_pos,
        dictionary_form=(
            template.dictionary_form if surface == template.surface else surface
        ),
        normalized_form=(
            template.normalized_form if surface == template.surface else surface
        ),
        reading=pair.reading,
        normalization_locked=effective_lock,
    )


def _is_structural_separator(surface: str) -> bool:
    return bool(surface) and all(char in "・･()（）" for char in surface)


def _normalize_lexical_boundaries(
    tokens: list[SudachiToken],
    token_sources: list[tuple[int, ...]],
    *,
    raw_tokens: list[SudachiToken],
) -> tuple[
    list[SudachiToken],
    list[tuple[int, ...]],
    list[SudachiNormalizationApplication],
]:
    output: list[SudachiToken] = []
    output_sources: list[tuple[int, ...]] = []
    applications: list[SudachiNormalizationApplication] = []
    raw_offsets = _raw_token_offsets(raw_tokens)
    index = 0
    while index < len(tokens):
        if (
            index + 1 < len(tokens)
            and tokens[index].surface == "皆"
            and tokens[index].reading == "ミナ"
            and tokens[index + 1].surface == "様"
            and tokens[index + 1].reading == "サマ"
        ):
            sources = tuple(
                dict.fromkeys(token_sources[index] + token_sources[index + 1])
            )
            merged = SudachiToken(
                surface="皆様",
                pos=tokens[index].pos,
                dictionary_form="皆様",
                normalized_form="皆様",
                reading="ミナサマ",
                normalization_locked=True,
            )
            output.append(merged)
            output_sources.append(sources)
            applications.append(
                _application(
                    rule_id="canonicalize_minasama_boundary",
                    raw_token_indexes=sources,
                    source_start=raw_offsets[sources[0]][0],
                    source_end=raw_offsets[sources[-1]][1],
                    before=tokens[index : index + 2],
                    after=[merged],
                )
            )
            index += 2
            continue
        output.append(tokens[index])
        output_sources.append(token_sources[index])
        index += 1
    return output, output_sources, applications


def _application(
    *,
    rule_id: str,
    raw_token_indexes: tuple[int, ...],
    source_start: int,
    source_end: int,
    before: Iterable[SudachiToken],
    after: Iterable[SudachiToken],
) -> SudachiNormalizationApplication:
    return SudachiNormalizationApplication(
        rule_id=rule_id,
        raw_token_indexes=raw_token_indexes,
        source_start=source_start,
        source_end=source_end,
        before=tuple((token.surface, token.reading) for token in before),
        after=tuple((token.surface, token.reading) for token in after),
    )


def _raw_token_offsets(tokens: list[SudachiToken]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        offsets.append((cursor, cursor + len(token.surface)))
        cursor += len(token.surface)
    return offsets


def _validate_source_partition(
    tokens: Iterable[SudachiToken],
    *,
    text: str,
    stage: str,
) -> None:
    reconstructed = "".join(token.surface for token in tokens)
    if reconstructed != text:
        raise ValueError(
            f"{stage} tokens do not reproduce source text: "
            f"expected={text!r}, reconstructed={reconstructed!r}"
        )
