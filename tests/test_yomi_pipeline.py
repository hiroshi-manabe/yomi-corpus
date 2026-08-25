from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yomi_corpus.yomi.adapters import (
    parse_decoder_output,
    parse_sudachi_documents,
    parse_sudachi_output,
    run_decoder,
    run_sudachi,
)
from yomi_corpus.yomi.post_sudachi import (
    UPPERCASE_LATIN_LETTER_READINGS,
    normalize_sudachi_token_reading,
)
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
from yomi_corpus.yomi.source_mapping import SourceSurfaceMappingError, SourceTextMapping
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


class YomiPipelineTests(unittest.TestCase):
    def test_parse_sudachi_output(self) -> None:
        tokens = parse_sudachi_output(
            "方\t名詞,普通名詞,一般,*,*,*\t方\t方\tホウ\t0\t[]\nEOS\n"
        )
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].surface, "方")
        self.assertEqual(tokens[0].reading, "ホウ")

    def test_parse_sudachi_documents_preserves_eos_boundaries(self) -> None:
        documents = parse_sudachi_documents(
            "BGM\t名詞,普通名詞,一般,*,*,*\tBGM\tBGM\tビージーエム\n"
            "EOS\n"
            "ZE:A\t名詞,固有名詞,一般,*,*,*\tZE:A\tZE:A\tゼア\n"
            "EOS\n"
        )
        self.assertEqual(
            [[token.surface for token in row] for row in documents],
            [["BGM"], ["ZE:A"]],
        )

    def test_parse_sudachi_preserves_raw_uppercase_unit_collisions(self) -> None:
        documents = parse_sudachi_documents(
            "A\t名詞,普通名詞,助数詞可能,*,*,*\ta\ta\tアール\n"
            "EOS\n"
            "Ａ\t名詞,普通名詞,助数詞可能,*,*,*\ta\ta\tアール\n"
            "EOS\n"
            "a\t名詞,普通名詞,助数詞可能,*,*,*\ta\ta\tアール\n"
            "EOS\n"
            "ａ\t名詞,普通名詞,助数詞可能,*,*,*\ta\ta\tアール\n"
            "EOS\n"
            "M\t名詞,普通名詞,助数詞可能,*,*,*\tm\tm\tメートル\n"
            "EOS\n"
            "Ｍ\t名詞,普通名詞,助数詞可能,*,*,*\tm\tm\tメートル\n"
            "EOS\n"
            "m\t名詞,普通名詞,助数詞可能,*,*,*\tm\tm\tメートル\n"
            "EOS\n"
            "ｍ\t名詞,普通名詞,助数詞可能,*,*,*\tm\tm\tメートル\n"
            "EOS\n"
        )

        self.assertEqual(
            [[(token.surface, token.reading) for token in row] for row in documents],
            [
                [("A", "アール")],
                [("Ａ", "アール")],
                [("a", "アール")],
                [("ａ", "アール")],
                [("M", "メートル")],
                [("Ｍ", "メートル")],
                [("m", "メートル")],
                [("ｍ", "メートル")],
            ],
        )

    def test_all_standalone_uppercase_letters_default_to_letter_names(self) -> None:
        self.assertEqual(set(UPPERCASE_LATIN_LETTER_READINGS), set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        for surface, expected in UPPERCASE_LATIN_LETTER_READINGS.items():
            for variant in (surface, chr(ord(surface) + 0xFEE0)):
                with self.subTest(surface=variant):
                    token = normalize_sudachi_token_reading(
                        SudachiToken(
                            variant,
                            "名詞,普通名詞,助数詞可能,*,*,*",
                            surface.lower(),
                            surface.lower(),
                            "トン",
                        )
                    )
                    self.assertEqual(token.reading, expected)

    def test_uppercase_letter_rule_does_not_override_lowercase_or_runs(self) -> None:
        for surface, reading in (("t", "トン"), ("ＴＶ", "テレビ"), ("JR", "ジェイアール")):
            token = SudachiToken(surface, "名詞,普通名詞,一般,*,*,*", surface, surface, reading)
            self.assertEqual(normalize_sudachi_token_reading(token), token)

    def test_parse_sudachi_documents_collapses_empty_compatibility_expansion(self) -> None:
        documents = parse_sudachi_documents(
            "⑴\t補助記号,括弧開,*,*,*,*\t(\t(\tキゴウ\n"
            "\t名詞,数詞,*,*,*,*\t1\t1\tイチ\n"
            "\t補助記号,括弧閉,*,*,*,*\t)\t)\tキゴウ\n"
            "EOS\n"
        )

        self.assertEqual(len(documents[0]), 1)
        self.assertEqual(documents[0][0].surface, "⑴")
        self.assertEqual(documents[0][0].reading, "イチ")
        self.assertTrue(documents[0][0].pos.startswith("名詞,数詞,"))

    def test_parse_sudachi_documents_attaches_variation_selector_to_previous_token(self) -> None:
        documents = parse_sudachi_documents(
            "禰\t名詞,固有名詞,人名,名,*,*\t禰\t禰\tネ\n"
            "󠄀\t補助記号,一般,*,*,*,*\t󠄀\t󠄀\t󠄀\n"
            "豆子\t名詞,固有名詞,人名,名,*,*\t豆子\t豆子\tズシ\n"
            "EOS\n"
        )

        self.assertEqual(
            [(token.surface, token.reading) for token in documents[0]],
            [("禰󠄀", "ネ"), ("豆子", "ズシ")],
        )

    def test_parse_sudachi_documents_rejects_unknown_empty_surface(self) -> None:
        with self.assertRaises(SourceSurfaceMappingError):
            parse_sudachi_documents(
                "語\t名詞,普通名詞,一般,*,*,*\t語\t語\tゴ\n"
                "\t名詞,普通名詞,一般,*,*,*\t別\t別\tベツ\n"
                "EOS\n"
            )

    def test_source_text_mapping_restores_original_whitespace(self) -> None:
        mapping = SourceTextMapping(
            source_text="A B\u3000C\u00a0D",
            analysis_text="A\u00a0B\u3000C\u00a0D",
        )

        self.assertEqual(
            mapping.restore_partition(
                ["A", "\u00a0", "B", "\u3000", "C", "\u00a0", "D"],
                stage="test",
            ),
            ["A", " ", "B", "\u3000", "C", "\u00a0", "D"],
        )

    def test_source_text_mapping_rejects_non_space_normalization(self) -> None:
        with self.assertRaises(SourceSurfaceMappingError):
            SourceTextMapping(source_text="⑴", analysis_text="(1)")

    def test_run_sudachi_restores_source_space(self) -> None:
        config = YomiGenerationConfig(
            sudachi_command="sudachi",
            sudachi_args=(),
            decoder_python="python",
            decoder_script="decode.py",
            decoder_config="config.toml",
            decoder_beam=10,
            decoder_nbest=5,
            default_strategy="aligned_hybrid_v1",
        )
        with patch("yomi_corpus.yomi.adapters.subprocess.run") as mocked_run:
            mocked_run.return_value = SimpleNamespace(
                stdout=(
                    "A\t名詞\tA\tA\tエー\n"
                    "\u00a0\t空白\t\u00a0\t\u00a0\tキゴウ\n"
                    "B\t名詞\tB\tB\tビー\nEOS\n"
                )
            )
            tokens = run_sudachi("A\u00a0B", config, source_text="A B")

        self.assertEqual([token.surface for token in tokens], ["A", " ", "B"])

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

    def test_parse_decoder_output_attaches_variation_selector_to_previous_entry(self) -> None:
        candidates = parse_decoder_output(
            json.dumps(
                {
                    "results": [
                        {
                            "rank": 1,
                            "score": -1.0,
                            "entries": [
                                {"surface": "禰", "reading": "ネ", "final_order": 1},
                                {"surface": "󠄀", "reading": "", "final_order": 1},
                                {"surface": "豆子", "reading": "ズシ", "final_order": 1},
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(
            [(entry.surface, entry.reading) for entry in candidates[0].entries],
            [("禰󠄀", "ネ"), ("豆子", "ズシ")],
        )

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
        self.assertIn("--text=方", command)
        self.assertNotIn("--text", command)
        self.assertIn("--model-dir", command)
        self.assertEqual(command[command.index("--model-dir") + 1], "/tmp/yomi-model")

    def test_run_decoder_passes_leading_hyphen_text_as_option_value(self) -> None:
        config = YomiGenerationConfig(
            sudachi_command="sudachi",
            sudachi_args=(),
            decoder_python="python",
            decoder_script="decode.py",
            decoder_config="config.toml",
            decoder_beam=10,
            decoder_nbest=5,
            default_strategy="aligned_hybrid_v1",
        )
        with patch("yomi_corpus.yomi.adapters.subprocess.run") as mocked_run:
            mocked_run.return_value = SimpleNamespace(stdout=json.dumps({"results": []}))
            run_decoder("-\u00a0本文", config)

        command = mocked_run.call_args.args[0]
        self.assertIn("--text=-\u00a0本文", command)

    def test_run_decoder_restores_source_space(self) -> None:
        config = YomiGenerationConfig(
            sudachi_command="sudachi",
            sudachi_args=(),
            decoder_python="python",
            decoder_script="decode.py",
            decoder_config="config.toml",
            decoder_beam=10,
            decoder_nbest=5,
            default_strategy="aligned_hybrid_v1",
        )
        with patch("yomi_corpus.yomi.adapters.subprocess.run") as mocked_run:
            mocked_run.return_value = SimpleNamespace(
                stdout=json.dumps(
                    {
                        "results": [
                            {
                                "rank": 1,
                                "score": -1.0,
                                "entries": [
                                    {"surface": "A", "reading": "エー"},
                                    {"surface": "\u00a0", "reading": ""},
                                    {"surface": "B", "reading": "ビー"},
                                ],
                            }
                        ]
                    }
                )
            )
            candidates = run_decoder("A\u00a0B", config, source_text="A B")

        self.assertEqual(
            [entry.surface for entry in candidates[0].entries],
            ["A", " ", "B"],
        )

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

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_render_pairs_from_sudachi_splits_internal_middle_dot(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {
            "小": "ショウ",
            "中学": "チュウガク",
        }.get

        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "小・中学",
                    "名詞,普通名詞,一般,*,*,*",
                    "小中学",
                    "小・中学",
                    "ショウチュウガク",
                )
            ]
        )

        self.assertEqual(rendered, "小/ショウ ・/・ 中学/チュウガク")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_render_pairs_from_sudachi_localizes_failed_middle_dot_lookup(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {"小": "ショウ", "未知語": ""}.get

        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "小・未知語",
                    "名詞,普通名詞,一般,*,*,*",
                    "小未知語",
                    "小・未知語",
                    "ショウミチゴ",
                )
            ]
        )

        self.assertEqual(rendered, "小/ショウ ・/・ 未知語/")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_render_pairs_from_sudachi_splits_middle_dot_in_proper_name(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {
            "サントメ": "サントメ",
            "プリンシペ": "プリンシペ",
        }.get
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "サントメ・プリンシペ",
                    "名詞,固有名詞,地名,国,*,*",
                    "サントメプリンシペ",
                    "サントメ・プリンシペ",
                    "サントメプリンシペ",
                )
            ]
        )

        self.assertEqual(rendered, "サントメ/サントメ ・/・ プリンシペ/プリンシペ")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_render_pairs_from_sudachi_splits_kana_middle_dot_name(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {
            "ラ": "ラ",
            "カンパネラ": "カンパネラ",
        }.get

        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "ラ・カンパネラ",
                    "名詞,固有名詞,一般,*,*,*",
                    "ラ・カンパネラ",
                    "ラ・カンパネラ",
                    "ラカンパネラ",
                )
            ]
        )

        self.assertEqual(rendered, "ラ/ラ ・/・ カンパネラ/カンパネラ")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_render_pairs_from_sudachi_splits_nonproper_parentheses(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {
            "男": "ダン",
            "女": "オンナ",
            "性": "セイ",
        }.get

        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "男（女）性",
                    "名詞,普通名詞,一般,*,*,*",
                    "男女性",
                    "男(女)性",
                    "ダンジョセイ",
                )
            ]
        )

        self.assertEqual(rendered, "男/ダン （/（ 女/ジョ ）/） 性/セイ")

    def test_render_pairs_from_sudachi_uses_short_semantic_parenthetical_reading(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "（社）",
                    "補助記号,一般,*,*,*,*",
                    "(社)",
                    "(社)",
                    "シャダンホウジン",
                )
            ]
        )

        self.assertEqual(rendered, "（/（ 社/シャ ）/）")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_render_pairs_from_sudachi_splits_parentheses_in_proper_name(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {"広": "ヒロ", "島": "シマ"}.get
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "広（島）",
                    "名詞,固有名詞,地名,一般,*,*",
                    "広島",
                    "広(島)",
                    "ヒロシマ",
                )
            ]
        )

        self.assertEqual(rendered, "広/ヒロ （/（ 島/シマ ）/）")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_parenthesis_split_does_not_use_incompatible_standalone_readings(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {
            "男": "オトコ",
            "女": "オンナ",
            "性": "セイ",
        }.get

        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "男（女）性",
                    "名詞,普通名詞,一般,*,*,*",
                    "男女性",
                    "男(女)性",
                    "ダンジョセイ",
                )
            ]
        )

        self.assertEqual(rendered, "男/ （/（ 女/ ）/） 性/")

    def test_render_pairs_from_sudachi_normalizes_attached_wave_reading(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken("な〜", "助詞,終助詞,*,*,*,*", "な", "な", "ナ"),
                SudachiToken("う～ん", "感動詞,一般,*,*,*,*", "うん", "うん", "ウウン"),
            ]
        )

        self.assertEqual(rendered, "な〜/ナー う～ん/ウーン")

    def test_render_pairs_from_sudachi_does_not_read_standalone_wave_as_long_vowel(self) -> None:
        rendered = render_pairs_from_sudachi(
            [SudachiToken("〜", "補助記号,一般,*,*,*,*", "〜", "〜", "キゴウ")]
        )

        self.assertEqual(rendered, "〜/〜")

    def test_render_pairs_from_sudachi_preserves_symbol_reading_in_proper_name(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "う～み",
                    "名詞,固有名詞,人名,一般,*,*",
                    "う～み",
                    "う～み",
                    "ウーミ",
                )
            ]
        )

        self.assertEqual(rendered, "う～み/ウーミ")

    @patch("yomi_corpus.yomi.strategies.lookup_component_reading")
    def test_aligned_hybrid_splits_internal_middle_dot_before_decoder_selection(
        self, lookup_component_reading
    ) -> None:
        lookup_component_reading.side_effect = {
            "小": "ショウ",
            "中学": "チュウガク",
        }.get

        result = apply_strategy(
            "aligned_hybrid_v1",
            text="小・中学",
            sudachi_tokens=[
                SudachiToken(
                    "小・中学",
                    "名詞,普通名詞,一般,*,*,*",
                    "小中学",
                    "小・中学",
                    "ショウチュウガク",
                )
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("小・中学", "ショウチュウガク", 2, [2])],
                )
            ],
        )

        self.assertEqual(result.rendered, "小/ショウ ・/・ 中学/チュウガク")
        self.assertIn("split_middle_dot_spanning_sudachi_token", result.signals)

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

    def test_ngram_boundary_preferred_combines_supported_decoder_span(self) -> None:
        result = apply_strategy(
            "ngram_boundary_preferred_v1",
            text="太平洋戦争終戦",
            sudachi_tokens=[
                SudachiToken("太平洋", "名詞,固有名詞,地名,一般,*,*", "太平洋", "太平洋", "タイヘイヨウ"),
                SudachiToken("戦", "接尾辞,名詞的,一般,*,*,*", "戦", "戦", "セン"),
                SudachiToken("争", "補助記号,一般,*,*,*,*", "争", "争", "争"),
                SudachiToken("終戦", "名詞,普通名詞,一般,*,*,*", "終戦", "終戦", "シュウセン"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("太平洋", "タイヘイヨウ", 3, [1, 2, 3]),
                        DecoderEntry("戦争", "センソウ", 5, [4, 5]),
                        DecoderEntry("終戦", "シュウセン", 2, [1, 2]),
                    ],
                )
            ],
        )

        self.assertEqual(result.rendered, "太平洋/タイヘイヨウ 戦争/センソウ 終戦/シュウセン")
        self.assertIn("prefer_supported_decoder_grouping", result.signals)

    def test_ngram_grouping_preferred_combines_supported_decoder_span(self) -> None:
        result = apply_strategy(
            "ngram_grouping_preferred_v1",
            text="戦争",
            sudachi_tokens=[
                SudachiToken("戦", "接尾辞,名詞的,一般,*,*,*", "戦", "戦", "セン"),
                SudachiToken("争", "補助記号,一般,*,*,*,*", "争", "争", "争"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("戦争", "センソウ", 2, [2, 2])],
                )
            ],
        )

        self.assertEqual(result.rendered, "戦争/センソウ")
        self.assertIn("prefer_supported_decoder_grouping", result.signals)

    def test_ngram_grouping_preferred_requires_support_for_every_piece(self) -> None:
        result = apply_strategy(
            "ngram_grouping_preferred_v1",
            text="楽して",
            sudachi_tokens=[
                SudachiToken("楽", "名詞,普通名詞,一般,*,*,*", "楽", "楽", "ラク"),
                SudachiToken("し", "動詞,非自立可能,*,*,サ行変格,連用形-一般", "する", "する", "シ"),
                SudachiToken("て", "助詞,接続助詞,*,*,*,*", "て", "て", "テ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("楽し", "タノシ", 2, [1, 2]),
                        DecoderEntry("て", "テ", 3, [3]),
                    ],
                )
            ],
        )

        self.assertEqual(result.rendered, "楽/ラク し/シ て/テ")
        self.assertNotIn("prefer_supported_decoder_grouping", result.signals)

    def test_ngram_grouping_preferred_does_not_partition_sudachi_token(self) -> None:
        result = apply_strategy(
            "ngram_grouping_preferred_v1",
            text="古本屋",
            sudachi_tokens=[
                SudachiToken("古本屋", "名詞,普通名詞,一般,*,*,*", "古本屋", "古本屋", "フルホンヤ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("古", "コ", 2, [2, 2]),
                        DecoderEntry("本屋", "ホンヤ", 2, [1, 2]),
                    ],
                )
            ],
        )

        self.assertEqual(result.rendered, "古本屋/フルホンヤ")
        self.assertNotIn("prefer_supported_decoder_partition", result.signals)

    def test_ngram_boundary_preferred_does_not_group_unigram_decoder_span(self) -> None:
        result = apply_strategy(
            "ngram_boundary_preferred_v1",
            text="戦争",
            sudachi_tokens=[
                SudachiToken("戦", "接尾辞,名詞的,一般,*,*,*", "戦", "戦", "セン"),
                SudachiToken("争", "補助記号,一般,*,*,*,*", "争", "争", "争"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("戦争", "センソウ", 1, [1])],
                )
            ],
        )

        self.assertEqual(result.rendered, "戦/セン 争/争")
        self.assertNotIn("prefer_supported_decoder_grouping", result.signals)

    def test_ngram_boundary_preferred_partitions_supported_decoder_entries(self) -> None:
        result = apply_strategy(
            "ngram_boundary_preferred_v1",
            text="古本屋",
            sudachi_tokens=[
                SudachiToken("古本屋", "名詞,普通名詞,一般,*,*,*", "古本屋", "古本屋", "フルホンヤ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("古", "コ", 2, [2, 2]),
                        DecoderEntry("本屋", "ホンヤ", 3, [2, 3]),
                    ],
                )
            ],
        )

        self.assertEqual(result.rendered, "古/コ 本屋/ホンヤ")
        self.assertIn("prefer_supported_decoder_partition", result.signals)

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

    def test_aligned_hybrid_groups_japanese_numeral_runs_without_reading(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="二〇〇二年",
            sudachi_tokens=[
                SudachiToken("二", "名詞,数詞,*,*,*,*", "二", "二", "ニ"),
                SudachiToken("〇", "名詞,数詞,*,*,*,*", "〇", "〇", "レイ"),
                SudachiToken("〇", "名詞,数詞,*,*,*,*", "〇", "〇", "レイ"),
                SudachiToken("二", "名詞,数詞,*,*,*,*", "二", "二", "ニ"),
                SudachiToken("年", "名詞,普通名詞,助数詞可能,*,*,*", "年", "年", "ネン"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("二", "ニ", 1, [1]),
                        DecoderEntry("〇", "レイ", 1, [1]),
                        DecoderEntry("〇", "レイ", 1, [1]),
                        DecoderEntry("二", "ニ", 1, [1]),
                        DecoderEntry("年", "ネン", 2, [2]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "二〇〇二/ 年/ネン")
        self.assertIn("group_numeric_run", result.signals)

    def test_sudachi_render_preserves_proper_name_japanese_numeral_reading(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "一二三",
                    "名詞,固有名詞,人名,名,*,*",
                    "一二三",
                    "一二三",
                    "ヒフミ",
                )
            ]
        )

        self.assertEqual(rendered, "一二三/ヒフミ")

    def test_aligned_hybrid_keeps_single_japanese_numeral_reading(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="七時",
            sudachi_tokens=[
                SudachiToken("七", "名詞,数詞,*,*,*,*", "七", "七", "ナナ"),
                SudachiToken("時", "名詞,普通名詞,助数詞可能,*,*,*", "時", "時", "ジ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("七", "ナナ", 1, [1]),
                        DecoderEntry("時", "ジ", 2, [2]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "七/ナナ 時/ジ")
        self.assertNotIn("group_numeric_run", result.signals)

    def test_aligned_hybrid_splits_mixed_numeric_counter_token(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="2級試験",
            sudachi_tokens=[
                SudachiToken("2級", "名詞,普通名詞,一般,*,*,*", "2級", "2級", "ニキュウ"),
                SudachiToken("試験", "名詞,普通名詞,一般,*,*,*", "試験", "試験", "シケン"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[
                        DecoderEntry("2", "", 1, [1]),
                        DecoderEntry("級", "キュウ", 2, [2]),
                        DecoderEntry("試験", "シケン", 2, [2]),
                    ],
                )
            ],
        )
        self.assertEqual(result.rendered, "2/ 級/キュウ 試験/シケン")
        self.assertIn("split_mixed_arabic_numeric_token", result.signals)

    def test_aligned_hybrid_derives_prefix_reading_before_numeric_suffix(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="中2",
            sudachi_tokens=[
                SudachiToken("中2", "名詞,普通名詞,一般,*,*,*", "中2", "中2", "チュウニ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("中2", "チュウニ", 1, [1])],
                )
            ],
        )
        self.assertEqual(result.rendered, "中/チュウ 2/")

    def test_aligned_hybrid_preserves_contextual_counter_reading(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="3階",
            sudachi_tokens=[
                SudachiToken("3階", "名詞,普通名詞,一般,*,*,*", "3階", "3階", "サンガイ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("3階", "サンガイ", 1, [1])],
                )
            ],
        )
        self.assertEqual(result.rendered, "3/ 階/ガイ")

    def test_aligned_hybrid_keeps_lexicalized_alphanumeric_token(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="2nd",
            sudachi_tokens=[
                SudachiToken("2nd", "名詞,普通名詞,一般,*,*,*", "2nd", "2nd", "セカンド"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("2nd", "セカンド", 1, [1])],
                )
            ],
        )
        self.assertEqual(result.rendered, "2nd/セカンド")

    def test_aligned_hybrid_keeps_explicit_irregular_numeric_compound(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="2人",
            sudachi_tokens=[
                SudachiToken("2人", "名詞,普通名詞,一般,*,*,*", "2人", "2人", "フタリ"),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("2人", "フタリ", 1, [1])],
                )
            ],
        )
        self.assertEqual(result.rendered, "2人/フタリ")

    def test_aligned_hybrid_normalizes_symbolic_sudachi_kaomoji(self) -> None:
        result = apply_strategy(
            "aligned_hybrid_v1",
            text="（●＾o＾●）",
            sudachi_tokens=[
                SudachiToken(
                    "（●＾o＾●）",
                    "補助記号,ＡＡ,顔文字,*,*,*",
                    "(●^o^●)",
                    "(●^o^●)",
                    "キゴウ",
                ),
            ],
            decoder_candidates=[
                DecoderCandidate(
                    rank=1,
                    score=-1.0,
                    entries=[DecoderEntry("（●＾o＾●）", "キゴウ", 2, [2])],
                )
            ],
        )
        self.assertEqual(result.rendered, "（●＾o＾●）/カオモジ")
        self.assertIn("normalize_symbolic_sudachi_kaomoji", result.signals)

    def test_sudachi_render_does_not_treat_parenthesized_japanese_as_kaomoji(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "（笑）",
                    "補助記号,ＡＡ,顔文字,*,*,*",
                    "(笑)",
                    "(笑)",
                    "キゴウ",
                ),
            ]
        )
        self.assertEqual(rendered, "（/（ 笑/ワライ ）/）")

    def test_sudachi_render_normalizes_semantic_emotion_parentheticals(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    surface,
                    "補助記号,ＡＡ,顔文字,*,*,*",
                    surface,
                    surface,
                    "キゴウ",
                )
                for surface in ("（汗）", "(泣)", "（苦笑）")
            ]
        )

        self.assertEqual(
            rendered,
            "（/（ 汗/アセ ）/） (/( 泣/ナキ )/) （/（ 苦笑/ニガワライ ）/）",
        )

    def test_sudachi_render_accepts_japanese_character_inside_symbolic_kaomoji(self) -> None:
        rendered = render_pairs_from_sudachi(
            [
                SudachiToken(
                    "（ノ∀｀）",
                    "補助記号,ＡＡ,顔文字,*,*,*",
                    "(ノ∀`)",
                    "(ノ∀`)",
                    "キゴウ",
                ),
            ]
        )
        self.assertEqual(rendered, "（ノ∀｀）/カオモジ")

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

    @patch("yomi_corpus.yomi.runtime.run_decoder")
    @patch("yomi_corpus.yomi.runtime.run_sudachi")
    def test_generate_mechanical_yomi_stores_exact_readingless_source_whitespace(
        self,
        mocked_sudachi,
        mocked_decoder,
    ) -> None:
        text = "A B\u00a0C\u3000D"
        mocked_sudachi.return_value = [
            SudachiToken("A", "名詞", "A", "A", "エー"),
            SudachiToken(" ", "空白", " ", " ", "キゴウ"),
            SudachiToken("B", "名詞", "B", "B", "ビー"),
            SudachiToken("\u00a0", "空白", "\u00a0", "\u00a0", "キゴウ"),
            SudachiToken("C", "名詞", "C", "C", "シー"),
            SudachiToken("\u3000", "空白", "\u3000", "\u3000", "キゴウ"),
            SudachiToken("D", "名詞", "D", "D", "ディー"),
        ]
        mocked_decoder.return_value = []

        result = generate_mechanical_yomi(
            text,
            config=YomiGenerationConfig(
                sudachi_command="sudachi",
                sudachi_args=(),
                decoder_python="python",
                decoder_script="decode.py",
                decoder_config="config.toml",
                decoder_beam=10,
                decoder_nbest=5,
                default_strategy="sudachi_only_v1",
            ),
        )

        self.assertEqual("".join(surface for surface, _reading in result.tokens), text)
        self.assertEqual(
            [pair for pair in result.tokens if pair[0].isspace()],
            [[" ", ""], ["\u00a0", ""], ["\u3000", ""]],
        )
        self.assertEqual(
            [token["surface"] for token in result.sudachi["raw"]["tokens"]],
            ["A", " ", "B", "\u00a0", "C", "\u3000", "D"],
        )
        self.assertEqual(
            result.sudachi["normalized"]["normalizer_version"],
            2,
        )
        self.assertEqual(
            result.sudachi["tokens"],
            result.sudachi["normalized"]["tokens"],
        )

    @patch("yomi_corpus.yomi.runtime.run_decoder")
    @patch("yomi_corpus.yomi.runtime.run_sudachi")
    def test_generate_mechanical_yomi_keeps_raw_and_normalized_sudachi_separate(
        self,
        mocked_sudachi,
        mocked_decoder,
    ) -> None:
        mocked_sudachi.return_value = [
            SudachiToken("A", "名詞", "a", "a", "アール"),
            SudachiToken("皆", "名詞", "皆", "皆", "ミナ"),
            SudachiToken("様", "名詞", "様", "様", "サマ"),
        ]
        mocked_decoder.return_value = []

        result = generate_mechanical_yomi(
            "A皆様",
            config=YomiGenerationConfig(
                sudachi_command="sudachi",
                sudachi_args=(),
                decoder_python="python",
                decoder_script="decode.py",
                decoder_config="config.toml",
                decoder_beam=10,
                decoder_nbest=5,
                default_strategy="sudachi_only_v1",
            ),
        )

        self.assertEqual(
            [(row["surface"], row["reading"]) for row in result.sudachi["raw"]["tokens"]],
            [("A", "アール"), ("皆", "ミナ"), ("様", "サマ")],
        )
        self.assertEqual(
            [
                (row["surface"], row["reading"])
                for row in result.sudachi["normalized"]["tokens"]
            ],
            [("A", "エー"), ("皆様", "ミナサマ")],
        )
        self.assertEqual(
            [row["rule_id"] for row in result.sudachi["normalized"]["applications"]],
            ["normalize_uppercase_latin_letter_reading", "canonicalize_minasama_boundary"],
        )
        self.assertIn("normalize_uppercase_latin_letter_reading", result.signals)
        self.assertIn("canonicalize_minasama_boundary", result.signals)

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
