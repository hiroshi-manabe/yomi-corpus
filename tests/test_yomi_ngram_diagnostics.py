from __future__ import annotations

import unittest

from yomi_corpus.yomi.ngram_diagnostics import (
    EntryInfo,
    analyze_row,
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
        self.assertEqual(candidates[0]["winning_votes"], 2)


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


if __name__ == "__main__":
    unittest.main()
