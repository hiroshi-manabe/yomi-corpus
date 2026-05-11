from __future__ import annotations

from dataclasses import dataclass

from yomi_corpus.yomi.types import (
    DecoderCandidate,
    DecoderEntry,
    SudachiToken,
    YomiStrategyResult,
)
DECODER_OVERRIDE_SURFACES = frozenset({"方"})


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
    signals = ["strategy:aligned_hybrid_v1"]
    if not decoder_candidates:
        signals.extend(["decoder_no_candidates", "fallback_sudachi"])
        return YomiStrategyResult(
            strategy="aligned_hybrid_v1",
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

    rendered_pairs: list[RenderedPair] = []
    index = 0
    while index < len(sudachi_spans):
        current = sudachi_spans[index]
        token = current.token

        if is_whitespace_token(token):
            signals.append("skip_whitespace_token")
            index += 1
            continue

        if is_numeric_token(token):
            numeric_surface, next_index = collect_numeric_sudachi_run(
                sudachi_spans=sudachi_spans,
                start_index=index,
            )
            rendered_pairs.append(RenderedPair(surface=numeric_surface, reading=""))
            signals.append("group_numeric_run")
            index = next_index
            continue

        exact_entry = exact_decoder_by_span.get((current.start, current.end))
        if exact_entry is not None:
            pair, pair_signals = render_exact_aligned_token(
                token=token,
                exact_entry=exact_entry,
                all_decoder_spans=decoder_spans_by_rank,
                start=current.start,
                end=current.end,
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
        strategy="aligned_hybrid_v1",
        rendered=" ".join(f"{pair.surface}/{pair.reading}" for pair in rendered_pairs),
        certain=certain,
        signals=dedupe_preserve_order(signals),
    )


def span_sudachi_tokens(text: str, tokens: list[SudachiToken]) -> list[SpannedSudachiToken]:
    spans: list[SpannedSudachiToken] = []
    cursor = 0
    for token in tokens:
        start = text.find(token.surface, cursor)
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
        start = text.find(entry.surface, cursor)
        if start < 0:
            raise ValueError(f"Could not align decoder entry surface {entry.surface!r} in text {text!r}")
        end = start + len(entry.surface)
        spans.append(SpannedDecoderEntry(entry=entry, start=start, end=end))
        cursor = end
    return spans


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
    if len(decoder_entries) <= 1:
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
        rendered.append(
            RenderedPair(
                surface=entry.entry.surface,
                reading=entry.entry.reading or entry.entry.surface,
            )
        )
    return rendered


def render_exact_aligned_token(
    *,
    token: SudachiToken,
    exact_entry: DecoderEntry,
    all_decoder_spans: list[list[SpannedDecoderEntry]],
    start: int,
    end: int,
) -> tuple[RenderedPair, list[str]]:
    signals: list[str] = []
    if is_punctuation_token(token):
        signals.append("normalize_punctuation_surface")
        return RenderedPair(surface=token.surface, reading=token.surface), signals

    if should_use_decoder_override(
        token=token,
        exact_entry=exact_entry,
        all_decoder_spans=all_decoder_spans,
        start=start,
        end=end,
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
    all_decoder_spans: list[list[SpannedDecoderEntry]],
    start: int,
    end: int,
) -> bool:
    if not exact_entry.reading:
        return False
    if not decoder_entry_has_ngram_support(exact_entry):
        return False
    if token.reading == exact_entry.reading:
        return False
    if token.surface not in DECODER_OVERRIDE_SURFACES:
        return False

    votes: dict[str, int] = {}
    for candidate_spans in all_decoder_spans:
        for entry in candidate_spans:
            if entry.start == start and entry.end == end and entry.entry.reading:
                votes[entry.entry.reading] = votes.get(entry.entry.reading, 0) + 1
                break
    if not votes:
        return False
    winning_reading, winning_votes = max(votes.items(), key=lambda item: (item[1], item[0]))
    return winning_reading == exact_entry.reading and winning_votes >= 2


def decoder_entry_has_ngram_support(entry: DecoderEntry) -> bool:
    return entry.final_order >= 2


def decoder_entry_has_previous_entry_support(entry: DecoderEntry) -> bool:
    if not entry.piece_orders:
        return False
    return entry.piece_orders[0] >= 2


def render_sudachi_token(token: SudachiToken) -> RenderedPair:
    if is_punctuation_token(token):
        return RenderedPair(surface=token.surface, reading=token.surface)
    if is_numeric_token(token):
        return RenderedPair(surface=token.surface, reading="")
    return RenderedPair(surface=token.surface, reading=token.reading or token.surface)


def is_whitespace_token(token: SudachiToken) -> bool:
    return token.surface.isspace() or token.pos.startswith("空白")


def is_punctuation_token(token: SudachiToken) -> bool:
    return token.pos.startswith("補助記号")


def is_numeric_token(token: SudachiToken) -> bool:
    return "数詞" in token.pos and token.surface.isdecimal()


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
            index += 1
            continue
        if is_numeric_token(token):
            surfaces: list[str] = []
            while index < len(tokens) and is_numeric_token(tokens[index]):
                surfaces.append(tokens[index].surface)
                index += 1
            pairs.append(RenderedPair(surface="".join(surfaces), reading=""))
            continue
        pairs.append(render_sudachi_token(token))
        index += 1
    return " ".join(f"{pair.surface}/{pair.reading}" for pair in pairs)


def render_pairs_from_decoder(candidate: DecoderCandidate) -> str:
    rendered: list[str] = []
    for entry in candidate.entries:
        reading = entry.reading or entry.surface
        rendered.append(f"{entry.surface}/{reading}")
    return " ".join(rendered)


STRATEGIES = {
    "agreement_prefer_decoder_v1": strategy_agreement_prefer_decoder_v1,
    "agreement_prefer_sudachi_v1": strategy_agreement_prefer_sudachi_v1,
    "aligned_hybrid_v1": strategy_aligned_hybrid_v1,
    "decoder_only_v1": strategy_decoder_only_v1,
    "sudachi_only_v1": strategy_sudachi_only_v1,
}
