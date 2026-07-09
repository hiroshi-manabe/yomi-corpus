from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yomi_corpus.yomi.adapters import parse_decoder_output, parse_sudachi_output, run_decoder
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.experiments import compare_yomi_experiments
from yomi_corpus.yomi.strategies import (
    apply_strategy,
    available_strategy_names,
    normalize_ascii_spaces_for_yomi,
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

    def test_run_decoder_passes_model_dir(self) -> None:
        config = YomiGenerationConfig(
            sudachi_command="sudachi",
            sudachi_args=(),
            decoder_python="python",
            decoder_script="decode.py",
            decoder_config="config.toml",
            decoder_beam=10,
            decoder_nbest=5,
            default_strategy="aligned_hybrid_v1",
            decoder_model_dir="/tmp/yomi-model",
        )
        with patch("yomi_corpus.yomi.adapters.subprocess.run") as mocked_run:
            mocked_run.return_value = SimpleNamespace(
                stdout=json.dumps({"results": []}),
            )
            run_decoder("方", config)

        command = mocked_run.call_args.args[0]
        self.assertIn("--model-dir", command)
        self.assertEqual(command[command.index("--model-dir") + 1], "/tmp/yomi-model")

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

    def test_render_pairs_from_sudachi_splits_space_spanning_token(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "The Beatles",
                    "名詞,固有名詞,一般,*,*,*",
                    "The Beatles",
                    "The Beatles",
                    "ザビートルズ",
                )
            ]
        )

        self.assertEqual(rendered, "The/ザ \u00a0/\u00a0 Beatles/ビートルズ")

    def test_render_pairs_from_sudachi_infers_one_space_component_residual(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "Led Zeppelin",
                    "名詞,固有名詞,人名,一般,*,*",
                    "レッド・ツェッペリン",
                    "Led Zeppelin",
                    "レッドツェッペリン",
                )
            ]
        )

        self.assertEqual(rendered, "Led/レッド \u00a0/\u00a0 Zeppelin/ツェッペリン")

    def test_render_pairs_from_sudachi_does_not_infer_two_unknown_space_components(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "Qxz Zxq",
                    "名詞,固有名詞,一般,*,*,*",
                    "Qxz Zxq",
                    "Qxz Zxq",
                    "フーバー",
                )
            ]
        )

        self.assertEqual(rendered, "Qxz/ \u00a0/\u00a0 Zxq/")

    def test_aligned_hybrid_uses_contextual_override_for_kata(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="あの方には",
            sudachi_tokens=[
                SudachiToken("あの", "連体詞", "あの", "あの", "アノ"),
                SudachiToken("方", "名詞,普通名詞,一般,*,*,*", "方", "方", "ホウ"),
                SudachiToken("に", "助詞", "に", "に", "ニ"),
                SudachiToken("は", "助詞", "は", "は", "ハ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("あの", "アノ", 2, [1, 2]),
                        DecoderEntry("方", "カタ", 2, [2]),
                        DecoderEntry("に", "ニ", 2, [2]),
                        DecoderEntry("は", "ハ", 3, [3]),
                    ],
                ),
                DecoderCandidate(
                    rank=2,
                    score=-2.0,
                    entries=[
                        DecoderEntry("あの", "アノ", 2, [1, 2]),
                        DecoderEntry("方", "カタ", 2, [2]),
                        DecoderEntry("に", "ニ", 2, [2]),
                        DecoderEntry("は", "ハ", 3, [3]),
                    ],
                ),
                DecoderCandidate(
                    rank=3,
                    score=-3.0,
                    entries=[
                        DecoderEntry("あの", "アノ", 2, [1, 2]),
                        DecoderEntry("方", "ホウ", 2, [2]),
                        DecoderEntry("に", "ニ", 2, [2]),
                        DecoderEntry("は", "ハ", 3, [3]),
                    ],
                ),
            ],
        )
        self.assertIn("use_decoder_contextual_override", result.signals)
        self.assertEqual(result.rendered, "あの/アノ 方/カタ に/ニ は/ハ")

    def test_aligned_hybrid_preserves_sudachi_split_when_decoder_groups_surface(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="なくなった",
            sudachi_tokens=[
                SudachiToken("なくなっ", "動詞", "なくなる", "なくなる", "ナクナッ"),
                SudachiToken("た", "助動詞", "た", "た", "タ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry(
                            "なくなった",
                            "ナクナッタ",
                            5,
                            [2, 3, 4, 5],
                        )
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "なくなっ/ナクナッ た/タ")
        self.assertIn("fallback_sudachi_token", result.signals)

    def test_aligned_hybrid_preserves_whitespace_and_normalizes_punctuation(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="A\u00a0B？",
            sudachi_tokens=[
                SudachiToken("A", "名詞", "A", "A", "エー"),
                SudachiToken("\u00a0", "空白,*,*,*,*,*", "\u00a0", "\u00a0", "キゴウ"),
                SudachiToken("B", "名詞", "B", "B", "ビー"),
                SudachiToken("？", "補助記号,句点,*,*,*,*", "？", "？", "?"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("A", "エー", 1, [1]),
                        DecoderEntry("B", "ビー", 1, [1]),
                        DecoderEntry("？", "", 1, [1]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "A/エー \u00a0/\u00a0 B/ビー ？/？")
        self.assertIn("preserve_whitespace_token", result.signals)
        self.assertIn("normalize_punctuation_surface", result.signals)

    def test_aligned_hybrid_groups_numeric_runs_without_reading(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="2021年",
            sudachi_tokens=[
                SudachiToken("2", "名詞,数詞,*,*,*,*", "2", "2", "ニ"),
                SudachiToken("0", "名詞,数詞,*,*,*,*", "0", "0", "レイ"),
                SudachiToken("2", "名詞,数詞,*,*,*,*", "2", "2", "ニ"),
                SudachiToken("1", "名詞,数詞,*,*,*,*", "1", "1", "イチ"),
                SudachiToken("年", "名詞,普通名詞,助数詞可能,*,*,*", "年", "年", "ネン"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("2", "ニ", 1, [1]),
                        DecoderEntry("0", "レイ", 1, [1]),
                        DecoderEntry("2", "ニ", 1, [1]),
                        DecoderEntry("1", "イチ", 1, [1]),
                        DecoderEntry("年", "ネン", 2, [2]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "2021/ 年/ネン")
        self.assertIn("group_numeric_run", result.signals)

    def test_aligned_hybrid_refines_single_compound_only_when_reading_is_preserved(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="古本屋さん",
            sudachi_tokens=[
                SudachiToken("古本屋", "名詞,普通名詞,一般,*,*,*", "古本屋", "古本屋", "フルホンヤ"),
                SudachiToken("さん", "接尾辞,名詞的,一般,*,*,*", "さん", "さん", "サン"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("古", "コ", 1, [1]),
                        DecoderEntry("本屋", "ホンヤ", 1, [1, 2]),
                        DecoderEntry("さん", "サン", 2, [2]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "古本屋/フルホンヤ さん/サン")
        self.assertNotIn("refine_single_sudachi_compound_with_decoder", result.signals)

    def test_aligned_hybrid_refines_single_compound_when_only_segmentation_changes(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="静岡県立大学",
            sudachi_tokens=[
                SudachiToken("静岡県立大学", "名詞,普通名詞,一般,*,*,*", "静岡県立大学", "静岡県立大学", "シズオカケンリツダイガク"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("静岡", "シズオカ", 2, [1, 2]),
                        DecoderEntry("県立", "ケンリツ", 4, [3, 4]),
                        DecoderEntry("大学", "ダイガク", 3, [2, 3]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "静岡/シズオカ 県立/ケンリツ 大学/ダイガク")
        self.assertIn("refine_single_sudachi_compound_with_decoder", result.signals)

    def test_aligned_hybrid_does_not_refine_when_decoder_support_is_only_unigram_fallback(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="ダイソー",
            sudachi_tokens=[
                SudachiToken("ダイソー", "名詞,普通名詞,一般,*,*,*", "ダイソー", "ダイソー", "ダイソー"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("ダイ", "ダイ", 2, [1, 2]),
                        DecoderEntry("ソー", "ソー", 1, [1]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "ダイソー/ダイソー")
        self.assertNotIn("refine_single_sudachi_compound_with_decoder", result.signals)

    def test_aligned_hybrid_does_not_refine_without_cross_boundary_support(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="ガンビア",
            sudachi_tokens=[
                SudachiToken("ガンビア", "名詞,固有名詞,地名,国,*,*", "ガンビア", "ガンビア", "ガンビア"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("ガン", "ガン", 3, [2, 3]),
                        DecoderEntry("ビア", "ビア", 2, [1, 2]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "ガンビア/ガンビア")
        self.assertNotIn("refine_single_sudachi_compound_with_decoder", result.signals)

    def test_aligned_hybrid_refines_when_split_boundaries_have_ngram_support(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="マジシャン",
            sudachi_tokens=[
                SudachiToken("マジシャン", "名詞,普通名詞,一般,*,*,*", "マジシャン", "マジシャン", "マジシャン"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("マジ", "マジ", 2, [1, 2]),
                        DecoderEntry("シャン", "シャン", 3, [2, 3]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "マジ/マジ シャン/シャン")
        self.assertIn("refine_single_sudachi_compound_with_decoder", result.signals)

    def test_render_pairs_from_decoder_uses_surface_when_reading_is_empty(self) -> None:
        candidate = DecoderCandidate(
            rank=1,
            score=-1.0,
            entries=[DecoderEntry("。", "", 1, [1])],
        )
        self.assertEqual(render_pairs_from_decoder(candidate), "。/。")

    def test_render_pairs_from_sudachi_preserves_whitespace(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken("不要", "名詞", "不要", "不要", "フヨウ"),
                SudachiToken(" ", "空白,*,*,*,*,*", " ", " ", "キゴウ"),
                SudachiToken("\u3000", "空白,*,*,*,*,*", "\u3000", "\u3000", "キゴウ"),
                SudachiToken("時", "名詞", "時", "時", "トキ"),
            ]
        )
        self.assertEqual(rendered, "不要/フヨウ \u00a0/\u00a0 \u3000/\u3000 時/トキ")

    def test_normalize_ascii_spaces_for_yomi(self) -> None:
        self.assertEqual(normalize_ascii_spaces_for_yomi("A B　C"), "A\u00a0B　C")

    def test_render_pairs_from_sudachi_groups_numeric_runs_without_reading(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken("2", "名詞,数詞,*,*,*,*", "2", "2", "ニ"),
                SudachiToken("0", "名詞,数詞,*,*,*,*", "0", "0", "レイ"),
                SudachiToken("2", "名詞,数詞,*,*,*,*", "2", "2", "ニ"),
                SudachiToken("1", "名詞,数詞,*,*,*,*", "1", "1", "イチ"),
            ]
        )
        self.assertEqual(rendered, "2021/")

    def test_available_strategy_names(self) -> None:
        self.assertIn("aligned_hybrid_v1", available_strategy_names())

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
