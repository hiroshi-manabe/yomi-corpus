from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.llm_readings import (
    apply_yomi_llm_reading_results_file,
    build_yomi_llm_reading_items,
    build_yomi_llm_reading_queue_file,
)
from yomi_corpus.yomi.ngram_diagnostics import StableTwoKanjiChecker


def unit() -> dict:
    return {
        "unit_id": "u1",
        "text": "学校は上です。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "sudachi": {
                        "tokens": [
                            {
                                "surface": "学校",
                                "pos": "名詞,普通名詞,一般,*,*,*",
                                "dictionary_form": "学校",
                                "normalized_form": "学校",
                                "reading": "ガッコウ",
                            },
                            {
                                "surface": "は",
                                "pos": "助詞,係助詞,*,*,*,*",
                                "dictionary_form": "は",
                                "normalized_form": "は",
                                "reading": "ハ",
                            },
                            {
                                "surface": "上",
                                "pos": "名詞,普通名詞,副詞可能,*,*,*",
                                "dictionary_form": "上",
                                "normalized_form": "上",
                                "reading": "ウエ",
                            },
                            {
                                "surface": "です",
                                "pos": "助動詞,*,*,*,助動詞-ダ,終止形-一般",
                                "dictionary_form": "だ",
                                "normalized_form": "だ",
                                "reading": "デス",
                            },
                            {
                                "surface": "。",
                                "pos": "補助記号,句点,*,*,*,*",
                                "dictionary_form": "。",
                                "normalized_form": "。",
                                "reading": "。",
                            },
                        ]
                    }
                }
            }
        },
    }


class YomiLLMReadingsTests(unittest.TestCase):
    def test_build_items_marks_sudachi_tokens(self) -> None:
        items = build_yomi_llm_reading_items(unit())

        self.assertEqual([item["surface"] for item in items], ["学校", "上"])
        self.assertEqual(items[0]["marked_text"], "**学校**は上（うえ）です。")
        self.assertEqual(items[0]["marked_source_text"], "**学校**は上です。")
        self.assertEqual(items[0]["current_reading_hiragana"], "がっこう")
        self.assertEqual(items[1]["marked_text"], "学校（がっこう）は**上**です。")

    def test_stable_two_kanji_can_be_skipped(self) -> None:
        checker = make_stable_checker(
            "学校,5146,5146,7253,学校,名詞,普通名詞,一般,*,*,*,ガッコウ,学校,*,A,*,*,*,*\n"
        )

        items = build_yomi_llm_reading_items(unit(), stable_checker=checker)

        self.assertEqual(items[0]["surface"], "学校")
        self.assertEqual(items[0]["queue_status"], "skipped")
        self.assertEqual(items[0]["skip_reason"], "stable_two_kanji")
        self.assertEqual(items[1]["surface"], "上")
        self.assertEqual(items[1]["queue_status"], "queued")

    def test_queue_file_writes_only_queued_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            queue_path = root / "queue.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(json.dumps(unit(), ensure_ascii=False) + "\n", encoding="utf-8")

            summary = build_yomi_llm_reading_queue_file(
                input_jsonl=input_path,
                output_jsonl=queue_path,
                summary_json=summary_path,
                skip_stable_two_kanji=False,
            )

            self.assertEqual(summary.queued_items, 2)
            rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["surface"] for row in rows], ["学校", "上"])

    def test_apply_results_compares_hiragana_readings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(json.dumps(unit(), ensure_ascii=False) + "\n", encoding="utf-8")
            items = build_yomi_llm_reading_items(unit())
            queue_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "item_id": items[0]["item_id"],
                                "raw_text": '{"学校":"がっこう"}',
                                "parsed": {"学校": "がっこう"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "item_id": items[1]["item_id"],
                                "raw_text": '{"上":"じょう"}',
                                "parsed": {"上": "じょう"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_llm_reading_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.matched_items, 1)
            self.assertEqual(summary.mismatched_items, 1)
            row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            judgments = row["analysis"]["llm"]["yomi_readings"]["items"]
            self.assertEqual([judgment["status"] for judgment in judgments], ["matched", "mismatched"])


def make_stable_checker(raw_csv: str) -> StableTwoKanjiChecker:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        matrix_dir = root / "matrix"
        matrix_dir.mkdir(parents=True)
        (matrix_dir / "system.dic").write_bytes(b"")
        (root / "core_lex.csv").write_text(raw_csv, encoding="utf-8")
        checker = StableTwoKanjiChecker(
            rows=[],
            decoder_lexicon_path=Path("missing.jsonl"),
            raw_sudachi_dict_dir=root,
        )
        return checker


if __name__ == "__main__":
    unittest.main()
