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

    def test_run_prompt_experiment_can_omit_source_text(self) -> None:
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
        prompt_path = self.tmp_root / "prompt.txt"
        prompt_path.write_text("{text_section}Yomi: {rendered}\nAnswer:\n", encoding="utf-8")
        backend = FakeExperimentBackend({"u1": {"parsed": {"status": "OK"}, "usage": None}})
        run_dir = self.tmp_root / "yomi_no_text"

        summary = run_prompt_experiment(
            task_config_path="config/llm/yomi_triage_furigana_no_text.toml",
            eval_jsonl_path=str(eval_path),
            run_dir=str(run_dir),
            prompt_template=str(prompt_path),
            backend=backend,
        )

        self.assertFalse(summary["effective_config"]["include_source_text"])
        items = [json.loads(line) for line in (run_dir / "items.jsonl").read_text().splitlines()]
        self.assertNotIn("Text:", items[0]["prompt"])
        self.assertIn("Yomi: 大学（だいがく）です。", items[0]["prompt"])

    def test_run_prompt_experiment_scores_yomi_reading(self) -> None:
        backend = FakeExperimentBackend(
            {
                "kanji_pain_001": {"parsed": {"痛": "いた"}, "usage": None},
                "kanji_middle_001": {"parsed": {"中": "ちゅう"}, "usage": None},
                "iteration_hibi_001": {"parsed": {"日々": "ひび"}, "usage": None},
                "alphabetic_ok_001": {"parsed": {"OK": "オーケー"}, "usage": None},
                "alphabetic_sns_001": {"parsed": {"SNS": "エスエヌエス"}, "usage": None},
            }
        )
        run_dir = self.tmp_root / "yomi_reading"

        summary = run_prompt_experiment(
            task_config_path="config/llm/yomi_reading.toml",
            eval_jsonl_path="data/evals/yomi_reading/regression_v1.jsonl",
            run_dir=str(run_dir),
            backend=backend,
        )

        self.assertEqual(summary["score"]["pass_count"], 5)
        self.assertEqual(summary["score"]["fail_count"], 0)
        items = [json.loads(line) for line in (run_dir / "items.jsonl").read_text().splitlines()]
        self.assertIn("**日々**", items[2]["prompt"])
        self.assertIn("**OK**", items[3]["prompt"])

    def test_run_prompt_experiment_accepts_extra_yomi_reading_keys(self) -> None:
        eval_path = self.tmp_root / "yomi_reading_extra_keys.jsonl"
        eval_path.write_text(
            json.dumps(
                {
                    "item_id": "fx_001",
                    "surface": "FX",
                    "expected_reading": "えふえっくす",
                    "marked_text": "**FX**取引・CFD取引",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        backend = FakeExperimentBackend(
            {
                "fx_001": {
                    "parsed": {"FX": "エフエックス", "CFD": "シーエフディー"},
                    "usage": None,
                },
            }
        )
        run_dir = self.tmp_root / "yomi_reading_extra_keys"

        summary = run_prompt_experiment(
            task_config_path="config/llm/yomi_reading.toml",
            eval_jsonl_path=str(eval_path),
            run_dir=str(run_dir),
            backend=backend,
        )

        self.assertEqual(summary["score"]["pass_count"], 1)
        scored = [json.loads(line) for line in (run_dir / "scored.jsonl").read_text().splitlines()]
        self.assertEqual(scored[0]["notes"], ["extra_json_keys"])

    def test_run_prompt_experiment_supports_background_mode(self) -> None:
        eval_path = self.tmp_root / "background_eval.jsonl"
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
        run_dir = self.tmp_root / "background_run"

        class FakeBackgroundBackend:
            def __init__(self, **kwargs: object) -> None:
                pass

            def submit_background_item(self, task_config: object, item: object) -> dict[str, object]:
                return {"response_id": f"resp_{item.item_id}", "status": "queued"}

            def retrieve_response(self, response_id: str) -> dict[str, object]:
                return {
                    "response_id": response_id,
                    "status": "completed",
                    "raw_text": "OK",
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }

        from unittest.mock import patch

        with patch("yomi_corpus.llm.runner.OpenAIResponsesBackend", FakeBackgroundBackend):
            summary = run_prompt_experiment(
                task_config_path="config/llm/yomi_triage.toml",
                eval_jsonl_path=str(eval_path),
                run_dir=str(run_dir),
                execution_mode="background",
            )

        self.assertEqual(summary["llm_execution_mode"], "background")
        self.assertEqual(summary["llm_job"]["mode"], "background")
        self.assertEqual(summary["score"]["pass_count"], 1)
        self.assertTrue((run_dir / "llm_job" / "responses.jsonl").exists())
        self.assertTrue((run_dir / "results.raw.jsonl").exists())
