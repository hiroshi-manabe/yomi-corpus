from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_review import (
    AlphabeticLLMJudgment,
    append_alphabetic_llm_judgments,
    build_llm_judgments_from_results,
    build_promotion_candidates,
    build_review_pack,
    load_jsonl,
)


class AlphabeticReviewTests(unittest.TestCase):
    def test_build_llm_judgments_from_results(self) -> None:
        rows = [
            {
                "item_id": "ok",
                "parsed": {"status": "in_scope", "confidence": "high", "note": "common"},
                "parse_error": None,
                "metadata": {
                    "source_row": {
                        "entity_key": "ok",
                        "strict_case": True,
                        "occurrence_count": 7,
                        "unit_count": 7,
                        "surface_forms": ["OK"],
                        "example_unit_ids": ["u1"],
                        "example_texts": ["OKを押してください。"],
                    }
                },
            }
        ]
        judgments = build_llm_judgments_from_results(rows, batch_name="batch_0001", source_path="x.jsonl")
        self.assertEqual(len(judgments), 1)
        self.assertEqual(judgments[0].entity_key, "ok")
        self.assertEqual(judgments[0].llm_status, "in_scope")
        self.assertEqual(judgments[0].occurrence_count, 7)

    def test_append_judgments_replaces_same_batch(self) -> None:
        temp_path = PROJECT_ROOT / "tests" / "tmp_alphabetic_llm_judgments.jsonl"
        if temp_path.exists():
            temp_path.unlink()
        append_alphabetic_llm_judgments(
            temp_path,
            [
                AlphabeticLLMJudgment(
                    batch_name="batch_0001",
                    entity_key="ok",
                    strict_case=True,
                    llm_status="in_scope",
                    confidence="high",
                    note="common",
                    occurrence_count=7,
                    unit_count=7,
                    surface_forms=["OK"],
                    example_unit_ids=["u1"],
                    example_texts=["OKを押してください。"],
                    source_path="a.jsonl",
                )
            ],
        )
        append_alphabetic_llm_judgments(
            temp_path,
            [
                AlphabeticLLMJudgment(
                    batch_name="batch_0001",
                    entity_key="concerts de midi",
                    strict_case=False,
                    llm_status="out_of_scope",
                    confidence="high",
                    note="title",
                    occurrence_count=1,
                    unit_count=1,
                    surface_forms=["Concerts de Midi"],
                    example_unit_ids=["u2"],
                    example_texts=["Concerts de Midiが開催されています。"],
                    source_path="b.jsonl",
                )
            ],
        )
        rows = load_jsonl(temp_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entity_key"], "concerts de midi")
        temp_path.unlink()

    def test_build_promotion_candidates_threshold(self) -> None:
        judgments = [
            {
                "batch_name": "batch_0001",
                "entity_key": "ok",
                "strict_case": True,
                "llm_status": "in_scope",
                "confidence": "high",
                "note": "common",
                "occurrence_count": 7,
                "unit_count": 7,
                "surface_forms": ["OK"],
                "example_unit_ids": ["u1"],
                "example_texts": ["OKを押してください。"],
                "source_path": "x.jsonl",
            },
            {
                "batch_name": "batch_0001",
                "entity_key": "concerts de midi",
                "strict_case": False,
                "llm_status": "out_of_scope",
                "confidence": "high",
                "note": "title",
                "occurrence_count": 1,
                "unit_count": 1,
                "surface_forms": ["Concerts de Midi"],
                "example_unit_ids": ["u2"],
                "example_texts": ["Concerts de Midiが開催されています。"],
                "source_path": "x.jsonl",
            },
        ]
        candidates = build_promotion_candidates(judgments, threshold_observations=3)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].entity_key, "ok")
        self.assertEqual(candidates[0].proposed_decision, "whitelist")

    def test_build_promotion_candidates_skips_conflicts(self) -> None:
        judgments = [
            {
                "batch_name": "batch_0001",
                "entity_key": "lab",
                "strict_case": True,
                "llm_status": "in_scope",
                "confidence": "medium",
                "note": "brand",
                "occurrence_count": 3,
                "unit_count": 3,
                "surface_forms": ["Lab"],
                "example_unit_ids": ["u1"],
                "example_texts": ["Labです。"],
                "source_path": "x.jsonl",
            },
            {
                "batch_name": "batch_0002",
                "entity_key": "lab",
                "strict_case": True,
                "llm_status": "out_of_scope",
                "confidence": "medium",
                "note": "generic",
                "occurrence_count": 4,
                "unit_count": 4,
                "surface_forms": ["Lab"],
                "example_unit_ids": ["u2"],
                "example_texts": ["Labです。"],
                "source_path": "y.jsonl",
            },
        ]
        candidates = build_promotion_candidates(judgments, threshold_observations=3)
        self.assertEqual(candidates, [])

    def test_build_review_pack(self) -> None:
        candidates = build_promotion_candidates(
            [
                {
                    "batch_name": "batch_0001",
                    "entity_key": "ok",
                    "strict_case": True,
                    "llm_status": "in_scope",
                    "confidence": "high",
                    "note": "common",
                    "occurrence_count": 7,
                    "unit_count": 7,
                    "surface_forms": ["OK"],
                    "example_unit_ids": ["u1"],
                    "example_texts": ["OKを押してください。"],
                    "source_path": "x.jsonl",
                }
            ],
            threshold_observations=3,
        )
        pack = build_review_pack(candidates, pack_id="alphabetic_candidates_batch_0001_v1")
        self.assertEqual(pack["review_stage"], "alphabetic_candidate_review")
        self.assertEqual(pack["item_count"], 1)
        self.assertEqual(pack["items"][0]["item_id"], "entity:ok")


if __name__ == "__main__":
    unittest.main()
