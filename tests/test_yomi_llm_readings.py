from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.llm_readings import (
    apply_yomi_llm_reading_results_file,
    build_item_judgment,
    build_yomi_llm_reading_items,
    build_yomi_llm_reading_queue_file,
    build_yomi_llm_reading_retry_queue_file,
)
from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.parsers import parse_output
from yomi_corpus.llm.tasks import build_prompt_items
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
    def test_yomi_prompt_clips_only_pathologically_long_context(self) -> None:
        task_config = load_llm_task_config("config/llm/yomi_reading.toml")
        text = ("前" * 150) + "学校" + ("後" * 150)
        row = {
            "item_id": "long:r0001c01",
            "surface": "学校",
            "text": text,
            "marked_text": ("前" * 150) + "**学校**" + ("後" * 150),
            "target_start": 150,
            "target_end": 152,
        }

        item = build_prompt_items(task_config, [row])[0]

        expected_context = "…" + ("前" * 80) + "**学校**" + ("後" * 80) + "…"
        self.assertIn(expected_context, item.prompt)
        self.assertNotIn("前" * 81, item.prompt)
        self.assertEqual(item.metadata["source_row"]["text"], text)
        self.assertEqual(
            item.metadata["source_row"]["marked_text"],
            row["marked_text"],
        )
        self.assertEqual(
            item.metadata["prompt_context"],
            {
                "clipped": True,
                "original_text_chars": 302,
                "clip_threshold_chars": 200,
                "side_context_chars": 80,
                "context_start": 70,
                "context_end": 232,
                "left_clipped": True,
                "right_clipped": True,
                "prompt_text_chars": 162,
            },
        )

    def test_yomi_prompt_keeps_context_at_threshold(self) -> None:
        task_config = load_llm_task_config("config/llm/yomi_reading.toml")
        text = ("前" * 99) + "学校" + ("後" * 99)
        marked_text = ("前" * 99) + "**学校**" + ("後" * 99)
        row = {
            "item_id": "threshold:r0001c01",
            "surface": "学校",
            "text": text,
            "marked_text": marked_text,
            "target_start": 99,
            "target_end": 101,
        }

        item = build_prompt_items(task_config, [row])[0]

        self.assertIn(marked_text, item.prompt)
        self.assertFalse(item.metadata["prompt_context"]["clipped"])

    def test_build_items_marks_sudachi_tokens(self) -> None:
        items = build_yomi_llm_reading_items(unit())

        self.assertEqual([item["surface"] for item in items], ["学校", "上"])
        self.assertEqual(items[0]["marked_text"], "**学校**は上です。")
        self.assertEqual(items[0]["marked_furigana_text"], "**学校**は上（うえ）です。")
        self.assertEqual(items[0]["current_reading_hiragana"], "がっこう")
        self.assertEqual(items[1]["marked_text"], "学校は**上**です。")
        self.assertEqual(items[1]["marked_furigana_text"], "学校（がっこう）は**上**です。")

    def test_build_items_uses_hybrid_rendered_reading_as_current(self) -> None:
        payload = {
            "unit_id": "u_hybrid",
            "text": "ご興味がわいた方は。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "ご/ゴ 興味/キョウミ が/ガ わい/ワイ た/タ 方/カタ は/ハ 。/。",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "ご",
                                    "pos": "接頭辞,*,*,*,*,*",
                                    "dictionary_form": "御",
                                    "normalized_form": "ご",
                                    "reading": "ゴ",
                                },
                                {
                                    "surface": "興味",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                    "dictionary_form": "興味",
                                    "normalized_form": "興味",
                                    "reading": "キョウミ",
                                },
                                {
                                    "surface": "が",
                                    "pos": "助詞,格助詞,*,*,*,*",
                                    "dictionary_form": "が",
                                    "normalized_form": "が",
                                    "reading": "ガ",
                                },
                                {
                                    "surface": "わい",
                                    "pos": "動詞,一般,*,*,五段-カ行,連用形-イ音便",
                                    "dictionary_form": "わく",
                                    "normalized_form": "わく",
                                    "reading": "ワイ",
                                },
                                {
                                    "surface": "た",
                                    "pos": "助動詞,*,*,*,助動詞-タ,連体形-一般",
                                    "dictionary_form": "た",
                                    "normalized_form": "た",
                                    "reading": "タ",
                                },
                                {
                                    "surface": "方",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                    "dictionary_form": "方",
                                    "normalized_form": "方",
                                    "reading": "ホウ",
                                },
                                {
                                    "surface": "は",
                                    "pos": "助詞,係助詞,*,*,*,*",
                                    "dictionary_form": "は",
                                    "normalized_form": "は",
                                    "reading": "ハ",
                                },
                                {
                                    "surface": "。",
                                    "pos": "補助記号,句点,*,*,*,*",
                                    "dictionary_form": "。",
                                    "normalized_form": "。",
                                    "reading": "。",
                                },
                            ]
                        },
                    }
                }
            },
        }

        items = build_yomi_llm_reading_items(payload)

        item = next(row for row in items if row["surface"] == "方")
        self.assertEqual(item["current_reading"], "カタ")
        self.assertEqual(item["current_reading_hiragana"], "かた")
        self.assertEqual(
            item["marked_furigana_text"],
            "ご興味（きょうみ）がわいた**方**は。",
        )

    def test_iteration_mark_is_part_of_kanji_target(self) -> None:
        payload = {
            "unit_id": "u2",
            "text": "日々変わります。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "日々",
                                    "pos": "名詞,普通名詞,副詞可能,*,*,*",
                                    "dictionary_form": "日々",
                                    "normalized_form": "日々",
                                    "reading": "ヒビ",
                                },
                                {
                                    "surface": "変わり",
                                    "pos": "動詞,一般,*,*,五段-ラ行,連用形-一般",
                                    "dictionary_form": "変わる",
                                    "normalized_form": "変わる",
                                    "reading": "カワリ",
                                },
                                {
                                    "surface": "ます",
                                    "pos": "助動詞,*,*,*,助動詞-マス,終止形-一般",
                                    "dictionary_form": "ます",
                                    "normalized_form": "ます",
                                    "reading": "マス",
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

        items = build_yomi_llm_reading_items(payload)

        self.assertEqual(items[0]["surface"], "日々")
        self.assertEqual(items[0]["current_reading_hiragana"], "ひび")
        self.assertEqual(items[0]["marked_text"], "**日々**変わります。")
        self.assertEqual(items[0]["marked_furigana_text"], "**日々**変（か）わります。")

    def test_alphabetic_target_is_marked_and_compared(self) -> None:
        payload = {
            "unit_id": "u3",
            "text": "30分でもOKです。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "30",
                                    "pos": "名詞,数詞,*,*,*,*",
                                    "dictionary_form": "30",
                                    "normalized_form": "30",
                                    "reading": "",
                                },
                                {
                                    "surface": "分",
                                    "pos": "名詞,普通名詞,助数詞可能,*,*,*",
                                    "dictionary_form": "分",
                                    "normalized_form": "分",
                                    "reading": "フン",
                                },
                                {
                                    "surface": "でも",
                                    "pos": "助詞,副助詞,*,*,*,*",
                                    "dictionary_form": "でも",
                                    "normalized_form": "でも",
                                    "reading": "デモ",
                                },
                                {
                                    "surface": "OK",
                                    "pos": "名詞,普通名詞,サ変可能,*,*,*",
                                    "dictionary_form": "OK",
                                    "normalized_form": "OK",
                                    "reading": "オーケー",
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

        items = build_yomi_llm_reading_items(payload)

        self.assertEqual([item["surface"] for item in items], ["分", "OK"])
        alphabetic = items[1]
        self.assertEqual(alphabetic["marked_text"], "30分でも**OK**です。")
        self.assertEqual(alphabetic["current_reading_hiragana"], "おーけー")
        judgment = build_item_judgment(
            alphabetic,
            {
                "item_id": alphabetic["item_id"],
                "raw_text": '{"OK":"オーケー"}',
                "parsed": {"OK": "オーケー"},
            },
        )
        self.assertEqual(judgment["status"], "matched")

    def test_ascii_space_text_aligns_with_nbsp_yomi_token(self) -> None:
        payload = {
            "unit_id": "u4",
            "text": "お金 人生です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "お金",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                    "dictionary_form": "お金",
                                    "normalized_form": "お金",
                                    "reading": "オカネ",
                                },
                                {
                                    "surface": "\u00a0",
                                    "pos": "空白,*,*,*,*,*",
                                    "dictionary_form": " ",
                                    "normalized_form": " ",
                                    "reading": "キゴウ",
                                },
                                {
                                    "surface": "人生",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                    "dictionary_form": "人生",
                                    "normalized_form": "人生",
                                    "reading": "ジンセイ",
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

        items = build_yomi_llm_reading_items(payload)

        self.assertEqual([item["surface"] for item in items], ["金", "人生"])
        self.assertEqual(items[0]["marked_text"], "お**金** 人生です。")
        self.assertEqual(items[1]["marked_text"], "お金 **人生**です。")

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
            safety_targets = row["analysis"]["safety"]["yomi"]["targets"]
            self.assertEqual([target["surface"] for target in safety_targets], ["学校", "上"])
            self.assertEqual([target["is_safe"] for target in safety_targets], [True, False])
            self.assertEqual(
                [target["review_status"] for target in safety_targets],
                ["safe", "unresolved"],
            )
            self.assertIn("safe_by_llm_match", safety_targets[0]["accepted_signal_names"])
            self.assertNotIn("safe_by_llm_match", safety_targets[1]["accepted_signal_names"])
            self.assertEqual(
                row["analysis"]["safety"]["yomi"]["summary"],
                {
                    "target_count": 2,
                    "safe_count": 1,
                    "unresolved_count": 1,
                    "all_targets_safe": False,
                },
            )

    def test_retry_queue_includes_only_parse_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            retry_path = root / "retry.jsonl"
            summary_path = root / "retry_summary.json"
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
                                "raw_text": '{"上":"ue"}',
                                "parsed": {"上": "ue"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_yomi_llm_reading_retry_queue_file(
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=retry_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.read_items, 2)
            self.assertEqual(summary.retry_items, 1)
            rows = [json.loads(line) for line in retry_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["item_id"], items[1]["item_id"])
            self.assertEqual(rows[0]["retry_of"], items[1]["item_id"])
            self.assertEqual(rows[0]["attempt"], 2)
            self.assertIn("is not kana", rows[0]["retry_reason"])

    def test_retry_results_override_first_pass_parse_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            retry_results_path = root / "retry_results.jsonl"
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
                                "raw_text": '{"下":"した"}',
                                "parsed": {"下": "した"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            retry_results_path.write_text(
                json.dumps(
                    {
                        "item_id": items[1]["item_id"],
                        "raw_text": '{"上":"うえ"}',
                        "parsed": {"上": "うえ"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_llm_reading_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                retry_results_jsonl=retry_results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.matched_items, 2)
            self.assertEqual(summary.parse_error_items, 0)
            row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            judgments = row["analysis"]["llm"]["yomi_readings"]["items"]
            self.assertEqual([judgment["status"] for judgment in judgments], ["matched", "matched"])

    def test_multiple_retry_result_files_are_merged_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            retry2_results_path = root / "retry2_results.jsonl"
            retry3_results_path = root / "retry3_results.jsonl"
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
                                "raw_text": '{"下":"した"}',
                                "parsed": {"下": "した"},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "item_id": items[1]["item_id"],
                                "raw_text": '{"下":"した"}',
                                "parsed": {"下": "した"},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            retry2_results_path.write_text(
                json.dumps(
                    {
                        "item_id": items[0]["item_id"],
                        "raw_text": '{"学校":"がっこう"}',
                        "parsed": {"学校": "がっこう"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            retry3_results_path.write_text(
                json.dumps(
                    {
                        "item_id": items[1]["item_id"],
                        "raw_text": '{"上":"うえ"}',
                        "parsed": {"上": "うえ"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_llm_reading_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                retry_results_jsonls=[retry2_results_path, retry3_results_path],
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.matched_items, 2)
            self.assertEqual(summary.parse_error_items, 0)

    def test_build_prompt_items_marks_only_target_surface(self) -> None:
        config = load_llm_task_config("config/llm/yomi_reading.toml")
        item = build_yomi_llm_reading_items(unit())[1]

        prompts = build_prompt_items(config, [item])

        self.assertEqual(prompts[0].item_id, item["item_id"])
        self.assertIn('目が**痛**い。->{"痛":"いた"}', prompts[0].prompt)
        self.assertIn("学校は**上**です。", prompts[0].prompt)
        self.assertTrue(prompts[0].prompt.rstrip().endswith("学校は**上**です。->"))
        self.assertNotIn('"学校"', prompts[0].prompt)

    def test_build_completion_prompt_prefills_target_key(self) -> None:
        config = load_llm_task_config("config/llm/yomi_reading_completion.toml")
        item = build_yomi_llm_reading_items(unit())[1]

        prompts = build_prompt_items(config, [item])

        self.assertIn('目が**痛**い。->{"痛":"いた"}', prompts[0].prompt)
        self.assertTrue(prompts[0].prompt.rstrip().endswith('学校は**上**です。->{"上":'))
        self.assertEqual(prompts[0].metadata["surface"], "上")

    def test_json_parser_accepts_plain_or_fenced_object(self) -> None:
        self.assertEqual(parse_output('{"上":"うえ"}', "json_object"), {"上": "うえ"})
        self.assertEqual(
            parse_output('```json\n{"上":"うえ"}\n```', "json_object"),
            {"上": "うえ"},
        )

    def test_item_judgment_accepts_extra_json_keys_when_target_key_is_present(self) -> None:
        item = build_yomi_llm_reading_items(unit())[1]

        judgment = build_item_judgment(
            item,
            {
                "item_id": item["item_id"],
                "raw_text": '{"上":"うえ","学校":"がっこう"}',
                "parsed": {"上": "うえ", "学校": "がっこう"},
            },
        )

        self.assertEqual(judgment["status"], "matched")
        self.assertEqual(judgment["extra_json_keys"], ["学校"])

    def test_item_judgment_salvages_echoed_prompt_json(self) -> None:
        item = build_yomi_llm_reading_items(unit())[1]

        judgment = build_item_judgment(
            item,
            {
                "item_id": item["item_id"],
                "raw_text": '学校は**上**です。->{"上":"うえ"}',
                "parsed": None,
                "parse_error": "Expected a JSON object in model output.",
            },
        )

        self.assertEqual(judgment["status"], "matched")
        self.assertEqual(judgment["llm_reading"], "うえ")

    def test_item_judgment_rejects_wrong_json_key(self) -> None:
        item = build_yomi_llm_reading_items(unit())[1]

        judgment = build_item_judgment(
            item,
            {
                "item_id": item["item_id"],
                "raw_text": '{"下":"した"}',
                "parsed": {"下": "した"},
            },
        )

        self.assertEqual(judgment["status"], "parse_error")
        self.assertIn("'上'", judgment["parse_error"])

    def test_item_judgment_rejects_non_string_reading(self) -> None:
        item = build_yomi_llm_reading_items(unit())[1]

        judgment = build_item_judgment(
            item,
            {
                "item_id": item["item_id"],
                "raw_text": '{"上":["うえ"]}',
                "parsed": {"上": ["うえ"]},
            },
        )

        self.assertEqual(judgment["status"], "parse_error")
        self.assertIn("is not a string", judgment["parse_error"])

    def test_item_judgment_rejects_non_kana_reading(self) -> None:
        item = build_yomi_llm_reading_items(unit())[1]

        judgment = build_item_judgment(
            item,
            {
                "item_id": item["item_id"],
                "raw_text": '{"上":"ue"}',
                "parsed": {"上": "ue"},
            },
        )

        self.assertEqual(judgment["status"], "parse_error")
        self.assertIn("is not kana", judgment["parse_error"])
        self.assertIsNone(judgment["llm_reading"])

    def test_item_judgment_marks_missing_result(self) -> None:
        item = build_yomi_llm_reading_items(unit())[1]

        judgment = build_item_judgment(item, None)

        self.assertEqual(judgment["status"], "missing_result")
        self.assertIsNone(judgment["llm_reading"])


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
