from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from yomi_corpus.yomi.token_codec import YomiTokenError, legacy_rendered_to_yomi_tokens
from yomi_corpus.yomi.numeric_surfaces import is_formatted_arabic_number_surface


_ASCII_DIGITS = "0123456789"
_FULLWIDTH_DIGITS = "０１２３４５６７８９"
_TO_ASCII_DIGITS = str.maketrans(_FULLWIDTH_DIGITS, _ASCII_DIGITS)
_NUMERIC_RE = re.compile(r"[0-9０-９]+")


@dataclass(frozen=True)
class NumericCompoundRule:
    reading: str
    review_readings: tuple[str, ...] = ()


@dataclass(frozen=True)
class NumericCompoundOccurrence:
    surface: str
    reading: str
    start: int
    end: int
    pair_index: int
    rule: NumericCompoundRule


@dataclass(frozen=True)
class NumericCompoundNormalization:
    rendered: str
    applied_surfaces: tuple[str, ...]
    formatted_numeric_surfaces: tuple[str, ...] = ()


# These lexicalized Japanese forms are more useful as single reading units than
# as a digit plus the generic reading of a counter/date suffix.
NUMERIC_COMPOUND_RULES: dict[str, NumericCompoundRule] = {
    "1日": NumericCompoundRule("イチニチ", ("イチニチ", "ツイタチ")),
    "2日": NumericCompoundRule("フツカ"),
    "3日": NumericCompoundRule("ミッカ"),
    "4日": NumericCompoundRule("ヨッカ"),
    "5日": NumericCompoundRule("イツカ"),
    "6日": NumericCompoundRule("ムイカ"),
    "7日": NumericCompoundRule("ナノカ"),
    "8日": NumericCompoundRule("ヨウカ"),
    "9日": NumericCompoundRule("ココノカ"),
    "10日": NumericCompoundRule("トオカ"),
    "14日": NumericCompoundRule("ジュウヨッカ"),
    "20日": NumericCompoundRule("ハツカ"),
    "24日": NumericCompoundRule("ニジュウヨッカ"),
    "1人": NumericCompoundRule("ヒトリ"),
    "2人": NumericCompoundRule("フタリ"),
    "1つ": NumericCompoundRule("ヒトツ"),
    "2つ": NumericCompoundRule("フタツ"),
    "3つ": NumericCompoundRule("ミッツ"),
    "4つ": NumericCompoundRule("ヨッツ"),
    "5つ": NumericCompoundRule("イツツ"),
    "6つ": NumericCompoundRule("ムッツ"),
    "7つ": NumericCompoundRule("ナナツ"),
    "8つ": NumericCompoundRule("ヤッツ"),
    "9つ": NumericCompoundRule("ココノツ"),
}


def numeric_compound_rule(surface: str) -> NumericCompoundRule | None:
    return NUMERIC_COMPOUND_RULES.get(surface.translate(_TO_ASCII_DIGITS))


def normalize_numeric_compounds(rendered: str) -> NumericCompoundNormalization:
    pairs, formatted_numeric_surfaces = _merge_formatted_numeric_expressions(
        _parse_rendered_pairs(rendered)
    )
    normalized: list[tuple[str, str]] = []
    applied: list[str] = []
    index = 0
    while index < len(pairs):
        surface, reading = pairs[index]
        duration_rule = (
            numeric_compound_rule(surface[:-1])
            if surface.endswith("日間")
            else None
        )
        if duration_rule is not None:
            date_surface = surface[:-1]
            normalized.append((date_surface, duration_rule.reading))
            normalized.append(("間", "カン"))
            applied.append(surface)
            index += 1
            continue
        rule = numeric_compound_rule(surface)
        consumed = 1
        source_reading = reading
        if rule is None and _NUMERIC_RE.fullmatch(surface) and index + 1 < len(pairs):
            next_surface, next_reading = pairs[index + 1]
            if next_surface == "日間":
                duration_rule = numeric_compound_rule(surface + "日")
                if duration_rule is not None:
                    normalized.append((surface + "日", duration_rule.reading))
                    normalized.append(("間", "カン"))
                    applied.append(surface + next_surface)
                    index += 2
                    continue
            combined = surface + next_surface
            combined_rule = numeric_compound_rule(combined)
            if combined_rule is not None:
                surface = combined
                rule = combined_rule
                source_reading = next_reading
                consumed = 2
        if rule is not None:
            normalized_reading = (
                source_reading
                if source_reading in rule.review_readings
                else rule.reading
            )
            normalized.append((surface, normalized_reading))
            if consumed != 1 or reading != normalized_reading:
                applied.append(surface)
        else:
            normalized.append((surface, reading))
        index += consumed
    return NumericCompoundNormalization(
        rendered=_render_pairs(normalized),
        applied_surfaces=tuple(applied),
        formatted_numeric_surfaces=tuple(formatted_numeric_surfaces),
    )


def _merge_formatted_numeric_expressions(
    pairs: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[str]]:
    merged: list[tuple[str, str]] = []
    applied: list[str] = []
    index = 0
    numeric_chars = set("0123456789０１２３４５６７８９,，.．+-＋－−")
    while index < len(pairs):
        best_end = index
        candidate = ""
        for end in range(index, len(pairs)):
            surface = pairs[end][0]
            if not surface or any(char not in numeric_chars for char in surface):
                break
            candidate += surface
            has_formatting = any(char in candidate for char in ",，.．+-＋－−")
            if has_formatting and is_formatted_arabic_number_surface(candidate):
                best_end = end + 1
        if best_end > index:
            surface = "".join(value for value, _reading in pairs[index:best_end])
            merged.append((surface, ""))
            if best_end - index > 1 or pairs[index] != (surface, ""):
                applied.append(surface)
            index = best_end
            continue
        merged.append(pairs[index])
        index += 1
    return merged, applied


def numeric_compound_occurrences(text: str, rendered: str) -> list[NumericCompoundOccurrence]:
    occurrences: list[NumericCompoundOccurrence] = []
    try:
        pairs = legacy_rendered_to_yomi_tokens(rendered, text=text)
    except YomiTokenError:
        return []
    cursor = 0
    for pair_index, (surface, reading) in enumerate(pairs):
        end = cursor + len(surface)
        if _equivalent_text(surface, text[cursor:end]):
            source_surface = text[cursor:end]
        else:
            start = _find_equivalent_surface(text, surface, cursor)
            if start < 0:
                return []
            cursor = start
            end = cursor + len(surface)
            source_surface = text[cursor:end]
        rule = numeric_compound_rule(source_surface)
        if rule is not None:
            occurrences.append(
                NumericCompoundOccurrence(
                    surface=source_surface,
                    reading=reading or rule.reading,
                    start=cursor,
                    end=end,
                    pair_index=pair_index,
                    rule=rule,
                )
            )
        cursor = end
    return occurrences


def canonicalize_final_numeric_compounds(tokens: Iterable[Iterable[str]]) -> list[list[str]]:
    canonical: list[list[str]] = []
    for raw_surface, raw_reading in tokens:
        surface = str(raw_surface)
        reading = str(raw_reading)
        rule = numeric_compound_rule(surface)
        if rule is not None and rule.review_readings and reading == "イチニチ":
            canonical.append([surface[:-1], ""])
            canonical.append([surface[-1], "ニチ"])
        else:
            canonical.append([surface, reading])
    return canonical


def _parse_rendered_pairs(rendered: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for token in rendered.split(" "):
        if not token:
            continue
        if "/" not in token:
            pairs.append((token, ""))
            continue
        surface, reading = token.rsplit("/", 1)
        pairs.append((surface, reading))
    return pairs


def _render_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    return " ".join(f"{surface}/{reading}" for surface, reading in pairs)


def _equivalent_text(left: str, right: str) -> bool:
    return left.replace("\u00a0", " ") == right.replace("\u00a0", " ")


def _find_equivalent_surface(text: str, surface: str, start: int) -> int:
    normalized_text = text.replace("\u00a0", " ")
    normalized_surface = surface.replace("\u00a0", " ")
    return normalized_text.find(normalized_surface, start)
