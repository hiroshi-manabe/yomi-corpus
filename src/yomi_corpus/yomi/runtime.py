from __future__ import annotations

from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.adapters import run_decoder, run_sudachi
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.strategies import apply_strategy, render_pairs_from_decoder, render_pairs_from_sudachi


def generate_mechanical_yomi(
    text: str,
    *,
    config: YomiGenerationConfig,
    strategy_name: str | None = None,
) -> MechanicalYomi:
    sudachi_tokens = run_sudachi(text, config)
    decoder_candidates = run_decoder(text, config)
    resolved_strategy = strategy_name or config.default_strategy
    strategy_result = apply_strategy(
        resolved_strategy,
        text=text,
        sudachi_tokens=sudachi_tokens,
        decoder_candidates=decoder_candidates,
    )
    return MechanicalYomi(
        rendered=strategy_result.rendered,
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
                            "original_segments": [
                                {
                                    "surface": segment.surface,
                                    "reading": segment.reading,
                                }
                                for segment in entry.original_segments
                            ],
                        }
                        for entry in candidate.entries
                    ],
                }
                for candidate in decoder_candidates
            ]
        },
        signals=list(strategy_result.signals),
    )
