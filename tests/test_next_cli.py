from __future__ import annotations

import runpy
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEXT_MODULE = runpy.run_path(str(PROJECT_ROOT / "next"), run_name="next_cli_test")
format_next_summary = NEXT_MODULE["format_next_summary"]
from yomi_corpus.cli_format import format_stage_summary


class NextCliTests(unittest.TestCase):
    def test_format_stage_summary_only_shows_current_and_next_stage(self) -> None:
        rendered = format_stage_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "scope_triage_llm_completed",
                "next_stage": "yomi_generated",
                "advanced": True,
                "artifacts": {
                    "scope_triage_llm_job_completed": "64",
                    "scope_triage_llm_job_total": "64",
                },
            }
        )

        self.assertEqual(
            rendered,
            "current_stage: scope_triage_llm_completed\nnext_stage: yomi_generated",
        )

    def test_format_suppresses_completed_prior_llm_status_on_non_llm_stage(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "alphabetic_promotion_candidates",
                "advanced": True,
                "artifacts": {
                    "alphabetic_judgment_llm_job_completed": "2",
                    "alphabetic_judgment_llm_job_total": "2",
                    "alphabetic_judgment_llm_job_status": "completed",
                    "alphabetic_promotion_candidates_jsonl": "data/state/alphabetic/promotion_candidates.jsonl",
                },
            }
        )

        self.assertNotIn("LLM progress", rendered)
        self.assertNotIn("LLM job", rendered)
        self.assertIn("Output: data/state/alphabetic/promotion_candidates.jsonl", rendered)

    def test_format_shows_running_llm_status_on_blocked_llm_stage(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "alphabetic_llm_judged",
                "advanced": False,
                "blocking_reason": "LLM background job is running; rerun ./next to poll or resume.",
                "artifacts": {
                    "alphabetic_judgment_llm_job_completed": "1",
                    "alphabetic_judgment_llm_job_total": "2",
                    "alphabetic_judgment_llm_job_status": "running",
                },
            }
        )

        self.assertIn("LLM progress (alphabetic_judgment): 1/2 completed", rendered)
        self.assertIn("LLM job (alphabetic_judgment): running", rendered)

    def test_format_shows_human_review_required(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "alphabetic_llm_judged",
                "advanced": False,
                "blocking_reason": "Alphabetic promotion candidates require human review.",
                "artifacts": {
                    "human_review_required": "true",
                    "human_review_gate": "promotion_candidate_review",
                    "human_review_item_count": "3",
                },
            }
        )

        self.assertIn(
            "Human review: required - promotion_candidate_review (3)",
            rendered,
        )
        self.assertIn("Status: blocked - Alphabetic promotion candidates require human review.", rendered)

    def test_format_shows_human_review_skipped(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "alphabetic_promotion_candidates",
                "advanced": True,
                "artifacts": {
                    "human_review_required": "true",
                    "human_review_skipped": "true",
                    "human_review_gate": "promotion_candidate_review",
                    "human_review_item_count": "3",
                },
            }
        )

        self.assertIn(
            "Human review: required - promotion_candidate_review (3)",
            rendered,
        )
        self.assertIn("Human review skipped: promotion_candidate_review", rendered)


if __name__ == "__main__":
    unittest.main()
