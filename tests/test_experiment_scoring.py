from __future__ import annotations

import unittest

from yomi_corpus.llm.experiment_scoring import score_output


class ExperimentScoringTest(unittest.TestCase):
    def test_yomi_repair_scores_expected_segments_and_normalizes_kana(self) -> None:
        score = score_output(
            task_name="yomi_repair",
            eval_row={
                "expected_segments": [
                    {"surface": "池尻", "reading": "いけじり"},
                    {"surface": "中学校", "reading": "ちゅうがっこう"},
                ]
            },
            parsed=[
                {"surface": "池尻", "reading": "イケジリ", "used_web_search": False},
                {"surface": "中学校", "reading": "チュウガッコウ", "used_web_search": True},
            ],
        )

        self.assertTrue(score["passed"])
        self.assertEqual(score["notes"], [])

    def test_yomi_repair_requires_an_expected_result(self) -> None:
        score = score_output(
            task_name="yomi_repair",
            eval_row={},
            parsed=[{"surface": "真光元", "reading": "しんこうげん"}],
        )

        self.assertFalse(score["passed"])
        self.assertEqual(score["notes"], ["missing_expected_repair"])

    def test_yomi_repair_detects_segmentation_mismatch(self) -> None:
        score = score_output(
            task_name="yomi_repair",
            eval_row={
                "expected_segments": [
                    {"surface": "池尻", "reading": "いけじり"},
                    {"surface": "中学校", "reading": "ちゅうがっこう"},
                ]
            },
            parsed=[{"surface": "池尻中学校", "reading": "いけじりちゅうがっこう"}],
        )

        self.assertFalse(score["passed"])


if __name__ == "__main__":
    unittest.main()
