from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.paths import resolve_repo_path
from yomi_corpus.yomi.triage import (
    apply_yomi_triage_results_file,
    build_yomi_triage_item,
    build_yomi_triage_items,
    build_yomi_triage_queue_file,
    has_unannotated_kanji,
    has_unannotated_kanji_or_latin_token,
    is_katakana_reading,
)


def unit(unit_id: str, text: str, rendered: str, *, accepted: bool) -> dict:
    return {
        "unit_id": unit_id,
        "text": text,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": rendered,
                    "auto_accept": {
                        "value": accepted,
                        "signals": ["test_signal"],
                    },
                }
            }
        },
    }


class YomiTriageTests(unittest.TestCase):
    def test_production_prompt_documents_empty_numeric_readings(self) -> None:
        prompt = resolve_repo_path("config/prompts/yomi_triage_v2.txt").read_text(encoding="utf-8")

        self.assertIn("such as `2021`", prompt)
        self.assertIn("`二〇〇二`", prompt)
        self.assertIn("30分（ぷん）", prompt)
        self.assertIn("number-reading module", prompt)

    def test_build_yomi_triage_item_keeps_minimal_llm_input(self) -> None:
        item = build_yomi_triage_item(
            unit("u1", "大学です。", "大学/ダイガク です/デス 。/。", accepted=False)
        )

        self.assertEqual(item["unit_id"], "u1")
        self.assertEqual(item["text"], "大学です。")
        self.assertEqual(item["rendered"], "大学/ダイガク です/デス 。/。")
        self.assertEqual(item["rendered_prompt"], "大学（だいがく）です。")
        self.assertFalse(item["has_unannotated_kanji"])
        self.assertFalse(item["auto_accept"]["value"])

    def test_comma_span_items_split_text_and_rendered_yomi(self) -> None:
        items = build_yomi_triage_items(
            unit(
                "u1",
                "大学です、行きます。",
                "大学/ダイガク です/デス 、/、 行き/イキ ます/マス 。/。",
                accepted=False,
            ),
            unit_mode="comma_span",
        )

        self.assertEqual([item["unit_id"] for item in items], ["u1:s0001", "u1:s0002"])
        self.assertEqual(items[0]["parent_unit_id"], "u1")
        self.assertEqual(items[0]["text"], "大学です、")
        self.assertEqual(items[0]["rendered"], "大学/ダイガク です/デス 、/、")
        self.assertEqual(items[0]["rendered_prompt"], "大学（だいがく）です、")
        self.assertEqual(items[1]["text"], "行きます。")
        self.assertEqual(items[1]["rendered"], "行き/イキ ます/マス 。/。")
        self.assertEqual(items[1]["rendered_prompt"], "行（い）きます。")

    def test_build_yomi_triage_queue_file_skips_auto_accepted_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "units.yomi.auto_accept.jsonl"
            output_path = root / "yomi_triage_input.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            unit("u1", "大学です。", "大学/ダイガク です/デス 。/。", accepted=True),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            unit("u2", "方です。", "方/ホウ です/デス 。/。", accepted=False),
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_yomi_triage_queue_file(
                input_jsonl=input_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.read, 2)
            self.assertEqual(summary.queued, 1)
            self.assertEqual(summary.skipped_auto_accepted, 1)
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["unit_id"], "u2")

    def test_build_yomi_triage_queue_file_can_queue_comma_spans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "units.yomi.auto_accept.jsonl"
            output_path = root / "yomi_triage_input.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(
                json.dumps(
                    unit(
                        "u1",
                        "大学です、行きます。",
                        "大学/ダイガク です/デス 、/、 行き/イキ ます/マス 。/。",
                        accepted=False,
                    ),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_yomi_triage_queue_file(
                input_jsonl=input_path,
                output_jsonl=output_path,
                summary_json=summary_path,
                unit_mode="comma_span",
            )

            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary.queued, 2)
            self.assertEqual(summary.unit_mode, "comma_span")
            self.assertEqual([row["unit_id"] for row in rows], ["u1:s0001", "u1:s0002"])

    def test_apply_yomi_triage_results_merges_auto_and_llm_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.yomi.auto_accept.jsonl"
            results_path = root / "yomi_triage_results.jsonl"
            output_path = root / "units.yomi.triaged.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            unit("u1", "大学です。", "大学/ダイガク です/デス 。/。", accepted=True),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            unit("u2", "方です。", "方/ホウ です/デス 。/。", accepted=False),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            unit("u3", "時です。", "時/ジ です/デス 。/。", accepted=False),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            unit("u4", "人です。", "人/ジン です/デス 。/。", accepted=False),
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "item_id": "u2",
                                "raw_text": "Skip",
                                "parsed": {"status": "Skip"},
                                "parse_error": None,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "item_id": "u3",
                                "raw_text": "maybe",
                                "parsed": None,
                                "parse_error": "Expected exactly one of OK, Review, or Skip.",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_triage_results_file(
                units_jsonl=units_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            statuses = {
                row["unit_id"]: row["analysis"]["llm"]["yomi_triage"]["status"]
                for row in rows
            }
            sources = {
                row["unit_id"]: row["analysis"]["llm"]["yomi_triage"]["source"]
                for row in rows
            }
            self.assertEqual(statuses, {"u1": "OK", "u2": "Skip", "u3": "Review", "u4": "Review"})
            self.assertEqual(sources["u1"], "auto_accept")
            self.assertEqual(sources["u2"], "llm")
            self.assertEqual(sources["u3"], "parse_error")
            self.assertEqual(sources["u4"], "missing_llm_result")
            self.assertEqual(summary.auto_accepted_ok, 1)
            self.assertEqual(summary.llm_skip, 1)
            self.assertEqual(summary.parse_error_review, 1)
            self.assertEqual(summary.missing_result_review, 1)

    def test_apply_yomi_triage_blocks_ok_when_prompt_yomi_has_unannotated_kanji(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.yomi.auto_accept.jsonl"
            results_path = root / "yomi_triage_results.jsonl"
            output_path = root / "units.yomi.triaged.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    unit("u1", "難語です。", "難語/ です/デス 。/。", accepted=False),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1",
                        "raw_text": "OK",
                        "parsed": {"status": "OK"},
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_triage_results_file(
                units_jsonl=units_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            row = json.loads(output_path.read_text(encoding="utf-8").strip())
            judgment = row["analysis"]["llm"]["yomi_triage"]
            self.assertEqual(judgment["status"], "Review")
            self.assertEqual(judgment["source"], "llm_ok_blocked_unannotated_kanji")
            self.assertTrue(judgment["has_unannotated_kanji"])
            self.assertEqual(judgment["blocked_reason"], "unannotated_kanji")
            self.assertEqual(summary.llm_ok, 0)
            self.assertEqual(summary.llm_review, 1)
            self.assertEqual(summary.blocked_unannotated_kanji_review, 1)

    def test_apply_yomi_triage_allows_skip_when_prompt_yomi_has_unannotated_kanji(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.yomi.auto_accept.jsonl"
            results_path = root / "yomi_triage_results.jsonl"
            output_path = root / "units.yomi.triaged.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    unit("u1", "難語です。", "難語/ です/デス 。/。", accepted=False),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1",
                        "raw_text": "Skip",
                        "parsed": {"status": "Skip"},
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_triage_results_file(
                units_jsonl=units_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            row = json.loads(output_path.read_text(encoding="utf-8").strip())
            judgment = row["analysis"]["llm"]["yomi_triage"]
            self.assertEqual(judgment["status"], "Skip")
            self.assertEqual(judgment["source"], "llm")
            self.assertTrue(judgment["has_unannotated_kanji"])
            self.assertEqual(summary.llm_skip, 1)
            self.assertEqual(summary.blocked_unannotated_kanji_review, 0)

    def test_has_unannotated_kanji_ignores_annotated_kanji(self) -> None:
        self.assertFalse(has_unannotated_kanji("大学（だいがく）です。"))
        self.assertTrue(has_unannotated_kanji("難語です。"))

    def test_has_unannotated_kanji_or_latin_token_uses_canonical_spaced_tokens(self) -> None:
        self.assertFalse(has_unannotated_kanji_or_latin_token("霞ヶ関/カスミガセキ です/デス 。/。"))
        self.assertFalse(has_unannotated_kanji_or_latin_token("API/エーピーアイ です/デス 。/。"))
        self.assertTrue(has_unannotated_kanji_or_latin_token("難語/ です/デス 。/。"))
        self.assertTrue(has_unannotated_kanji_or_latin_token("API/ です/デス 。/。"))
        self.assertTrue(has_unannotated_kanji_or_latin_token("難語/なんご です/デス 。/。"))
        self.assertTrue(has_unannotated_kanji_or_latin_token("API/api です/デス 。/。"))
        self.assertTrue(has_unannotated_kanji_or_latin_token("難語/ナンゴ1 です/デス 。/。"))
        self.assertTrue(has_unannotated_kanji_or_latin_token("難語 です/デス 。/。"))
        self.assertFalse(has_unannotated_kanji_or_latin_token("二〇〇二/ 年/ネン です/デス 。/。"))

    def test_flattened_prompt_ignores_unannotated_japanese_numeral_run(self) -> None:
        self.assertFalse(has_unannotated_kanji("二〇〇二年（ねん）です。"))

    def test_is_katakana_reading_allows_only_katakana_and_long_vowel_mark(self) -> None:
        self.assertTrue(is_katakana_reading("カタカナー"))
        self.assertFalse(is_katakana_reading(""))
        self.assertFalse(is_katakana_reading("かたかな"))
        self.assertFalse(is_katakana_reading("カタカナ・"))
        self.assertFalse(is_katakana_reading("API"))

    def test_apply_yomi_triage_results_aggregates_comma_span_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.yomi.auto_accept.jsonl"
            results_path = root / "yomi_triage_results.jsonl"
            output_path = root / "units.yomi.triaged.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    unit(
                        "u1",
                        "大学です、古文です。",
                        "大学/ダイガク です/デス 、/、 古文/コブン です/デス 。/。",
                        accepted=False,
                    ),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "item_id": "u1:s0001",
                                "raw_text": "OK",
                                "parsed": {"status": "OK"},
                                "parse_error": None,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "item_id": "u1:s0002",
                                "raw_text": "Skip",
                                "parsed": {"status": "Skip"},
                                "parse_error": None,
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_triage_results_file(
                units_jsonl=units_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
                unit_mode="comma_span",
            )

            row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            judgment = row["analysis"]["llm"]["yomi_triage"]
            self.assertEqual(judgment["status"], "Skip")
            self.assertEqual(judgment["source"], "span_aggregate")
            self.assertEqual([span["status"] for span in judgment["spans"]], ["OK", "Skip"])
            self.assertEqual(summary.llm_ok, 1)
            self.assertEqual(summary.llm_skip, 1)
            self.assertEqual(summary.unit_mode, "comma_span")


if __name__ == "__main__":
    unittest.main()
