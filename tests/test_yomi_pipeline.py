from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.adapters import parse_decoder_output, parse_sudachi_output
from yomi_corpus.yomi.experiments import compare_yomi_experiments
from yomi_corpus.yomi.strategies import (
    apply_strategy,
    available_strategy_names,
    render_pairs_from_decoder,
    render_pairs_from_sudachi,
)
from yomi_corpus.yomi.types import DecoderCandidate, DecoderEntry, SudachiToken


class YomiPipelineTests(unittest.TestCase):
    def test_parse_sudachi_output(self) -> None:
        tokens = parse_sudachi_output(
            "方\t名詞,普通名詞,一般,*,*,*\t方\t方\tホウ\t0\t[]\nEOS\n"
        )
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].surface, "方")
        self.assertEqual(tokens[0].reading, "ホウ")

    def test_parse_decoder_output(self) -> None:
        candidates = parse_decoder_output(
            json.dumps(
                {
                    "text": "方",
                    "results": [
                        {
                            "rank": 1,
                            "score": -1.0,
                            "entries": [
                                {
                                    "surface": "方",
                                    "reading": "ホウ",
                                    "final_order": 2,
                                    "piece_orders": [1, 2],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].entries[0].reading, "ホウ")

    def test_agreement_prefer_decoder_marks_exact_agreement_certain(self) -> None:
        result = apply_strategy(
            "agreement_prefer_decoder_v1",
            text="こっちの方がいいです",
            sudachi_tokens=[
                SudachiToken("方", "名詞", "方", "方", "ホウ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("方", "ホウ", 2, [1, 2])],
                )
            ],
        )
        self.assertTrue(result.certain)
        self.assertIn("sudachi_decoder_exact_agreement", result.signals)

    def test_agreement_prefer_decoder_falls_back_on_surface_disagreement(self) -> None:
        result = apply_strategy(
            "agreement_prefer_decoder_v1",
            text="お金",
            sudachi_tokens=[
                SudachiToken("お", "接頭辞", "御", "お", "オ"),
                SudachiToken("金", "名詞", "金", "金", "カネ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("お金", "オカネ", 2, [1, 2])],
                )
            ],
        )
        self.assertEqual(result.rendered, "お/オ 金/カネ")
        self.assertIn("fallback_sudachi", result.signals)

    def test_available_strategy_names(self) -> None:
        self.assertIn("agreement_prefer_decoder_v1", available_strategy_names())

    def test_compare_yomi_experiments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"
            candidate = root / "candidate"
            base.mkdir()
            candidate.mkdir()
            (base / "summary.json").write_text(json.dumps({"exact_match_accuracy": 0.5}), encoding="utf-8")
            (candidate / "summary.json").write_text(json.dumps({"exact_match_accuracy": 1.0}), encoding="utf-8")
            (base / "scored.jsonl").write_text(
                json.dumps({"item_id": "x", "predicted_rendered": "A", "exact_match": False}) + "\n",
                encoding="utf-8",
            )
            (candidate / "scored.jsonl").write_text(
                json.dumps({"item_id": "x", "predicted_rendered": "B", "exact_match": True}) + "\n",
                encoding="utf-8",
            )
            comparison = compare_yomi_experiments(base_run_dir=base, candidate_run_dir=candidate)
            self.assertEqual(len(comparison["changed_items"]), 1)


if __name__ == "__main__":
    unittest.main()
