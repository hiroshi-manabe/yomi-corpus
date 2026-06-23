from __future__ import annotations

import runpy
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEXT_MODULE = runpy.run_path(str(PROJECT_ROOT / "next"), run_name="next_cli_test")
format_next_summary = NEXT_MODULE["format_next_summary"]


class NextCliTests(unittest.TestCase):
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
                "current_stage": "alphabetic_judged",
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


if __name__ == "__main__":
    unittest.main()
