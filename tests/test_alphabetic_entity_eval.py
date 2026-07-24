from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "evals" / "alphabetic_entity_judge" / "gold_v1.jsonl"
SUMMARY = ROOT / "data" / "evals" / "alphabetic_entity_judge" / "gold_v1.summary.json"


def test_alphabetic_entity_gold_v1_shape_and_balance() -> None:
    rows = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line]

    assert len(rows) == 95
    assert len({row["case_id"] for row in rows}) == 95
    assert len({row["entity_key"] for row in rows}) == 95
    assert Counter(row["expected_status"] for row in rows) == {
        "in_scope": 48,
        "out_of_scope": 47,
    }
    assert Counter(row["split"] for row in rows) == {
        "development": 72,
        "holdout": 23,
    }
    assert Counter(row["difficulty"] for row in rows) == {
        "clear": 72,
        "boundary": 23,
    }
    assert all(row["surface_forms"] for row in rows)
    assert all(row["example_texts"] for row in rows)
    assert all(row["source_batch"].startswith("dev_batch_") for row in rows)

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["case_count"] == len(rows)
    assert summary["status_counts"] == dict(
        Counter(row["expected_status"] for row in rows)
    )
    assert summary["split_counts"] == dict(Counter(row["split"] for row in rows))
    assert summary["difficulty_counts"] == dict(
        Counter(row["difficulty"] for row in rows)
    )
