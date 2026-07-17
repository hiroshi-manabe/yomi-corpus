from __future__ import annotations

from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.adapters import run_decoder, run_sudachi
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.repairs import apply_post_hybrid_repairs
from yomi_corpus.yomi.numeric_compounds import normalize_numeric_compounds
from yomi_corpus.yomi.strategies import (
    apply_strategy,
    normalize_ascii_spaces_for_yomi,
    render_pairs_from_decoder,
    render_pairs_from_sudachi,
)


def generate_mechanical_yomi(
    text: str,
    *,
    config: YomiGenerationConfig,
    strategy_name: str | None = None,
) -> MechanicalYomi:
    normalized_text = normalize_ascii_spaces_for_yomi(text)
    sudachi_tokens = run_sudachi(normalized_text, config)
    decoder_candidates = run_decoder(normalized_text, config)
    resolved_strategy = strategy_name or config.default_strategy
    strategy_result = apply_strategy(
        resolved_strategy,
        text=normalized_text,
        sudachi_tokens=sudachi_tokens,
        decoder_candidates=decoder_candidates,
    )
    repair_result = apply_post_hybrid_repairs(
        strategy_result.rendered,
        rules_path=config.post_hybrid_repair_rules,
    )
    numeric_result = normalize_numeric_compounds(repair_result.rendered)
    signals = list(strategy_result.signals)
    if repair_result.metadata:
        signals.append("apply_post_hybrid_yomi_repairs")
    if numeric_result.applied_surfaces:
        signals.append("normalize_japanese_numeric_compounds")
    return MechanicalYomi(
        rendered=numeric_result.rendered,
        certain=strategy_result.certain,
        sudachi={
            "tokens": [
                {
                    "surface": token.surface,
                    "pos": token.pos,
                    "dictionary_form": token.dictionary_form,
                    "normalized_form": token.normalized_form,
                    "reading": token.reading,
                }
                for token in sudachi_tokens
            ],
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
        post_hybrid_repairs=repair_result.metadata,
        signals=signals,
    )
