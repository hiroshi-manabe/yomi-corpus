from __future__ import annotations

import unittest

from yomi_corpus.yomi.final_review import canonicalize_finalized_unit_yomi, reading_candidates
from yomi_corpus.yomi.llm_readings import build_yomi_llm_reading_items
from yomi_corpus.yomi.numeric_compounds import normalize_numeric_compounds


def token(surface: str, reading: str, pos: str = "名詞,普通名詞,一般,*,*,*") -> dict:
    return {
        "surface": surface,
        "pos": pos,
        "dictionary_form": surface,
        "normalized_form": surface,
        "reading": reading,
    }


class NumericCompoundTests(unittest.TestCase):
    def test_normalizes_deterministic_forms_and_fullwidth_digits(self) -> None:
        result = normalize_numeric_compounds(
            "2/ 日/ニチ 14/ 日/ニチ ２０/ 日/ニチ 1/ 人/ニン 9/ つ/ツ"
        )

        self.assertEqual(
            result.rendered,
            "2日/フツカ 14日/ジュウヨッカ ２０日/ハツカ 1人/ヒトリ 9つ/ココノツ",
        )

    def test_normalizes_existing_fused_form(self) -> None:
        result = normalize_numeric_compounds("24日/ニジュウヨンニチ です/デス")

        self.assertEqual(result.rendered, "24日/ニジュウヨッカ です/デス")

    def test_preserves_supported_tsuitachi_reading(self) -> None:
        self.assertEqual(
            normalize_numeric_compounds("1/ 日/ツイタチ です/デス").rendered,
            "1日/ツイタチ です/デス",
        )

    def test_splits_sudachi_duration_token_after_lexicalized_date(self) -> None:
        self.assertEqual(
            normalize_numeric_compounds("3/ 日間/カカン です/デス").rendered,
            "3日/ミッカ 間/カン です/デス",
        )
        self.assertEqual(
            normalize_numeric_compounds("４日間/ヨッカカン です/デス").rendered,
            "４日/ヨッカ 間/カン です/デス",
        )

    def test_llm_items_use_whole_ichinichi_target_and_keep_later_hybrid_reading(self) -> None:
        unit = {
            "unit_id": "u1",
            "text": "1日は方です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "1日/イチニチ は/ハ 方/ホウ です/デス 。/。",
                        "sudachi": {
                            "tokens": [
                                token("1", "イチ", "名詞,数詞,*,*,*,*"),
                                token("日", "ニチ", "名詞,普通名詞,助数詞可能,*,*,*"),
                                token("は", "ハ", "助詞,係助詞,*,*,*,*"),
                                token("方", "カタ"),
                                token("です", "デス", "助動詞,*,*,*,*,*"),
                                token("。", "。", "補助記号,句点,*,*,*,*"),
                            ]
                        },
                    }
                }
            },
        }

        items = build_yomi_llm_reading_items(unit)

        self.assertEqual([item["surface"] for item in items], ["1日", "方"])
        self.assertEqual(items[0]["marked_text"], "**1日**は方です。")
        self.assertEqual(items[0]["current_reading_hiragana"], "いちにち")
        self.assertEqual(items[1]["current_reading_hiragana"], "ほう")

    def test_deterministic_compound_is_not_an_llm_target(self) -> None:
        unit = {
            "unit_id": "u2",
            "text": "2日です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "2日/フツカ です/デス 。/。",
                        "sudachi": {
                            "tokens": [
                                token("2", "ニ", "名詞,数詞,*,*,*,*"),
                                token("日", "ニチ", "名詞,普通名詞,助数詞可能,*,*,*"),
                                token("です", "デス", "助動詞,*,*,*,*,*"),
                                token("。", "。", "補助記号,句点,*,*,*,*"),
                            ]
                        },
                    }
                }
            },
        }

        self.assertEqual(build_yomi_llm_reading_items(unit), [])

    def test_review_candidates_include_both_ichinichi_readings(self) -> None:
        candidates = reading_candidates(
            {
                "surface": "1日",
                "current_reading_hiragana": "いちにち",
                "signals": [],
            }
        )

        self.assertEqual(
            [candidate["reading"] for candidate in candidates if candidate["reading"]],
            ["いちにち", "ついたち"],
        )

    def test_finalization_expands_ichinichi_but_keeps_tsuitachi_fused(self) -> None:
        ichinichi = {
            "unit_id": "u3",
            "text": "1日",
            "analysis": {"mechanical": {"yomi": {"rendered": "1日/イチニチ"}}},
        }
        tsuitachi = {
            "unit_id": "u4",
            "text": "１日",
            "analysis": {"mechanical": {"yomi": {"rendered": "１日/ツイタチ"}}},
        }

        canonicalize_finalized_unit_yomi(ichinichi)
        canonicalize_finalized_unit_yomi(tsuitachi)

        self.assertEqual(
            ichinichi["analysis"]["mechanical"]["yomi"]["tokens"],
            [["1", ""], ["日", "ニチ"]],
        )
        self.assertEqual(
            tsuitachi["analysis"]["mechanical"]["yomi"]["tokens"],
            [["１日", "ツイタチ"]],
        )


if __name__ == "__main__":
    unittest.main()
