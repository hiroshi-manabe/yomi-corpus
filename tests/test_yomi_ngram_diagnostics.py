from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from yomi_corpus.yomi.ngram_diagnostics import (
    EntryInfo,
    StableTwoKanjiChecker,
    StableTwoKanjiJudgment,
    analyze_hybrid_stable_two_kanji_row,
    analyze_row,
    load_raw_sudachi_two_kanji_readings,
    override_candidates_for_row,
    split_entries_on_comma,
)


class YomiNgramDiagnosticsTests(unittest.TestCase):
    def test_split_entries_on_japanese_comma_only(self) -> None:
        spans = split_entries_on_comma(
            [
                EntryInfo("前", "マエ", [1, 2]),
                EntryInfo("、", "", [2]),
                EntryInfo("後", "アト", [1, 2]),
                EntryInfo("。", "", [3]),
            ]
        )

        self.assertEqual([[entry.surface for entry in span] for span in spans], [["前"], ["後", "。"]])

    def test_boundary_between_non_exempt_entries_requires_later_order_two(self) -> None:
        diagnostics = analyze_row(
            make_row(
                [
                    {"surface": "家", "reading": "イエ", "piece_orders": [1, 2]},
                    {"surface": "方", "reading": "カタ", "piece_orders": [1]},
                ]
            )
        )

        self.assertEqual(len(diagnostics), 1)
        self.assertFalse(diagnostics[0].passed)
        self.assertEqual(diagnostics[0].failures[0]["current"], "方")

    def test_kana_numeric_and_symbol_adjacent_boundaries_are_exempt(self) -> None:
        diagnostics = analyze_row(
            make_row(
                [
                    {"surface": "かな", "reading": "カナ", "piece_orders": [1]},
                    {"surface": "2", "reading": "", "piece_orders": [1]},
                    {"surface": "！", "reading": "", "piece_orders": [1]},
                    {"surface": "だけ", "reading": "ダケ", "piece_orders": [1]},
                ]
            )
        )

        self.assertEqual(len(diagnostics), 1)
        self.assertTrue(diagnostics[0].passed)
        self.assertEqual(diagnostics[0].checked_entry_count, 0)

    def test_alphabetic_units_are_skipped(self) -> None:
        diagnostics = analyze_row(
            make_row(
                [{"surface": "A", "reading": "エー", "piece_orders": [1]}],
                text="Aです",
            )
        )

        self.assertEqual(diagnostics, [])

    def test_override_without_whitelist_reports_supported_non_whitelisted_change(self) -> None:
        row = make_row(
            [
                {
                    "surface": "家",
                    "reading": "ケ",
                    "final_order": 2,
                    "piece_orders": [2],
                }
            ],
            text="家",
            sudachi_tokens=[
                {
                    "surface": "家",
                    "pos": "名詞",
                    "dictionary_form": "家",
                    "normalized_form": "家",
                    "reading": "イエ",
                }
            ],
            extra_candidates=[
                [
                    {
                        "surface": "家",
                        "reading": "ケ",
                        "final_order": 2,
                        "piece_orders": [2],
                    }
                ]
            ],
        )

        candidates = override_candidates_for_row(row)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["surface"], "家")
        self.assertEqual(candidates[0]["sudachi_reading"], "イエ")
        self.assertEqual(candidates[0]["decoder_reading"], "ケ")
        self.assertEqual(candidates[0]["decoder_reading_votes"], 2)

    def test_stable_two_kanji_does_not_rescue_decoder_only_subpiece(self) -> None:
        row = make_yomi_row(
            text="古本屋さんではありません。",
            rendered="古本屋/フルホンヤ さん/サン で/デ は/ハ あり/アリ ませ/マセ ん/ン 。/。",
            decoder_entries=[
                {"surface": "古", "reading": "コ", "final_order": 2, "piece_orders": [2]},
                {"surface": "本屋", "reading": "ホンヤ", "final_order": 2, "piece_orders": [1, 2]},
                {"surface": "さん", "reading": "サン", "final_order": 4, "piece_orders": [3, 4]},
                {"surface": "で", "reading": "デ", "final_order": 3, "piece_orders": [3]},
                {"surface": "は", "reading": "ハ", "final_order": 4, "piece_orders": [4]},
                {"surface": "あり", "reading": "アリ", "final_order": 4, "piece_orders": [3, 4]},
                {"surface": "ませ", "reading": "マセ", "final_order": 6, "piece_orders": [5, 6]},
                {"surface": "ん", "reading": "ン", "final_order": 6, "piece_orders": [6]},
                {"surface": "。", "reading": "", "final_order": 6, "piece_orders": [6]},
            ],
        )

        result = analyze_hybrid_stable_two_kanji_row(row, stable_checker=FakeStableChecker({"本屋"}))

        self.assertEqual(len(result["spans"]), 1)
        self.assertFalse(result["spans"][0]["relaxed_pass"])
        self.assertEqual(result["spans"][0]["forgiven"], [])

    def test_stable_previous_token_does_not_rescue_following_boundary(self) -> None:
        row = make_yomi_row(
            text="保険入ってないと",
            rendered="保険/ホケン 入っ/ハイッ て/テ ない/ナイ と/ト",
            decoder_entries=[
                {"surface": "保険", "reading": "ホケン", "final_order": 2, "piece_orders": [1, 2]},
                {"surface": "入っ", "reading": "ハイッ", "final_order": 2, "piece_orders": [1, 2]},
                {"surface": "て", "reading": "テ", "final_order": 3, "piece_orders": [3]},
                {"surface": "ない", "reading": "ナイ", "final_order": 4, "piece_orders": [4]},
                {"surface": "と", "reading": "ト", "final_order": 5, "piece_orders": [5]},
            ],
        )

        result = analyze_hybrid_stable_two_kanji_row(row, stable_checker=FakeStableChecker({"保険"}))

        self.assertFalse(result["spans"][0]["relaxed_pass"])
        self.assertEqual(result["spans"][0]["forgiven"], [])
        self.assertEqual(result["spans"][0]["relaxed_failures"][0]["surface"], "入っ")

    def test_stable_current_token_can_rescue_its_own_missing_support(self) -> None:
        row = make_yomi_row(
            text="記事では",
            rendered="記事/キジ で/デ は/ハ",
            decoder_entries=[
                {"surface": "記事", "reading": "キジ", "final_order": 1, "piece_orders": [1]},
                {"surface": "で", "reading": "デ", "final_order": 2, "piece_orders": [2]},
                {"surface": "は", "reading": "ハ", "final_order": 3, "piece_orders": [3]},
            ],
        )

        result = analyze_hybrid_stable_two_kanji_row(row, stable_checker=FakeStableChecker({"記事"}))

        self.assertFalse(result["spans"][0]["baseline_pass"])
        self.assertTrue(result["spans"][0]["relaxed_pass"])
        self.assertEqual(result["spans"][0]["forgiven"][0]["surface"], "記事")

    def test_raw_sudachi_reading_inventory_includes_component_only_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core_lex.csv"
            path.write_text(
                "\n".join(
                    [
                        "大麻,5146,5146,7253,大麻,名詞,普通名詞,一般,*,*,*,タイマ,大麻,*,A,*,*,*,*",
                        "大麻,-1,-1,0,大麻,名詞,固有名詞,地名,一般,*,*,オオアサ,大麻,*,A,*,*,*,*",
                        "群馬,-1,-1,0,群馬,名詞,固有名詞,地名,一般,*,*,グンマ,群馬,*,A,*,*,*,*",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            readings = load_raw_sudachi_two_kanji_readings(Path(tmp))

        self.assertEqual(readings["大麻"], {"タイマ", "オオアサ"})
        self.assertEqual(readings["群馬"], {"グンマ"})

    def test_stable_two_kanji_rejects_raw_sudachi_multi_reading_surface(self) -> None:
        checker = make_stable_checker(
            "大麻,5146,5146,7253,大麻,名詞,普通名詞,一般,*,*,*,タイマ,大麻,*,A,*,*,*,*\n"
            "大麻,-1,-1,0,大麻,名詞,固有名詞,地名,一般,*,*,オオアサ,大麻,*,A,*,*,*,*\n"
        )

        judgment = checker.judge("大麻", "タイマ")

        self.assertFalse(judgment.value)
        self.assertEqual(judgment.reason, "multi_reading_raw_sudachi:オオアサ|タイマ")

    def test_stable_two_kanji_allows_unique_proper_noun_reading(self) -> None:
        checker = make_stable_checker(
            "群馬,-1,-1,0,群馬,名詞,固有名詞,地名,一般,*,*,グンマ,群馬,*,A,*,*,*,*\n"
        )

        judgment = checker.judge("群馬", "グンマ")

        self.assertTrue(judgment.value)
        self.assertEqual(judgment.reason, "stable_two_kanji_unique_raw_sudachi")


class FakeStableChecker:
    def __init__(self, stable_surfaces: set[str]) -> None:
        self.stable_surfaces = stable_surfaces

    def judge(self, surface: str, reading: str) -> StableTwoKanjiJudgment:
        return StableTwoKanjiJudgment(surface in self.stable_surfaces, "fake")


def make_row(
    entries: list[dict],
    text: str = "家方",
    sudachi_tokens: list[dict] | None = None,
    extra_candidates: list[list[dict]] | None = None,
) -> dict:
    if sudachi_tokens is None:
        sudachi_tokens = [
            {
                "surface": "".join(str(entry["surface"]) for entry in entries),
                "pos": "名詞",
                "dictionary_form": "",
                "normalized_form": "",
                "reading": "",
            }
        ]
    candidate_entries = [entries] + list(extra_candidates or [])
    return {
        "unit_id": "u1",
        "text": text,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": "",
                    "sudachi": {
                        "tokens": sudachi_tokens,
                    },
                    "ngram_decoder": {
                        "candidates": [
                            {
                                "rank": index,
                                "score": -1.0,
                                "entries": [
                                    {
                                        "surface": entry["surface"],
                                        "reading": entry["reading"],
                                        "final_order": entry.get("final_order", 1),
                                        "piece_orders": entry["piece_orders"],
                                    }
                                    for entry in candidate
                                ],
                            }
                            for index, candidate in enumerate(candidate_entries, start=1)
                        ]
                    },
                }
            }
        },
    }


def make_yomi_row(*, text: str, rendered: str, decoder_entries: list[dict]) -> dict:
    return {
        "unit_id": "u1",
        "text": text,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": rendered,
                    "ngram_decoder": {
                        "candidates": [
                            {
                                "rank": 1,
                                "score": -1.0,
                                "entries": decoder_entries,
                            }
                        ]
                    },
                }
            }
        },
    }


def make_stable_checker(raw_csv: str) -> StableTwoKanjiChecker:
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name)
    (path / "core_lex.csv").write_text(raw_csv, encoding="utf-8")
    checker = StableTwoKanjiChecker(
        rows=[],
        decoder_lexicon_path=Path("missing.jsonl"),
        raw_sudachi_dict_dir=path,
    )
    checker._tmpdir = tmp  # type: ignore[attr-defined]
    return checker


if __name__ == "__main__":
    unittest.main()
