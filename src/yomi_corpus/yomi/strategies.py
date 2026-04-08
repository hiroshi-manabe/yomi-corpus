from __future__ import annotations

from yomi_corpus.yomi.types import DecoderCandidate, SudachiToken, YomiStrategyResult


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


def render_pairs_from_sudachi(tokens: list[SudachiToken]) -> str:
    return " ".join(f"{token.surface}/{token.reading}" for token in tokens)


def render_pairs_from_decoder(candidate: DecoderCandidate) -> str:
    return " ".join(f"{entry.surface}/{entry.reading}" for entry in candidate.entries)


STRATEGIES = {
    "agreement_prefer_decoder_v1": strategy_agreement_prefer_decoder_v1,
    "agreement_prefer_sudachi_v1": strategy_agreement_prefer_sudachi_v1,
    "decoder_only_v1": strategy_decoder_only_v1,
    "sudachi_only_v1": strategy_sudachi_only_v1,
}
