from __future__ import annotations

from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.types import DecoderCandidate, SudachiToken
from yomi_corpus.yomi.adapters import run_decoder, run_sudachi, run_decoder_many, run_sudachi_many
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.repairs import (
    apply_post_hybrid_repairs,
    normalize_parenthesized_semantic_tokens_rendered,
)
from yomi_corpus.yomi.numeric_compounds import normalize_numeric_compounds
from yomi_corpus.yomi.learned_lexicon import apply_exact_yomi_rewrites
from yomi_corpus.yomi.post_sudachi import (
    normalize_sudachi_tokens,
    serialize_sudachi_token,
)
from yomi_corpus.yomi.strategies import (
    apply_strategy,
    normalize_analysis_text_for_yomi,
    render_pairs_from_decoder,
    render_pairs_from_sudachi,
)
from yomi_corpus.yomi.token_codec import (
    canonicalize_whitespace_readings,
    legacy_rendered_to_yomi_tokens,
)


def generate_mechanical_yomi(
    text: str,
    *,
    config: YomiGenerationConfig,
    strategy_name: str | None = None,
) -> MechanicalYomi:
    normalized_text = normalize_analysis_text_for_yomi(text)
    raw_sudachi_tokens = run_sudachi(normalized_text, config, source_text=text)
    decoder_candidates = run_decoder(normalized_text, config, source_text=text)
    return _assemble_mechanical_yomi(text, config, strategy_name, raw_sudachi_tokens, decoder_candidates)


def generate_mechanical_yomi_many(
    texts: list[str], *, config: YomiGenerationConfig, strategy_name: str | None = None,
) -> list[MechanicalYomi]:
    normalized = [normalize_analysis_text_for_yomi(text) for text in texts]
    # The subprocess stdin protocols are line-oriented. Keep exceptional input
    # on the existing single-text path rather than changing source text.
    if any(not text or "\n" in text or "\r" in text for text in normalized):
        return [generate_mechanical_yomi(text, config=config, strategy_name=strategy_name) for text in texts]
    sudachi = run_sudachi_many(normalized, config, source_texts=texts)
    decoder = run_decoder_many(normalized, config, source_texts=texts)
    return [_assemble_mechanical_yomi(text, config, strategy_name, tokens, candidates)
            for text, tokens, candidates in zip(texts, sudachi, decoder, strict=True)]


def _assemble_mechanical_yomi(
    text: str,
    config: YomiGenerationConfig,
    strategy_name: str | None,
    raw_sudachi_tokens: list[SudachiToken],
    decoder_candidates: list[DecoderCandidate],
) -> MechanicalYomi:
    normalized_sudachi = normalize_sudachi_tokens(raw_sudachi_tokens, text=text)
    sudachi_tokens = list(normalized_sudachi.tokens)
    resolved_strategy = strategy_name or config.default_strategy
    strategy_result = apply_strategy(
        resolved_strategy,
        text=text,
        sudachi_tokens=sudachi_tokens,
        decoder_candidates=decoder_candidates,
    )
    learned_result = apply_exact_yomi_rewrites(
        strategy_result.rendered,
        rewrites_path=config.learned_exact_rewrites,
    )
    repair_result = apply_post_hybrid_repairs(
        learned_result.rendered,
        rules_path=config.post_hybrid_repair_rules,
    )
    parenthetical_result = normalize_parenthesized_semantic_tokens_rendered(
        repair_result.rendered
    )
    numeric_result = normalize_numeric_compounds(parenthetical_result.rendered)
    signals = list(strategy_result.signals)
    signals.extend(
        application.rule_id for application in normalized_sudachi.applications
    )
    if learned_result.applications:
        signals.append("apply_learned_exact_yomi_rewrites")
    if repair_result.metadata:
        signals.append("apply_post_hybrid_yomi_repairs")
    if parenthetical_result.metadata:
        signals.append("normalize_parenthesized_semantic_tokens")
    if numeric_result.applied_surfaces:
        signals.append("normalize_japanese_numeric_compounds")
    if numeric_result.formatted_numeric_surfaces:
        signals.append("normalize_formatted_numeric_expressions")
    if numeric_result.measurement_unit_surfaces:
        signals.append("normalize_common_measurement_unit_readings")
    canonical_tokens = canonicalize_whitespace_readings(
        legacy_rendered_to_yomi_tokens(
            numeric_result.rendered,
            text=text,
        )
    )
    return MechanicalYomi(
        rendered=numeric_result.rendered,
        certain=strategy_result.certain,
        tokens=canonical_tokens,
        sudachi={
            "raw": {
                "tokens": [
                    serialize_sudachi_token(token) for token in raw_sudachi_tokens
                ],
            },
            "normalized": {
                **normalized_sudachi.metadata(),
                "tokens": [
                    serialize_sudachi_token(token) for token in sudachi_tokens
                ],
                "rendered": render_pairs_from_sudachi(sudachi_tokens),
            },
            # Compatibility aliases for readers of pre-normalizer artifacts.
            "tokens": [serialize_sudachi_token(token) for token in sudachi_tokens],
            "rendered": render_pairs_from_sudachi(sudachi_tokens),
        },
        ngram_decoder={
            "candidates": [
                {
                    "rank": candidate.rank,
                    "score": candidate.score,
                    "rendered": render_pairs_from_decoder(candidate),
                    "entries": [
                        {
                            "surface": entry.surface,
                            "reading": entry.reading,
                            "final_order": entry.final_order,
                            "piece_orders": entry.piece_orders,
                        }
                        for entry in candidate.entries
                    ],
                }
                for candidate in decoder_candidates
            ]
        },
        post_hybrid_repairs={
            **repair_result.metadata,
            **(
                {"learned_exact_rewrites": list(learned_result.applications)}
                if learned_result.applications
                else {}
            ),
        },
        signals=signals,
    )
