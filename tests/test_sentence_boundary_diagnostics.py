from __future__ import annotations

import unittest

from sudachipy import dictionary, tokenizer as sudachi_tokenizer

from yomi_corpus.sentence_boundary_diagnostics import compare_document_boundaries


class SentenceBoundaryDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tokenizer = dictionary.Dictionary(dict="full").create()
        cls.split_mode = sudachi_tokenizer.Tokenizer.SplitMode.C

    def test_sudachi_adds_halfwidth_sentence_boundary(self) -> None:
        comparison = compare_document_boundaries(
            "あります｡次です。",
            tokenizer=self.tokenizer,
            split_mode=self.split_mode,
        )

        self.assertEqual(comparison["current_only"], [])
        self.assertEqual(
            [item["preceding_character"] for item in comparison["sudachi_only"]],
            ["｡"],
        )

    def test_sudachi_suppresses_periods_inside_known_emoticon(self) -> None:
        comparison = compare_document_boundaries(
            "前です。(。・ω・。)後です。",
            tokenizer=self.tokenizer,
            split_mode=self.split_mode,
        )

        self.assertEqual(comparison["sudachi_only"], [])
        self.assertEqual(
            [item["preceding_character"] for item in comparison["current_only"]],
            ["。", "。"],
        )


if __name__ == "__main__":
    unittest.main()
