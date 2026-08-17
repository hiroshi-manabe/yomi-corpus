from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.experiments import run_prompt_experiment
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

    def test_run_prompt_experiment_accepts_valid_yomi_reading_variant(self) -> None:
        eval_path = self.tmp_root / "yomi_reading_variant.jsonl"
        eval_path.write_text(
            json.dumps(
                {
                    "item_id": "iku_001",
                    "surface": "行",
                    "expected_reading": "い",
                    "acceptable_readings": ["い", "ゆ"],
                    "marked_text": "先端を**行**く",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        backend = FakeExperimentBackend(
            {"iku_001": {"parsed": {"行": "ゆ"}, "usage": None}}
        )

        summary = run_prompt_experiment(
            task_config_path="config/llm/yomi_reading.toml",
            eval_jsonl_path=str(eval_path),
            run_dir=str(self.tmp_root / "yomi_reading_variant"),
            backend=backend,
        )

        self.assertEqual(summary["score"]["pass_count"], 1)
