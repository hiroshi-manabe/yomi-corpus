from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.acceptance import (
    AUTO_ACCEPT_RULE,
    apply_yomi_auto_acceptance_file,
    judge_yomi_auto_accept,
)


def unit(text: str, rendered: str) -> dict:
    return {
        "unit_id": "u1",
        "text": text,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": rendered,
                    "certain": False,
                }
            }
        },
    }


class YomiAcceptanceTests(unittest.TestCase):
    def test_accepts_plain_kana_and_punctuation(self) -> None:
        judgment = judge_yomi_auto_accept(
            unit("ありがとうございます。", "ありがとう/アリガトウ ござい/ゴザイ ます/マス 。/。")
        )
        self.assertTrue(judgment.value)
        self.assertEqual(judgment.rule, AUTO_ACCEPT_RULE)
        self.assertIn("no_kanji", judgment.signals)
        self.assertIn("no_alphabetic", judgment.signals)

    def test_accepts_grouped_numeric_run_with_empty_reading(self) -> None:
        judgment = judge_yomi_auto_accept(unit("2021です。", "2021/ です/デス 。/。"))
        self.assertTrue(judgment.value)

    def test_rejects_kanji(self) -> None:
        judgment = judge_yomi_auto_accept(unit("大学に行く。", "大学/ダイガク に/ニ 行く/イク 。/。"))
        self.assertFalse(judgment.value)
        self.assertIn("contains_kanji", judgment.signals)

    def test_rejects_kanji_iteration_mark(self) -> None:
        judgment = judge_yomi_auto_accept(unit("々です。", "々/ノマ です/デス 。/。"))
        self.assertFalse(judgment.value)
        self.assertIn("contains_kanji", judgment.signals)

    def test_rejects_alphabetic(self) -> None:
        judgment = judge_yomi_auto_accept(unit("OKです。", "OK/オーケー です/デス 。/。"))
        self.assertFalse(judgment.value)
        self.assertIn("contains_alphabetic", judgment.signals)

    def test_rejects_empty_non_numeric_reading(self) -> None:
        judgment = judge_yomi_auto_accept(unit("です。", "です/ 。/。"))
        self.assertFalse(judgment.value)
        self.assertIn("has_unresolved_non_numeric_reading", judgment.signals)

    def test_file_application_writes_judgments_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps(unit("ありがとう。", "ありがとう/アリガトウ 。/。"), ensure_ascii=False),
                        json.dumps(unit("大学です。", "大学/ダイガク です/デス 。/。"), ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_auto_acceptance_file(
                input_jsonl=input_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.rejected, 1)
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(rows[0]["analysis"]["mechanical"]["yomi"]["auto_accept"]["value"])
            self.assertFalse(rows[1]["analysis"]["mechanical"]["yomi"]["auto_accept"]["value"])
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["accepted"], 1)


if __name__ == "__main__":
    unittest.main()
