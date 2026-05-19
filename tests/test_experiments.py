from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.experiments import compare_prompt_experiments, run_prompt_experiment
from yomi_corpus.llm.schemas import LLMResult


class FakeExperimentBackend:
    def __init__(self, results_by_item_id: dict[str, dict[str, object]]) -> None:
        self.results_by_item_id = results_by_item_id
        self.api_key_source = "test"

    def run_sync(self, task_config, items):
        results = []
        for item in items:
            payload = self.results_by_item_id[item.item_id]
            results.append(
                LLMResult(
                    item_id=item.item_id,
                    raw_text=json.dumps(payload["parsed"], ensure_ascii=False),
                    parsed=payload["parsed"],
                    usage=payload.get("usage"),
                    metadata=item.metadata,
                )
            )
        return results


class ExperimentHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = PROJECT_ROOT / "tests" / "tmp_experiments"
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

    def test_run_prompt_experiment_writes_summary_and_scores(self) -> None:
        backend = FakeExperimentBackend(
            {
                "android": {
                    "parsed": {"status": "in_scope", "confidence": "high", "note": "ok"},
                    "usage": {
                        "input_tokens": 1100,
                        "cached_input_tokens": 1024,
                        "output_tokens": 6,
                        "reasoning_tokens": 1,
                        "total_tokens": 1106,
                    },
                },
                "iphone": {
                    "parsed": {"status": "in_scope", "confidence": "high", "note": "ok"},
                    "usage": {
                        "input_tokens": 1090,
                        "cached_input_tokens": 1024,
                        "output_tokens": 6,
                        "reasoning_tokens": 1,
                        "total_tokens": 1096,
                    },
                },
                "concerts de midi": {
                    "parsed": {"status": "out_of_scope", "confidence": "high", "note": "skip"},
                    "usage": {
                        "input_tokens": 1120,
                        "cached_input_tokens": 1024,
                        "output_tokens": 7,
                        "reasoning_tokens": 1,
                        "total_tokens": 1127,
                    },
                },
                "ok": {
                    "parsed": {"status": "in_scope", "confidence": "medium", "note": "common UI term"},
                    "usage": {
                        "input_tokens": 980,
                        "cached_input_tokens": 0,
                        "output_tokens": 6,
                        "reasoning_tokens": 1,
                        "total_tokens": 986,
                    },
                },
            }
        )
        run_dir = self.tmp_root / "run_0001"
        summary = run_prompt_experiment(
            task_config_path="config/llm/alphabetic_entity_judge.toml",
            eval_jsonl_path="data/evals/alphabetic_entity_judge/dev.jsonl",
            run_dir=str(run_dir),
            backend=backend,
        )

        self.assertEqual(summary["score"]["pass_count"], 4)
        self.assertEqual(summary["score"]["fail_count"], 0)
        self.assertTrue((run_dir / "summary.json").exists())
        self.assertTrue((run_dir / "scored.jsonl").exists())

    def test_compare_prompt_experiments_reports_changed_cases(self) -> None:
        good_backend = FakeExperimentBackend(
            {
                "android": {"parsed": {"status": "in_scope"}, "usage": None},
                "iphone": {"parsed": {"status": "in_scope"}, "usage": None},
                "concerts de midi": {"parsed": {"status": "out_of_scope"}, "usage": None},
                "ok": {"parsed": {"status": "in_scope"}, "usage": None},
            }
        )
        weak_backend = FakeExperimentBackend(
            {
                "android": {"parsed": {"status": "in_scope"}, "usage": None},
                "iphone": {"parsed": {"status": "out_of_scope"}, "usage": None},
                "concerts de midi": {"parsed": {"status": "out_of_scope"}, "usage": None},
                "ok": {"parsed": {"status": "out_of_scope"}, "usage": None},
            }
        )

        base_dir = self.tmp_root / "base"
        candidate_dir = self.tmp_root / "candidate"
        run_prompt_experiment(
            task_config_path="config/llm/alphabetic_entity_judge.toml",
            eval_jsonl_path="data/evals/alphabetic_entity_judge/dev.jsonl",
            run_dir=str(base_dir),
            backend=weak_backend,
        )
        run_prompt_experiment(
            task_config_path="config/llm/alphabetic_entity_judge.toml",
            eval_jsonl_path="data/evals/alphabetic_entity_judge/dev.jsonl",
            run_dir=str(candidate_dir),
            backend=good_backend,
        )

        comparison = compare_prompt_experiments(str(base_dir), str(candidate_dir))
        self.assertEqual(comparison["changed_case_count"], 2)
        self.assertEqual(comparison["changed_cases"][0]["item_id"], "iphone")
        self.assertEqual(comparison["changed_cases"][0]["change_type"], "fixed")

    def test_run_prompt_experiment_overrides_yomi_rendering(self) -> None:
        eval_path = self.tmp_root / "yomi_eval.jsonl"
        eval_path.write_text(
            json.dumps(
                {
                    "unit_id": "u1",
                    "text": "大学です。",
                    "rendered": "大学/ダイガク です/デス 。/。",
                    "expected_status": "OK",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        backend = FakeExperimentBackend({"u1": {"parsed": {"status": "OK"}, "usage": None}})
        run_dir = self.tmp_root / "yomi_full"

        summary = run_prompt_experiment(
            task_config_path="config/llm/yomi_triage.toml",
            eval_jsonl_path=str(eval_path),
            run_dir=str(run_dir),
            rendered_yomi_display="full",
            backend=backend,
        )

        self.assertEqual(summary["effective_config"]["rendered_yomi_display"], "full")
        items = [json.loads(line) for line in (run_dir / "items.jsonl").read_text().splitlines()]
        self.assertEqual(items[0]["metadata"]["rendered_prompt"], "大学/ダイガク です/デス 。/。")
        self.assertIn("です/デス", items[0]["prompt"])

    def test_run_prompt_experiment_supports_furigana_yomi_rendering(self) -> None:
        eval_path = self.tmp_root / "yomi_eval.jsonl"
        eval_path.write_text(
            json.dumps(
                {
                    "unit_id": "u1",
                    "text": "大学です。",
                    "rendered": "大学/ダイガク です/デス 。/。",
                    "expected_status": "OK",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        backend = FakeExperimentBackend({"u1": {"parsed": {"status": "OK"}, "usage": None}})
        run_dir = self.tmp_root / "yomi_furigana"

        summary = run_prompt_experiment(
            task_config_path="config/llm/yomi_triage.toml",
            eval_jsonl_path=str(eval_path),
            run_dir=str(run_dir),
            rendered_yomi_display="furigana_no_space",
            backend=backend,
        )

        self.assertEqual(summary["effective_config"]["rendered_yomi_display"], "furigana_no_space")
        items = [json.loads(line) for line in (run_dir / "items.jsonl").read_text().splitlines()]
        self.assertEqual(items[0]["metadata"]["rendered_prompt"], "大学（だいがく）です。")
        self.assertIn("大学（だいがく）です。", items[0]["prompt"])
