from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.alphabetic_review import (
    build_review_promoted_decisions,
    load_json,
    load_review_submissions,
    replay_review_submissions,
    rewrite_alphabetic_decisions_with_review_promotions,
    store_review_submission,
)
from yomi_corpus.alphabetic_state import AlphabeticDecision, load_alphabetic_decisions, upsert_alphabetic_decision


class AlphabeticReviewImportTests(unittest.TestCase):
    def test_replay_review_submission_accepts_defaults_and_overrides_exceptions(self) -> None:
        pack = {
            "review_stage": "alphabetic_candidate_review",
            "pack_id": "pack_1",
            "item_count": 3,
            "items": [
                {"item_id": "entity:a", "seq": 1, "entity_key": "a", "strict_case": False, "proposed_action": "whitelist"},
                {"item_id": "entity:b", "seq": 2, "entity_key": "b", "strict_case": False, "proposed_action": "blacklist"},
                {"item_id": "entity:c", "seq": 3, "entity_key": "c", "strict_case": False, "proposed_action": "whitelist"},
            ],
        }
        submission = {
            "submission_type": "review_patch",
            "review_stage": "alphabetic_candidate_review",
            "pack_id": "pack_1",
            "submission_id": "sub_1",
            "generated_at_epoch": 10,
            "reviewed_ranges": [{"from_seq": 1, "to_seq": 3}],
            "overrides": [{"item_id": "entity:b", "decision": "reject"}],
        }

        effective = replay_review_submissions(pack, [submission])
        self.assertEqual(effective["entity:a"]["status"], "accept")
        self.assertEqual(effective["entity:b"]["status"], "reject")
        self.assertEqual(effective["entity:c"]["status"], "accept")

        promoted = build_review_promoted_decisions(pack, effective)
        self.assertEqual([(row.entity_key, row.status) for row in promoted], [("a", "whitelist"), ("c", "whitelist")])

    def test_later_submission_overwrites_earlier_overlap(self) -> None:
        pack = {
            "review_stage": "alphabetic_candidate_review",
            "pack_id": "pack_1",
            "item_count": 2,
            "items": [
                {"item_id": "entity:a", "seq": 1, "entity_key": "a", "strict_case": False, "proposed_action": "whitelist"},
                {"item_id": "entity:b", "seq": 2, "entity_key": "b", "strict_case": False, "proposed_action": "blacklist"},
            ],
        }
        first = {
            "submission_type": "review_patch",
            "review_stage": "alphabetic_candidate_review",
            "pack_id": "pack_1",
            "submission_id": "sub_1",
            "generated_at_epoch": 10,
            "reviewed_ranges": [{"from_seq": 1, "to_seq": 2}],
            "overrides": [{"item_id": "entity:b", "decision": "reject"}],
        }
        second = {
            "submission_type": "review_patch",
            "review_stage": "alphabetic_candidate_review",
            "pack_id": "pack_1",
            "submission_id": "sub_2",
            "generated_at_epoch": 20,
            "reviewed_ranges": [{"from_seq": 2, "to_seq": 2}],
            "overrides": [],
        }

        effective = replay_review_submissions(pack, [first, second])
        self.assertEqual(effective["entity:b"]["status"], "accept")

    def test_store_and_load_review_submissions(self) -> None:
        submission = {
            "submission_type": "review_patch",
            "review_stage": "alphabetic_candidate_review",
            "pack_id": "pack_1",
            "submission_id": "pack_1__2026-04-08T12:00:00Z",
            "generated_at_epoch": 10,
            "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
            "overrides": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            stored = store_review_submission(submission, submission_store_dir=tmp)
            self.assertTrue(stored.exists())
            loaded = load_review_submissions(tmp, review_stage="alphabetic_candidate_review", pack_id="pack_1")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["submission_id"], submission["submission_id"])

    def test_rewrite_decisions_preserves_non_review_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "token_decisions.jsonl"
            upsert_alphabetic_decision(
                decisions_path,
                AlphabeticDecision(
                    entity_key="manual_seed",
                    strict_case=False,
                    status="whitelist",
                    source="manual",
                ),
            )
            rewrite_alphabetic_decisions_with_review_promotions(
                decisions_path,
                [
                    AlphabeticDecision(
                        entity_key="rvh",
                        strict_case=False,
                        status="whitelist",
                        source="review:alphabetic_candidate_review",
                    )
                ],
            )
            decisions = load_alphabetic_decisions(decisions_path)
            self.assertEqual(decisions["manual_seed"].source, "manual")
            self.assertEqual(decisions["rvh"].source, "review:alphabetic_candidate_review")


if __name__ == "__main__":
    unittest.main()
