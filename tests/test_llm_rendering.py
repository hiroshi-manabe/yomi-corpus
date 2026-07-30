from __future__ import annotations

import unittest

from yomi_corpus.llm.rendering import (
    escape_source_parentheses,
    furigana_no_space_rendered_for_llm,
    is_fused_digit_yomi_token,
    rendered_for_llm,
    rendered_tokens,
    restore_source_whitespace_tokens,
)


class LLMRenderingTests(unittest.TestCase):
    def test_escape_source_parentheses_distinguishes_width(self) -> None:
        self.assertEqual(
            escape_source_parentheses("（明日）(予定)"),
            "-LRB-明日-RRB--lrb-予定-rrb-",
        )

    def test_furigana_no_space_uses_parenthesized_yomi_and_escapes_source_parentheses(self) -> None:
        rendered = "荷物/ニモツ を/ヲ 送っ/オクッ て/テ 、/、 （/（ 明日/アシタ ）/） 届く/トドク 。/。"
        self.assertEqual(
            furigana_no_space_rendered_for_llm(rendered),
            "荷物（にもつ）を送（おく）って、-LRB-明日（あした）-RRB-届（とど）く。",
        )

    def test_furigana_no_space_supports_supplementary_cjk_characters(self) -> None:
        self.assertEqual(
            furigana_no_space_rendered_for_llm("𠮟られる/シカラレル 𩸽/ホッケ"),
            "𠮟（しか）られる𩸽（ほっけ）",
        )

    def test_rendered_for_llm_supports_furigana_display(self) -> None:
        self.assertEqual(
            rendered_for_llm("大学/ダイガク です/デス 。/。", "furigana_no_space"),
            "大学（だいがく）です。",
        )

    def test_furigana_no_space_marks_fused_digit_yomi_tokens(self) -> None:
        self.assertEqual(
            furigana_no_space_rendered_for_llm("1/ 人/ニン です/デス 。/。"),
            "1人（にん）です。",
        )
        self.assertEqual(
            furigana_no_space_rendered_for_llm("1人/ヒトリ です/デス 。/。"),
            "|1人（ひとり）です。",
        )

    def test_is_fused_digit_yomi_token(self) -> None:
        self.assertTrue(is_fused_digit_yomi_token("1人", "ヒトリ"))
        self.assertTrue(is_fused_digit_yomi_token("２０２１年", "ニセンニジュウイチネン"))
        self.assertFalse(is_fused_digit_yomi_token("2021", ""))
        self.assertFalse(is_fused_digit_yomi_token("2021", "ニセンニジュウイチ"))
        self.assertFalse(is_fused_digit_yomi_token("人", "ヒト"))

    def test_furigana_no_space_suppresses_invalid_kanji_or_latin_readings(self) -> None:
        self.assertEqual(
            furigana_no_space_rendered_for_llm("This/This \u00a0/\u00a0 sentence/sentence です/デス 。/。"),
            "This\u00a0sentenceです。",
        )
        self.assertEqual(
            furigana_no_space_rendered_for_llm("API/API は/ハ 難語/なんご です/デス 。/。"),
            "APIは難語です。",
        )

    def test_furigana_no_space_parenthesizes_latin_katakana_readings(self) -> None:
        self.assertEqual(
            furigana_no_space_rendered_for_llm("OK/オーケー な/ナ API/エーピーアイ です/デス 。/。"),
            "OK（オーケー）なAPI（エーピーアイ）です。",
        )

    def test_rendered_tokens_split_only_on_ascii_token_separators(self) -> None:
        self.assertEqual(
            rendered_tokens("A/エー \u00a0/\u00a0 \u3000/\u3000 B/ビー"),
            ["A/エー", "\u00a0/\u00a0", "\u3000/\u3000", "B/ビー"],
        )

    def test_restore_source_whitespace_tokens_preserves_readings(self) -> None:
        refreshed, warnings = restore_source_whitespace_tokens(
            "A B　C",
            "A/エー B/ビー C/シー",
        )
        self.assertEqual(warnings, [])
        self.assertEqual(refreshed, "A/エー \u00a0/\u00a0 B/ビー \u3000/\u3000 C/シー")

    def test_restore_source_whitespace_tokens_is_idempotent(self) -> None:
        rendered = "A/エー \u00a0/\u00a0 B/ビー \u3000/\u3000 C/シー"
        refreshed, warnings = restore_source_whitespace_tokens("A B　C", rendered)
        self.assertEqual(warnings, [])
        self.assertEqual(refreshed, rendered)

    def test_restore_source_whitespace_tokens_reports_alignment_failure(self) -> None:
        refreshed, warnings = restore_source_whitespace_tokens("AX B", "A/エー B/ビー")
        self.assertEqual(refreshed, "A/エー B/ビー")
        self.assertIn("non-whitespace source gap", warnings[0])

    def test_restore_source_whitespace_tokens_handles_slash_surface(self) -> None:
        refreshed, warnings = restore_source_whitespace_tokens("Q/微信", "Q/キュー /// 微/ビ 信/シン")
        self.assertEqual(warnings, [])
        self.assertEqual(refreshed, "Q/キュー /// 微/ビ 信/シン")


if __name__ == "__main__":
    unittest.main()
