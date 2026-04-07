from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.pricing import estimate_cost_usd
from yomi_corpus.llm.usage_report import summarize_batch_job, summarize_results_jsonl


class UsageReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = PROJECT_ROOT / "tests" / "tmp_usage_report"
        if self.tmp_root.exists():
            for path in sorted(self.tmp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.tmp_root.exists():
            for path in sorted(self.tmp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()

    def test_estimate_cost_uses_cached_tokens_separately(self) -> None:
        estimate = estimate_cost_usd(
            {
                "input_tokens": 1200,
                "cached_input_tokens": 1024,
                "output_tokens": 10,
                "reasoning_tokens": 2,
                "total_tokens": 1210,
            },
            model="gpt-5.4-mini",
            processing_tier="standard",
        )
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.billable_input_tokens, 176)
        self.assertGreater(estimate.estimated_total_cost_usd, 0.0)

    def test_summarize_results_jsonl(self) -> None:
        results_path = self.tmp_root / "results.jsonl"
        results_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "item_id": "a",
                            "usage": {
                                "input_tokens": 1200,
                                "cached_input_tokens": 1024,
                                "output_tokens": 10,
                                "reasoning_tokens": 2,
                                "total_tokens": 1210,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "item_id": "b",
                            "usage": {
                                "input_tokens": 300,
                                "cached_input_tokens": 0,
                                "output_tokens": 4,
                                "reasoning_tokens": 0,
                                "total_tokens": 304,
                            },
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        summary = summarize_results_jsonl(
            str(results_path),
            model="gpt-5.4-mini",
            processing_tier="standard",
            pricing_config_path="config/pricing/openai_models.toml",
        )
        self.assertEqual(summary["item_count"], 2)
        self.assertEqual(summary["usage"]["input_tokens"], 1500)
        self.assertEqual(summary["usage"]["cached_input_tokens"], 1024)
        self.assertGreater(summary["estimated_total_cost_usd"], 0.0)

    def test_summarize_batch_job(self) -> None:
        job_dir = self.tmp_root / "job_0001"
        job_dir.mkdir()
        (job_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "task_name": "alphabetic_entity_judge",
                    "model": "gpt-5.4-mini",
                    "item_count": 1,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (job_dir / "status.json").write_text(
            json.dumps(
                {
                    "state": "fetched",
                    "remote_status": "completed",
                    "batch_id": "batch-1",
                    "remote_snapshot": {
                        "usage": {
                            "input_tokens": 600,
                            "input_tokens_details": {"cached_tokens": 512},
                            "output_tokens": 5,
                            "output_tokens_details": {"reasoning_tokens": 1},
                            "total_tokens": 605,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (job_dir / "results.parsed.jsonl").write_text(
            json.dumps(
                {
                    "item_id": "led zeppelin",
                    "usage": {
                        "input_tokens": 600,
                        "cached_input_tokens": 512,
                        "output_tokens": 5,
                        "reasoning_tokens": 1,
                        "total_tokens": 605,
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        summary = summarize_batch_job(
            str(job_dir),
            pricing_config_path="config/pricing/openai_models.toml",
        )
        self.assertEqual(summary["processing_tier"], "batch")
        self.assertEqual(summary["usage"]["cached_input_tokens"], 512)
        self.assertGreater(summary["estimated_total_cost_usd"], 0.0)
