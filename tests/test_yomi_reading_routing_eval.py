from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = PROJECT_ROOT / "data/evals/yomi_reading/routing_targets_v1.jsonl"
SUMMARY_PATH = PROJECT_ROOT / "data/evals/yomi_reading/routing_targets_v1.summary.json"
EXPECTED_TARGETS = {"方", "人", "日", "月", "行", "中", "何", "入", "思", "多"}
HIRAGANA_RE = re.compile(r"[ぁ-ゖー]+")


class YomiReadingRoutingEvalTests(unittest.TestCase):
    def test_fixture_has_twenty_valid_unique_rows_per_target(self) -> None:
        rows = [
            json.loads(line)
            for line in EVAL_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 200)
        self.assertEqual(len({row["item_id"] for row in rows}), 200)
        self.assertEqual(
            Counter(row["surface"] for row in rows),
            Counter({target: 20 for target in EXPECTED_TARGETS}),
        )
        self.assertEqual(
            len({(row["surface"], row["marked_text"]) for row in rows}),
            200,
        )
        for row in rows:
            self.assertEqual(row["schema_version"], "yomi_reading_routing_eval_v1")
            self.assertEqual(row["marked_text"].replace("**", ""), row["text"])
            self.assertIn(f"**{row['surface']}**", row["marked_text"])
            self.assertRegex(row["expected_reading"], HIRAGANA_RE)
            self.assertIn(row["routing_population"], {"deterministic", "llm_routed"})
            self.assertNotEqual(
                (row["surface"], row["expected_reading"]),
                ("日", "か"),
            )
            self.assertEqual(
                row["was_llm_routed"],
                row["routing_population"] == "llm_routed",
            )

    def test_summary_matches_fixture(self) -> None:
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(summary["selected_count"], 200)
        self.assertEqual(set(summary["targets"]), EXPECTED_TARGETS)
        for target in EXPECTED_TARGETS:
            self.assertEqual(summary["targets_summary"][target]["selected_count"], 20)


if __name__ == "__main__":
    unittest.main()
