from __future__ import annotations

import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from yomi_corpus.yomi.corpus_frequency import SurfaceReadingCount, SurfaceReadingStats
from yomi_corpus.yomi.llm_readings import build_yomi_llm_reading_queue_file
from yomi_corpus.yomi.safety import build_pre_llm_safety_records, set_yomi_safety_records


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


class YomiSafetyTests(unittest.TestCase):
    def test_corpus_frequency_marks_matching_target_safe(self) -> None:
        stats = make_stats(
            [
                SurfaceReadingCount(
                    surface="学校",
                    reading="ガッコウ",
                    count=5,
                    surface_total_count=5,
                    share=1.0,
                    source_corpus_version="fixture",
                )
            ]
        )

        records = build_pre_llm_safety_records(unit(), corpus_stats=stats)

        by_surface = {record["surface"]: record for record in records}
        self.assertTrue(by_surface["学校"]["is_safe"])
        self.assertIn("safe_by_corpus_frequency", by_surface["学校"]["accepted_signal_names"])
        self.assertFalse(by_surface["上"]["is_safe"])

    def test_whole_unit_auto_accept_marks_all_targets_safe(self) -> None:
        payload = unit()
        payload["analysis"]["mechanical"]["yomi"]["auto_accept"] = {
            "value": True,
            "rule": "fixture_auto_accept",
        }

        records = build_pre_llm_safety_records(payload)

        self.assertEqual([record["surface"] for record in records], ["学校", "上"])
        self.assertTrue(all(record["is_safe"] for record in records))
        self.assertTrue(
            all("safe_by_unit_auto_accept" in record["accepted_signal_names"] for record in records)
        )

    def test_queue_skips_targets_marked_safe_by_safety_records(self) -> None:
        payload = unit()
        records = build_pre_llm_safety_records(
            payload,
            corpus_stats=make_stats(
                [
                    SurfaceReadingCount(
                        surface="学校",
                        reading="ガッコウ",
                        count=5,
                        surface_total_count=5,
                        share=1.0,
                        source_corpus_version="fixture",
                    )
                ]
            ),
        )
        set_yomi_safety_records(payload, records)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            queue_path = root / "queue.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            summary = build_yomi_llm_reading_queue_file(
                input_jsonl=input_path,
                output_jsonl=queue_path,
                summary_json=summary_path,
                skip_stable_two_kanji=False,
            )

            rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["surface"] for row in rows], ["上"])
            self.assertEqual(summary.queued_items, 1)
            self.assertEqual(summary.safety_skipped, 1)


def make_stats(rows: list[SurfaceReadingCount]) -> SurfaceReadingStats:
    by_surface = defaultdict(list)
    for row in rows:
        by_surface[row.surface].append(row)
    return SurfaceReadingStats(rows_by_surface=dict(by_surface), source_corpus_version="fixture")


if __name__ == "__main__":
    unittest.main()
