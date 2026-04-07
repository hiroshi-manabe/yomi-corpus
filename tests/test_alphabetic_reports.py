from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_reports import build_unresolved_entity_rows, shorten_example_text


class AlphabeticReportTests(unittest.TestCase):
    def test_filters_only_unresolved_entities(self) -> None:
        rows = [
            {
                "entity_key": "zoom",
                "resolved_status": "unknown",
                "base_list_status": "unknown",
                "occurrence_count": 2,
                "unit_count": 2,
                "surface_forms": ["zoom"],
                "example_unit_ids": ["u1"],
                "example_texts": ["zoomで参加します。"],
            },
            {
                "entity_key": "android",
                "resolved_status": "whitelist",
                "base_list_status": "whitelist",
                "occurrence_count": 3,
                "unit_count": 3,
                "surface_forms": ["Android"],
                "example_unit_ids": ["u2"],
                "example_texts": ["Androidを使う。"],
            },
        ]
        unresolved = build_unresolved_entity_rows(rows)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["entity_key"], "zoom")

    def test_sorts_by_occurrence_then_unit_count(self) -> None:
        rows = [
            {
                "entity_key": "b",
                "resolved_status": "unknown",
                "base_list_status": "unknown",
                "occurrence_count": 2,
                "unit_count": 1,
                "surface_forms": ["B"],
                "example_unit_ids": ["u2"],
                "example_texts": ["Bです。"],
            },
            {
                "entity_key": "a",
                "resolved_status": "unknown",
                "base_list_status": "unknown",
                "occurrence_count": 3,
                "unit_count": 1,
                "surface_forms": ["A"],
                "example_unit_ids": ["u1"],
                "example_texts": ["Aです。"],
            },
            {
                "entity_key": "c",
                "resolved_status": "unknown",
                "base_list_status": "unknown",
                "occurrence_count": 2,
                "unit_count": 2,
                "surface_forms": ["C"],
                "example_unit_ids": ["u3"],
                "example_texts": ["Cです。"],
            },
        ]
        unresolved = build_unresolved_entity_rows(rows)
        self.assertEqual([row["entity_key"] for row in unresolved], ["a", "c", "b"])

    def test_limits_examples_and_respects_min_occurrences(self) -> None:
        rows = [
            {
                "entity_key": "led zeppelin",
                "resolved_status": "unknown",
                "base_list_status": "unknown",
                "occurrence_count": 1,
                "unit_count": 1,
                "surface_forms": ["Led Zeppelin"],
                "example_unit_ids": ["u1", "u2", "u3"],
                "example_texts": ["e1", "e2", "e3"],
            }
        ]
        self.assertEqual(
            build_unresolved_entity_rows(rows, min_occurrences=2),
            [],
        )
        unresolved = build_unresolved_entity_rows(rows, max_examples=2)
        self.assertEqual(unresolved[0]["example_unit_ids"], ["u1", "u2"])
        self.assertEqual(unresolved[0]["example_texts"], ["e1", "e2"])

    def test_shorten_example_text_keeps_short_text(self) -> None:
        text = "Android端末の設定を確認してください。"
        shortened = shorten_example_text(text, entity_text_candidates=["Android"], max_chars=160)
        self.assertEqual(shortened, text)

    def test_shorten_example_text_centers_on_entity(self) -> None:
        text = (
            "前置きがかなり長く続いていきます。"
            "さらに説明が続きます。"
            "毎週水曜日はお昼のコンサート「Concerts de Midi」が開催されています。"
            "ここから先にも補足が長く続いていきます。"
            "さらに別の説明が続きます。"
        )
        shortened = shorten_example_text(
            text,
            entity_text_candidates=["Concerts de Midi"],
            max_chars=70,
        )
        self.assertIn("Concerts de Midi", shortened)
        self.assertTrue(shortened.startswith("...") or len(shortened) <= 70)
        self.assertTrue(shortened.endswith("...") or len(shortened) <= 70)

    def test_shorten_example_text_truncates_without_entity_match(self) -> None:
        text = "これはかなり長い説明文です。" * 20
        shortened = shorten_example_text(
            text,
            entity_text_candidates=["NoMatch"],
            max_chars=40,
        )
        self.assertLessEqual(len(shortened), 43)
        self.assertTrue(shortened.endswith("..."))


if __name__ == "__main__":
    unittest.main()
