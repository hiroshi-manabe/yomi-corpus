from __future__ import annotations

from yomi_corpus.selection_experiments import (
    STRATEGIES,
    _historical_gate_matches,
    _select_documents,
)
from yomi_corpus.historical_register import classify_document


def test_novelty_strategy_prefers_a_document_with_a_new_target() -> None:
    docs = {
        1: {"text_length": 100, "target_ids": (1,)},
        2: {"text_length": 100, "target_ids": (1,)},
        3: {"text_length": 100, "target_ids": (2,)},
    }
    strategy = next(item for item in STRATEGIES if item.strategy_id == "novelty")

    selected = _select_documents(
        docs,
        excluded=set(),
        target_weights={1: 1.0, 2: 0.8},
        strategy=strategy,
        character_budget=200,
    )

    assert selected == [1, 3]


def test_density_strategy_prefers_more_distinct_targets_per_character() -> None:
    docs = {
        1: {"text_length": 8_000, "target_ids": (1, 2, 3)},
        2: {"text_length": 1_000, "target_ids": (4, 5)},
    }
    strategy = next(item for item in STRATEGIES if item.strategy_id == "density")

    selected = _select_documents(
        docs,
        excluded=set(),
        target_weights={target_id: 1.0 for target_id in range(1, 6)},
        strategy=strategy,
        character_budget=8_000,
    )

    assert selected[0] == 2


def test_campaign_historical_gate_needs_both_ratios() -> None:
    dominant = classify_document("舊國體を論ず。敵艦を追ひつゝ進む。之れを可と爲す。現代文です。")
    quotation = classify_document("現代文です。現代文です。現代文です。学而時習之、不亦説乎。")

    assert _historical_gate_matches(dominant) is True
    assert _historical_gate_matches(quotation) is False
