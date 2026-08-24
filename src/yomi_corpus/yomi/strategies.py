from __future__ import annotations

from dataclasses import dataclass
import re
from functools import lru_cache

from yomi_corpus.yomi.types import (
    DecoderCandidate,
    DecoderEntry,
    SudachiToken,
    YomiStrategyResult,
)
from yomi_corpus.yomi.numeric_surfaces import (
    is_numeric_digit_surface,
    is_numeric_only_surface,
)
from yomi_corpus.yomi.numeric_compounds import numeric_compound_rule
from yomi_corpus.yomi.repairs import PARENTHESIZED_SEMANTIC_TOKENS


@dataclass(frozen=True)
class SpannedSudachiToken:
    token: SudachiToken
    start: int
    end: int


@dataclass(frozen=True)
class SpannedDecoderEntry:
    entry: DecoderEntry
    start: int
    end: int


@dataclass(frozen=True)
class RenderedPair:
    surface: str
    reading: str


ASCII_SPACE = " "
NBSP = "\u00a0"
SPACE_RUN_RE = re.compile(r"(\s+)")
INTERNAL_MIDDLE_DOT_RUN_RE = re.compile(r"([・･]+)")
INTERNAL_PARENTHESIS_RUN_RE = re.compile(r"([()（）]+)")
ATTACHED_WAVE_MARKS = {"〜", "～"}
HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff々〆〻]")
SEMANTIC_PARENTHESIZED_JAPANESE_RE = re.compile(
    r"(?:\([\u3041-\u3096\u30a1-\u30fa\u3400-\u9fff\uf900-\ufaff々〆〻]+\)"
    r"|（[\u3041-\u3096\u30a1-\u30fa\u3400-\u9fff\uf900-\ufaff々〆〻]+）)"
)
ARABIC_DIGIT_RUN_RE = re.compile(r"[0-9０-９]+")
MIXED_ARABIC_NUMERIC_PART_RE = re.compile(r"[0-9０-９]+|[^0-9０-９]+")


def normalize_ascii_spaces_for_yomi(text: str) -> str:
    return text.replace(ASCII_SPACE, NBSP)


def available_strategy_names() -> list[str]:
    return sorted(STRATEGIES)


def apply_strategy(
    strategy_name: str,
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    try:
        strategy = STRATEGIES[strategy_name]
    except KeyError as exc:
        raise ValueError(f"Unknown yomi strategy: {strategy_name}") from exc
    return strategy(text=text, sudachi_tokens=sudachi_tokens, decoder_candidates=decoder_candidates)


def strategy_sudachi_only_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    rendered = render_pairs_from_sudachi(sudachi_tokens)
    signals = ["strategy:sudachi_only_v1"]
    if all(token.reading for token in sudachi_tokens):
        signals.append("all_sudachi_tokens_have_readings")
    return YomiStrategyResult(
        strategy="sudachi_only_v1",
        rendered=rendered,
        certain=False,
        signals=signals,
    )


def strategy_decoder_only_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    if not decoder_candidates:
        return YomiStrategyResult(
            strategy="decoder_only_v1",
            rendered=render_pairs_from_sudachi(sudachi_tokens),
            certain=False,
            signals=["strategy:decoder_only_v1", "decoder_no_candidates", "fallback_sudachi"],
        )
    return YomiStrategyResult(
        strategy="decoder_only_v1",
        rendered=render_pairs_from_decoder(decoder_candidates[0]),
        certain=False,
        signals=["strategy:decoder_only_v1", "decoder_top_candidate"],
    )


def strategy_agreement_prefer_decoder_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    sudachi_pairs = render_pairs_from_sudachi(sudachi_tokens)
    if not decoder_candidates:
        return YomiStrategyResult(
            strategy="agreement_prefer_decoder_v1",
            rendered=sudachi_pairs,
            certain=False,
            signals=["strategy:agreement_prefer_decoder_v1", "decoder_no_candidates", "fallback_sudachi"],
        )

    top_candidate = decoder_candidates[0]
    decoder_pairs = render_pairs_from_decoder(top_candidate)
    sudachi_surfaces = [token.surface for token in sudachi_tokens]
    decoder_surfaces = [entry.surface for entry in top_candidate.entries]

    signals = ["strategy:agreement_prefer_decoder_v1", "decoder_top_candidate"]
    if sudachi_pairs == decoder_pairs:
        signals.append("sudachi_decoder_exact_agreement")
        return YomiStrategyResult(
            strategy="agreement_prefer_decoder_v1",
            rendered=decoder_pairs,
            certain=True,
            signals=signals,
        )
    if sudachi_surfaces == decoder_surfaces:
        signals.append("sudachi_decoder_surface_agreement")
        signals.append("prefer_decoder_readings")
        return YomiStrategyResult(
            strategy="agreement_prefer_decoder_v1",
            rendered=decoder_pairs,
            certain=False,
            signals=signals,
        )
    signals.append("sudachi_decoder_surface_disagreement")
    signals.append("fallback_sudachi")
    return YomiStrategyResult(
        strategy="agreement_prefer_decoder_v1",
        rendered=sudachi_pairs,
        certain=False,
        signals=signals,
    )


def strategy_agreement_prefer_sudachi_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    sudachi_pairs = render_pairs_from_sudachi(sudachi_tokens)
    signals = ["strategy:agreement_prefer_sudachi_v1"]
    if not decoder_candidates:
        signals.extend(["decoder_no_candidates", "fallback_sudachi"])
        return YomiStrategyResult(
            strategy="agreement_prefer_sudachi_v1",
            rendered=sudachi_pairs,
            certain=False,
            signals=signals,
        )

    top_candidate = decoder_candidates[0]
    decoder_pairs = render_pairs_from_decoder(top_candidate)
    sudachi_surfaces = [token.surface for token in sudachi_tokens]
    decoder_surfaces = [entry.surface for entry in top_candidate.entries]

    if sudachi_pairs == decoder_pairs:
        signals.append("sudachi_decoder_exact_agreement")
        return YomiStrategyResult(
            strategy="agreement_prefer_sudachi_v1",
            rendered=sudachi_pairs,
            certain=True,
            signals=signals,
        )
    if sudachi_surfaces == decoder_surfaces:
        signals.extend(["sudachi_decoder_surface_agreement", "prefer_sudachi_readings"])
        return YomiStrategyResult(
            strategy="agreement_prefer_sudachi_v1",
            rendered=sudachi_pairs,
            certain=False,
            signals=signals,
        )
    signals.extend(["sudachi_decoder_surface_disagreement", "fallback_sudachi"])
    return YomiStrategyResult(
        strategy="agreement_prefer_sudachi_v1",
        rendered=sudachi_pairs,
        certain=False,
        signals=signals,
    )


def strategy_aligned_hybrid_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    return strategy_aligned_hybrid(
        text=text,
        sudachi_tokens=sudachi_tokens,
        decoder_candidates=decoder_candidates,
        strategy_name="aligned_hybrid_v1",
        prefer_supported_decoder_grouping=False,
        prefer_supported_decoder_partition=False,
    )


def strategy_ngram_grouping_preferred_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    return strategy_aligned_hybrid(
        text=text,
        sudachi_tokens=sudachi_tokens,
        decoder_candidates=decoder_candidates,
        strategy_name="ngram_grouping_preferred_v1",
        prefer_supported_decoder_grouping=True,
        prefer_supported_decoder_partition=False,
    )


def strategy_ngram_boundary_preferred_v1(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> YomiStrategyResult:
    return strategy_aligned_hybrid(
        text=text,
        sudachi_tokens=sudachi_tokens,
        decoder_candidates=decoder_candidates,
        strategy_name="ngram_boundary_preferred_v1",
        prefer_supported_decoder_grouping=True,
        prefer_supported_decoder_partition=True,
    )


def strategy_aligned_hybrid(
    *,
    text: str,
    sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
    strategy_name: str,
    prefer_supported_decoder_grouping: bool,
    prefer_supported_decoder_partition: bool,
) -> YomiStrategyResult:
    signals = [f"strategy:{strategy_name}"]
    if not decoder_candidates:
        signals.extend(["decoder_no_candidates", "fallback_sudachi"])
        return YomiStrategyResult(
            strategy=strategy_name,
            rendered=render_pairs_from_sudachi(sudachi_tokens),
            certain=False,
            signals=signals,
        )

    sudachi_spans = span_sudachi_tokens(text, sudachi_tokens)
    decoder_spans_by_rank = [span_decoder_entries(text, candidate) for candidate in decoder_candidates]
    top_decoder_spans = decoder_spans_by_rank[0]
    exact_decoder_by_span = {
        (entry.start, entry.end): entry.entry
        for entry in top_decoder_spans
    }
    decoder_by_start = {entry.start: entry for entry in top_decoder_spans}

    rendered_pairs: list[RenderedPair] = []
    index = 0
    while index < len(sudachi_spans):
        current = sudachi_spans[index]
        token = current.token

        if is_whitespace_token(token):
            rendered_pairs.append(render_sudachi_token(token))
            signals.append("preserve_whitespace_token")
            index += 1
            continue

        if is_symbolic_sudachi_kaomoji(token):
            rendered_pairs.append(RenderedPair(surface=token.surface, reading="カオモジ"))
            signals.append("normalize_symbolic_sudachi_kaomoji")
            index += 1
            continue

        if token_contains_space(token):
            rendered_pairs.extend(render_space_spanning_sudachi_token(token))
            signals.append("split_space_spanning_sudachi_token")
            index += 1
            continue

        if token_contains_internal_middle_dot(token):
            rendered_pairs.extend(render_middle_dot_spanning_sudachi_token(token))
            signals.append("split_middle_dot_spanning_sudachi_token")
            index += 1
            continue

        if token_contains_parenthesis(token):
            rendered_pairs.extend(render_parenthesis_spanning_sudachi_token(token))
            signals.append("split_parenthesis_spanning_sudachi_token")
            index += 1
            continue

        numeric_parts = split_mixed_arabic_numeric_token(token)
        if numeric_parts is not None:
            rendered_pairs.extend(numeric_parts)
            signals.append("split_mixed_arabic_numeric_token")
            index += 1
            continue

        if is_numeric_token(token):
            numeric_surface, next_index = collect_numeric_sudachi_run(
                sudachi_spans=sudachi_spans,
                start_index=index,
            )
            if is_numeric_only_surface(numeric_surface):
                rendered_pairs.append(RenderedPair(surface=numeric_surface, reading=""))
                signals.append("group_numeric_run")
                index = next_index
                continue

        if prefer_supported_decoder_grouping:
            grouped_entry = decoder_by_start.get(current.start)
            grouped_end_index = supported_decoder_grouped_sudachi_end_index(
                sudachi_spans=sudachi_spans,
                start_index=index,
                decoder_entry=grouped_entry,
            )
            if grouped_entry is not None and grouped_end_index is not None:
                rendered_pairs.append(
                    RenderedPair(
                        surface=grouped_entry.entry.surface,
                        reading=grouped_entry.entry.reading,
                    )
                )
                signals.append("prefer_supported_decoder_grouping")
                index = grouped_end_index
                continue

        exact_entry = exact_decoder_by_span.get((current.start, current.end))
        if exact_entry is not None:
            pair, pair_signals = render_exact_aligned_token(
                token=token,
                exact_entry=exact_entry,
            )
            rendered_pairs.append(pair)
            signals.extend(pair_signals)
            index += 1
            continue

        refined_entries = collect_decoder_entries_for_exact_span(
            decoder_spans=top_decoder_spans,
            target_start=current.start,
            target_end=current.end,
        )
        if can_refine_single_sudachi_token(token, refined_entries):
            rendered_pairs.extend(render_decoder_entries(refined_entries))
            signals.append("refine_single_sudachi_compound_with_decoder")
            index += 1
            continue
        if (
            prefer_supported_decoder_partition
            and can_prefer_supported_decoder_partition(token, refined_entries)
        ):
            rendered_pairs.extend(render_decoder_entries(refined_entries))
            signals.append("prefer_supported_decoder_partition")
            index += 1
            continue

        rendered_pairs.append(render_sudachi_token(token))
        signals.append("fallback_sudachi_token")
        index += 1

    certain = all(
        signal
        not in {
            "fallback_sudachi_token",
            "decoder_no_candidates",
        }
        for signal in signals
    )
    return YomiStrategyResult(
        strategy=strategy_name,
        rendered=" ".join(f"{pair.surface}/{pair.reading}" for pair in rendered_pairs),
        certain=certain,
        signals=dedupe_preserve_order(signals),
    )


def supported_decoder_grouped_sudachi_end_index(
    *,
    sudachi_spans: list[SpannedSudachiToken],
    start_index: int,
    decoder_entry: SpannedDecoderEntry | None,
) -> int | None:
    if decoder_entry is None or not decoder_entry.entry.reading:
        return None
    if not decoder_entry_has_full_piece_support(decoder_entry.entry):
        return None
    current = sudachi_spans[start_index]
    if decoder_entry.start != current.start or decoder_entry.end <= current.end:
        return None
    index = start_index
    cursor = current.start
    while index < len(sudachi_spans) and cursor < decoder_entry.end:
        span = sudachi_spans[index]
        if span.start != cursor or span.end > decoder_entry.end:
            return None
        if is_boundary_special_token(span.token):
            return None
        cursor = span.end
        index += 1
    if cursor != decoder_entry.end or index - start_index < 2:
        return None
    return index


def can_prefer_supported_decoder_partition(
    token: SudachiToken,
    decoder_entries: list[SpannedDecoderEntry],
) -> bool:
    if len(decoder_entries) <= 1 or is_boundary_special_token(token):
        return False
    if any(not entry.entry.reading for entry in decoder_entries):
        return False
    if any(is_decoder_entry_symbol(entry.entry) for entry in decoder_entries):
        return False
    return all(decoder_entry_has_full_piece_support(entry.entry) for entry in decoder_entries)


def is_boundary_special_token(token: SudachiToken) -> bool:
    return (
        token.normalization_locked
        or is_whitespace_token(token)
        or token_contains_space(token)
        or (is_numeric_token(token) and is_numeric_only_surface(token.surface))
        or (is_punctuation_token(token) and HAN_RE.search(token.surface) is None)
    )


def span_sudachi_tokens(text: str, tokens: list[SudachiToken]) -> list[SpannedSudachiToken]:
    spans: list[SpannedSudachiToken] = []
    cursor = 0
    for token in tokens:
        start = find_surface_start(text, token.surface, cursor)
        if start < 0:
            raise ValueError(f"Could not align Sudachi token surface {token.surface!r} in text {text!r}")
        end = start + len(token.surface)
        spans.append(SpannedSudachiToken(token=token, start=start, end=end))
        cursor = end
    return spans


def span_decoder_entries(text: str, candidate: DecoderCandidate) -> list[SpannedDecoderEntry]:
    spans: list[SpannedDecoderEntry] = []
    cursor = 0
    for entry in candidate.entries:
        start = find_surface_start(text, entry.surface, cursor)
        if start < 0:
            raise ValueError(f"Could not align decoder entry surface {entry.surface!r} in text {text!r}")
        end = start + len(entry.surface)
        spans.append(SpannedDecoderEntry(entry=entry, start=start, end=end))
        cursor = end
    return spans


def find_surface_start(text: str, surface: str, cursor: int = 0) -> int:
    start = text.find(surface, cursor)
    if start >= 0:
        return start
    if not surface:
        return -1
    max_start = len(text) - len(surface)
    for index in range(cursor, max_start + 1):
        if surface_matches_at(text, surface, index):
            return index
    return -1


def surface_matches_at(text: str, surface: str, start: int) -> bool:
    if start < 0 or start + len(surface) > len(text):
        return False
    return all(chars_align(text[start + offset], char) for offset, char in enumerate(surface))


def chars_align(left: str, right: str) -> bool:
    if left == right:
        return True
    return left in {ASCII_SPACE, NBSP} and right in {ASCII_SPACE, NBSP}


def collect_decoder_entries_for_exact_span(
    *,
    decoder_spans: list[SpannedDecoderEntry],
    target_start: int,
    target_end: int,
) -> list[SpannedDecoderEntry]:
    collected: list[SpannedDecoderEntry] = []
    for entry in decoder_spans:
        if entry.end <= target_start:
            continue
        if entry.start >= target_end:
            break
        collected.append(entry)
    if not collected:
        return []
    if collected[0].start != target_start:
        return []
    if collected[-1].end != target_end:
        return []
    cursor = target_start
    for entry in collected:
        if entry.start != cursor:
            return []
        cursor = entry.end
    if cursor != target_end:
        return []
    return collected


def can_refine_single_sudachi_token(
    token: SudachiToken,
    decoder_entries: list[SpannedDecoderEntry],
) -> bool:
    if len(decoder_entries) <= 1 or token.normalization_locked:
        return False
    if not token.pos.startswith(("名詞,", "接頭辞,")):
        return False
    if "数詞" in token.pos:
        return False
    if any(not entry.entry.reading for entry in decoder_entries):
        return False
    if any(is_decoder_entry_symbol(entry.entry) for entry in decoder_entries):
        return False
    if not token.reading:
        return False
    decoder_reading = "".join(entry.entry.reading for entry in decoder_entries)
    if decoder_reading != token.reading:
        return False
    if any(not decoder_entry_has_ngram_support(entry.entry) for entry in decoder_entries):
        return False
    if any(not decoder_entry_has_previous_entry_support(entry.entry) for entry in decoder_entries[1:]):
        return False
    return True


def render_decoder_entries(entries: list[SpannedDecoderEntry]) -> list[RenderedPair]:
    rendered: list[RenderedPair] = []
    for entry in entries:
        surface = (
            canonical_whitespace_surface(entry.entry.surface)
            if entry.entry.surface.isspace()
            else entry.entry.surface
        )
        rendered.append(
            RenderedPair(
                surface=surface,
                reading=entry.entry.reading or surface,
            )
        )
    return rendered


def render_exact_aligned_token(
    *,
    token: SudachiToken,
    exact_entry: DecoderEntry,
) -> tuple[RenderedPair, list[str]]:
    signals: list[str] = []
    if token.normalization_locked:
        signals.append("preserve_post_sudachi_normalization")
        return render_sudachi_token(token), signals
    if is_punctuation_token(token):
        signals.append("normalize_punctuation_surface")
        return RenderedPair(surface=token.surface, reading=token.surface), signals

    if should_use_decoder_override(
        token=token,
        exact_entry=exact_entry,
    ):
        signals.append("use_decoder_contextual_override")
        return RenderedPair(surface=token.surface, reading=exact_entry.reading), signals

    if exact_entry.reading == token.reading:
        signals.append("sudachi_decoder_exact_token_agreement")
    return render_sudachi_token(token), signals


def should_use_decoder_override(
    *,
    token: SudachiToken,
    exact_entry: DecoderEntry,
) -> bool:
    if not exact_entry.reading:
        return False
    if not decoder_entry_has_ngram_support(exact_entry):
        return False
    if token.reading == exact_entry.reading:
        return False
    return True


def decoder_entry_has_ngram_support(entry: DecoderEntry) -> bool:
    return entry.final_order >= 2


def decoder_entry_has_full_piece_support(entry: DecoderEntry) -> bool:
    return bool(entry.piece_orders) and all(order >= 2 for order in entry.piece_orders)


def decoder_entry_has_previous_entry_support(entry: DecoderEntry) -> bool:
    if not entry.piece_orders:
        return False
    return entry.piece_orders[0] >= 2


def render_sudachi_token(token: SudachiToken) -> RenderedPair:
    if is_whitespace_token(token):
        surface = canonical_whitespace_surface(token.surface)
        return RenderedPair(surface=surface, reading=surface)
    if is_symbolic_sudachi_kaomoji(token):
        return RenderedPair(surface=token.surface, reading="カオモジ")
    if is_punctuation_token(token):
        return RenderedPair(surface=token.surface, reading=token.surface)
    if is_numeric_token(token) and is_numeric_only_surface(token.surface):
        return RenderedPair(surface=token.surface, reading="")
    return RenderedPair(
        surface=token.surface,
        reading=normalized_attached_wave_reading(token),
    )


def normalized_attached_wave_reading(token: SudachiToken) -> str:
    reading = token.reading or token.surface
    if is_proper_name_token(token) or not any(mark in token.surface for mark in ATTACHED_WAVE_MARKS):
        return reading
    non_wave_surface = "".join(
        char for char in token.surface if char not in ATTACHED_WAVE_MARKS
    )
    if not non_wave_surface:
        return reading
    if all(is_hiragana_or_katakana(char) or char == "ー" for char in non_wave_surface):
        return "".join(
            "ー" if char in ATTACHED_WAVE_MARKS else hiragana_to_katakana(char)
            for char in token.surface
        )
    trailing_count = len(token.surface) - len(token.surface.rstrip("〜～"))
    if trailing_count and is_katakana_reading(reading):
        existing_count = len(reading) - len(reading.rstrip("ー"))
        return reading + ("ー" * max(0, trailing_count - existing_count))
    return reading


def is_hiragana_or_katakana(char: str) -> bool:
    return "\u3041" <= char <= "\u3096" or is_katakana(char) or char in {"ゝ", "ゞ"}


def hiragana_to_katakana(char: str) -> str:
    if "\u3041" <= char <= "\u3096":
        return chr(ord(char) + 0x60)
    return {"ゝ": "ヽ", "ゞ": "ヾ"}.get(char, char)


def token_contains_space(token: SudachiToken) -> bool:
    return any(char.isspace() for char in token.surface) and not is_whitespace_token(token)


def token_contains_internal_middle_dot(token: SudachiToken) -> bool:
    parts = split_surface_preserving_middle_dots(token.surface)
    return (
        len(parts) >= 3
        and not is_middle_dot_run(parts[0])
        and not is_middle_dot_run(parts[-1])
    )


def token_contains_parenthesis(token: SudachiToken) -> bool:
    parts = split_surface_preserving_parentheses(token.surface)
    return len(parts) >= 2 and any(is_parenthesis_run(part) for part in parts)


def render_space_spanning_sudachi_token(token: SudachiToken) -> list[RenderedPair]:
    parts = split_surface_preserving_spaces(token.surface)
    component_surfaces = [part for part in parts if not part.isspace()]
    readings = infer_space_spanning_component_readings(
        full_reading=token.reading,
        component_surfaces=component_surfaces,
    )

    rendered: list[RenderedPair] = []
    component_index = 0
    for part in parts:
        if part.isspace():
            whitespace = canonical_whitespace_surface(part)
            rendered.append(RenderedPair(surface=whitespace, reading=whitespace))
            continue
        rendered.append(
            RenderedPair(
                surface=part,
                reading=readings[component_index],
            )
        )
        component_index += 1
    return rendered


def split_surface_preserving_spaces(surface: str) -> list[str]:
    return [part for part in SPACE_RUN_RE.split(surface) if part]


def render_middle_dot_spanning_sudachi_token(token: SudachiToken) -> list[RenderedPair]:
    rendered: list[RenderedPair] = []
    for part in split_surface_preserving_middle_dots(token.surface):
        if is_middle_dot_run(part):
            rendered.append(RenderedPair(surface=part, reading=part))
        else:
            rendered.append(RenderedPair(surface=part, reading=lookup_component_reading(part)))
    return rendered


def split_surface_preserving_middle_dots(surface: str) -> list[str]:
    return [part for part in INTERNAL_MIDDLE_DOT_RUN_RE.split(surface) if part]


def is_middle_dot_run(value: str) -> bool:
    return bool(value) and all(char in {"・", "･"} for char in value)


def render_parenthesis_spanning_sudachi_token(token: SudachiToken) -> list[RenderedPair]:
    semantic_replacement = PARENTHESIZED_SEMANTIC_TOKENS.get(token.surface)
    if semantic_replacement is not None:
        return [
            RenderedPair(surface=surface, reading=reading)
            for surface, reading in semantic_replacement
        ]
    parts = split_surface_preserving_parentheses(token.surface)
    component_surfaces = [part for part in parts if not is_parenthesis_run(part)]
    component_readings = infer_symbol_separated_component_readings(
        full_reading=token.reading,
        component_surfaces=component_surfaces,
    )
    rendered: list[RenderedPair] = []
    component_index = 0
    for part in parts:
        if is_parenthesis_run(part):
            rendered.append(RenderedPair(surface=part, reading=part))
        else:
            rendered.append(
                RenderedPair(surface=part, reading=component_readings[component_index])
            )
            component_index += 1
    return rendered


def split_surface_preserving_parentheses(surface: str) -> list[str]:
    return [part for part in INTERNAL_PARENTHESIS_RUN_RE.split(surface) if part]


def is_parenthesis_run(value: str) -> bool:
    return bool(value) and all(char in {"(", ")", "（", "）"} for char in value)


def is_proper_name_token(token: SudachiToken) -> bool:
    return "固有名詞" in token.pos.split(",")


def infer_symbol_separated_component_readings(
    *,
    full_reading: str,
    component_surfaces: list[str],
) -> list[str]:
    readings = [lookup_component_reading(surface) for surface in component_surfaces]
    if not full_reading or not is_katakana_reading(full_reading):
        return readings
    if all(readings) and "".join(readings) == full_reading:
        return readings

    candidates: list[list[str]] = []
    for index, surface in enumerate(component_surfaces):
        if not HAN_RE.search(surface) and not any(char.isalpha() and char.isascii() for char in surface):
            continue
        if any(not reading for offset, reading in enumerate(readings) if offset != index):
            continue
        prefix = "".join(readings[:index])
        suffix = "".join(readings[index + 1 :])
        if prefix and not full_reading.startswith(prefix):
            continue
        if suffix and not full_reading.endswith(suffix):
            continue
        residual_start = len(prefix)
        residual_end = len(full_reading) - len(suffix) if suffix else len(full_reading)
        if residual_end < residual_start:
            continue
        residual = full_reading[residual_start:residual_end]
        if residual and not is_katakana_reading(residual):
            continue
        candidate = list(readings)
        candidate[index] = residual
        candidates.append(candidate)
    unique_candidates = {tuple(candidate) for candidate in candidates}
    if len(unique_candidates) == 1:
        return list(next(iter(unique_candidates)))
    return [
        kana_surface_reading(surface)
        if all(is_hiragana_or_katakana(char) or char == "ー" for char in surface)
        else ""
        for surface in component_surfaces
    ]


def kana_surface_reading(surface: str) -> str:
    return "".join(hiragana_to_katakana(char) for char in surface)


def infer_space_spanning_component_readings(
    *,
    full_reading: str,
    component_surfaces: list[str],
) -> list[str]:
    if not component_surfaces:
        return []

    component_readings = [lookup_component_reading(surface) for surface in component_surfaces]
    if full_reading and is_katakana_reading(full_reading):
        if all(component_readings) and "".join(component_readings) == full_reading:
            return component_readings

        inferred = infer_single_residual_reading(
            full_reading=full_reading,
            component_readings=component_readings,
        )
        if inferred is not None:
            return inferred

    return component_readings


def infer_single_residual_reading(
    *,
    full_reading: str,
    component_readings: list[str],
) -> list[str] | None:
    for unknown_index in range(len(component_readings)):
        if any(
            not reading
            for index, reading in enumerate(component_readings)
            if index != unknown_index
        ):
            continue
        prefix = "".join(component_readings[:unknown_index])
        suffix = "".join(component_readings[unknown_index + 1 :])
        if prefix and not full_reading.startswith(prefix):
            continue
        if suffix and not full_reading.endswith(suffix):
            continue
        residual_start = len(prefix)
        residual_end = len(full_reading) - len(suffix) if suffix else len(full_reading)
        if residual_end <= residual_start:
            continue
        residual = full_reading[residual_start:residual_end]
        if not is_katakana_reading(residual):
            continue
        inferred = list(component_readings)
        inferred[unknown_index] = residual
        return inferred
    return None


def lookup_component_reading(surface: str) -> str:
    try:
        tokenizer = component_lookup_tokenizer()
    except Exception:
        return ""
    try:
        from sudachipy import tokenizer as sudachi_tokenizer

        morphemes = tokenizer.tokenize(surface, sudachi_tokenizer.Tokenizer.SplitMode.C)
    except Exception:
        return ""
    if len(morphemes) != 1:
        return ""
    morpheme = morphemes[0]
    if morpheme.surface() != surface:
        return ""
    reading = morpheme.reading_form()
    if not is_katakana_reading(reading):
        return ""
    return reading


def split_mixed_arabic_numeric_token(token: SudachiToken) -> list[RenderedPair] | None:
    surface = token.surface
    if numeric_compound_rule(surface) is not None:
        return None
    parts = MIXED_ARABIC_NUMERIC_PART_RE.findall(surface)
    if len(parts) != 2:
        return None
    numeric_indexes = [
        index for index, part in enumerate(parts) if ARABIC_DIGIT_RUN_RE.fullmatch(part)
    ]
    if len(numeric_indexes) != 1:
        return None
    numeric_index = numeric_indexes[0]
    lexical_index = 1 - numeric_index
    numeric_reading = lookup_component_reading(parts[numeric_index])
    full_reading = token.reading
    lexical_reading = ""
    if numeric_reading and is_katakana_reading(full_reading):
        if numeric_index == 0 and full_reading.startswith(numeric_reading):
            lexical_reading = full_reading[len(numeric_reading) :]
        elif numeric_index == 1 and full_reading.endswith(numeric_reading):
            lexical_reading = full_reading[: -len(numeric_reading)]
    if not is_katakana_reading(lexical_reading):
        lexical_reading = lookup_component_reading(parts[lexical_index])
    if not is_katakana_reading(lexical_reading):
        return None
    readings = ["", ""]
    readings[lexical_index] = lexical_reading
    return [
        RenderedPair(surface=part, reading=readings[index])
        for index, part in enumerate(parts)
    ]


@lru_cache(maxsize=1)
def component_lookup_tokenizer():
    from sudachipy import dictionary

    return dictionary.Dictionary(dict="full").create()


def is_katakana_reading(reading: str) -> bool:
    return bool(reading) and all(is_katakana(char) or char == "ー" for char in reading)


def is_katakana(char: str) -> bool:
    return "\u30a1" <= char <= "\u30fa" or char in {"ヽ", "ヾ"}


def is_whitespace_token(token: SudachiToken) -> bool:
    return token.surface.isspace() or token.pos.startswith("空白")


def canonical_whitespace_surface(surface: str) -> str:
    return surface.replace(ASCII_SPACE, NBSP)


def is_punctuation_token(token: SudachiToken) -> bool:
    return token.pos.startswith("補助記号")


def is_symbolic_sudachi_kaomoji(token: SudachiToken) -> bool:
    return (
        token.pos.split(",")[:3] == ["補助記号", "ＡＡ", "顔文字"]
        and SEMANTIC_PARENTHESIZED_JAPANESE_RE.fullmatch(token.surface) is None
    )


def is_numeric_token(token: SudachiToken) -> bool:
    return "数詞" in token.pos and is_numeric_digit_surface(token.surface)


def collect_numeric_sudachi_run(
    *,
    sudachi_spans: list[SpannedSudachiToken],
    start_index: int,
) -> tuple[str, int]:
    surfaces: list[str] = []
    index = start_index
    while index < len(sudachi_spans):
        token = sudachi_spans[index].token
        if not is_numeric_token(token):
            break
        surfaces.append(token.surface)
        index += 1
    return "".join(surfaces), index


def is_decoder_entry_symbol(entry: DecoderEntry) -> bool:
    return not entry.surface.strip() or all(char.isspace() for char in entry.surface)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def render_pairs_from_sudachi(tokens: list[SudachiToken]) -> str:
    pairs: list[RenderedPair] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if is_whitespace_token(token):
            pairs.append(render_sudachi_token(token))
            index += 1
            continue
        if is_symbolic_sudachi_kaomoji(token):
            pairs.append(render_sudachi_token(token))
            index += 1
            continue
        if is_numeric_token(token):
            start_index = index
            while index < len(tokens) and is_numeric_token(tokens[index]):
                index += 1
            numeric_surface = "".join(row.surface for row in tokens[start_index:index])
            if is_numeric_only_surface(numeric_surface):
                pairs.append(RenderedPair(surface=numeric_surface, reading=""))
            else:
                pairs.extend(render_sudachi_token(row) for row in tokens[start_index:index])
            continue
        if token_contains_space(token):
            pairs.extend(render_space_spanning_sudachi_token(token))
            index += 1
            continue
        if token_contains_internal_middle_dot(token):
            pairs.extend(render_middle_dot_spanning_sudachi_token(token))
            index += 1
            continue
        if token_contains_parenthesis(token):
            pairs.extend(render_parenthesis_spanning_sudachi_token(token))
            index += 1
            continue
        numeric_parts = split_mixed_arabic_numeric_token(token)
        if numeric_parts is not None:
            pairs.extend(numeric_parts)
            index += 1
            continue
        pairs.append(render_sudachi_token(token))
        index += 1
    return " ".join(f"{pair.surface}/{pair.reading}" for pair in pairs)


def render_pairs_from_decoder(candidate: DecoderCandidate) -> str:
    rendered: list[str] = []
    for entry in candidate.entries:
        surface = (
            canonical_whitespace_surface(entry.surface)
            if entry.surface.isspace()
            else entry.surface
        )
        reading = entry.reading or surface
        rendered.append(f"{surface}/{reading}")
    return " ".join(rendered)


STRATEGIES = {
    "agreement_prefer_decoder_v1": strategy_agreement_prefer_decoder_v1,
    "agreement_prefer_sudachi_v1": strategy_agreement_prefer_sudachi_v1,
    "aligned_hybrid_v1": strategy_aligned_hybrid_v1,
    "ngram_boundary_preferred_v1": strategy_ngram_boundary_preferred_v1,
    "ngram_grouping_preferred_v1": strategy_ngram_grouping_preferred_v1,
    "decoder_only_v1": strategy_decoder_only_v1,
    "sudachi_only_v1": strategy_sudachi_only_v1,
}
