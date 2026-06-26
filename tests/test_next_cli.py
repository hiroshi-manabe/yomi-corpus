from __future__ import annotations

import runpy
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEXT_MODULE = runpy.run_path(str(PROJECT_ROOT / "next"), run_name="next_cli_test")
format_next_summary = NEXT_MODULE["format_next_summary"]
format_auto_progress_start = NEXT_MODULE["format_auto_progress_start"]
format_auto_progress_done = NEXT_MODULE["format_auto_progress_done"]
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

    def test_format_shows_empty_llm_queue_without_zero_over_zero_progress(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "yomi_reading_llm_completed",
                "advanced": True,
                "artifacts": {
                    "yomi_reading_queued": "0",
                    "yomi_reading_llm_job_completed": "0",
                    "yomi_reading_llm_job_total": "0",
                    "yomi_reading_llm_job_status": "completed",
                },
            }
        )

        self.assertIn("LLM requests (yomi_reading): none queued", rendered)
        self.assertNotIn("0/0 completed", rendered)

    def test_format_distinguishes_yomi_raw_parse_failures_from_final_apply(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "yomi_reading_llm_completed",
                "advanced": True,
                "artifacts": {
                    "yomi_reading_queued": "155",
                    "yomi_reading_llm_job_completed": "155",
                    "yomi_reading_llm_job_total": "155",
                    "yomi_reading_llm_job_failed": "3",
                    "yomi_reading_llm_job_status": "completed",
                    "yomi_reading_apply_summary_json": "data/units/dev_batch_0001/yomi_reading_apply_summary.json",
                    "yomi_reading_checked": "155",
                    "yomi_reading_matched": "154",
                    "yomi_reading_mismatched": "1",
                    "yomi_reading_parse_error": "0",
                    "yomi_reading_missing_result": "0",
                },
            }
        )

        self.assertIn(
            "LLM progress (yomi_reading): 155/155 completed, 3 raw parse failures",
            rendered,
        )
        self.assertIn(
            "Yomi reading final: 155 checked, 154 matched, 1 mismatched, 0 final parse errors, 0 missing",
            rendered,
        )
        self.assertNotIn("3 failed", rendered)

    def test_format_suppresses_stale_llm_status_on_queue_stage(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "yomi_reading_queued",
                "next_stage": "yomi_reading_llm_completed",
                "advanced": True,
                "artifacts": {
                    "yomi_reading_queued": "155",
                    "yomi_reading_llm_job_completed": "0",
                    "yomi_reading_llm_job_total": "0",
                    "yomi_reading_llm_job_status": "completed",
                    "yomi_reading_input_jsonl": "data/units/dev_batch_0001/yomi_reading_input.jsonl",
                    "units_yomi_llm_readings_jsonl": "data/units/dev_batch_0001/units.yomi.llm_readings.jsonl",
                },
            }
        )

        self.assertNotIn("LLM progress", rendered)
        self.assertNotIn("LLM job", rendered)
        self.assertNotIn("0/0 completed", rendered)
        self.assertIn("Output: data/units/dev_batch_0001/yomi_reading_input.jsonl", rendered)
        self.assertNotIn("Output: data/units/dev_batch_0001/units.yomi.llm_readings.jsonl", rendered)
        self.assertIn("Next: yomi_reading_llm_completed", rendered)

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

    def test_format_shows_auto_stage_trace(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "yomi_finalized",
                "advanced": True,
                "auto": True,
                "auto_steps": [
                    {"stage": "final_review_applied", "advanced": True},
                    {"stage": "yomi_strong_repair_queued", "advanced": True},
                    {"stage": "yomi_finalized", "advanced": True},
                ],
                "artifacts": {
                    "units_yomi_final_jsonl": "data/units/dev_batch_0001/units.yomi.final.jsonl",
                },
            }
        )

        self.assertIn(
            "Auto stages: final_review_applied, yomi_strong_repair_queued, yomi_finalized",
            rendered,
        )
        self.assertIn("Output: data/units/dev_batch_0001/units.yomi.final.jsonl", rendered)

    def test_format_final_review_prepared_shows_push_hint(self) -> None:
        rendered = format_next_summary(
            {
                "track_name": "dev",
                "batch_name": "dev_batch_0001",
                "current_stage": "final_review_prepared",
                "advanced": True,
                "artifacts": {
                    "final_review_pack_json": "docs/review/packs/yomi_final_dev_batch_0001_v1.json",
                    "review_site_manifest_json": "docs/review/manifest.json",
                },
            }
        )

        self.assertIn("Output: docs/review/packs/yomi_final_dev_batch_0001_v1.json", rendered)
        self.assertIn(
            "Review page: commit and push the generated docs/review artifacts, then open GitHub Pages.",
            rendered,
        )

    def test_format_auto_progress_start_shows_next_stage(self) -> None:
        rendered = format_auto_progress_start(
            {
                "track_name": "dev",
                "current_batch_name": "dev_batch_0001",
                "current_stage": "yomi_reading_queued",
                "next_stage": "yomi_reading_llm_completed",
            }
        )

        self.assertEqual(
            rendered,
            "Auto: running yomi_reading_llm_completed for dev dev_batch_0001 "
            "(current: yomi_reading_queued)",
        )

    def test_format_auto_progress_done_shows_blocking_reason(self) -> None:
        rendered = format_auto_progress_done(
            {
                "current_stage": "yomi_reading_llm_completed",
                "advanced": False,
                "blocking_reason": "LLM background job is running; rerun ./next to poll or resume.",
            }
        )

        self.assertEqual(
            rendered,
            "Auto: stopped at yomi_reading_llm_completed (advanced: false; "
            "blocked: LLM background job is running; rerun ./next to poll or resume.)",
        )


if __name__ == "__main__":
    unittest.main()
