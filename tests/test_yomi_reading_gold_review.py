from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class YomiReadingGoldReviewTests(unittest.TestCase):
    def test_generator_seeds_failures_without_prefilling_expected_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            regression_path = root / "regression.jsonl"
            scored_path = root / "scored.jsonl"
            output_tsv = root / "review.tsv"
            output_jsonl = root / "review.jsonl"
            summary_path = root / "summary.json"

            write_jsonl(
                queue_path,
                [
                    queue_row("u1:r1", "乗", "乗り越え", "の", "ここを**乗**り越える。"),
                    queue_row("u1:r2", "SNS", "SNS", "えすえぬえす", "**SNS**を使う。"),
                    queue_row("u1:r3", "日々", "日々", "ひび", "**日々**続ける。"),
                    queue_row("u1:r4", "中", "中", "ちゅう", "午前**中**です。"),
                ],
            )
            write_jsonl(
                regression_path,
                [
                    {
                        "item_id": "gold_001",
                        "surface": "痛",
                        "expected_reading": "いた",
                        "marked_text": "目が**痛**い。",
                        "note": "seed",
                    }
                ],
            )
            write_jsonl(
                scored_path,
                [
                    {
                        "item_id": "u1:r1",
                        "passed": False,
                        "raw_text": '{"乗":"のり"}',
                        "parse_error": None,
                        "actual": {"reading": "のり"},
                    }
                ],
            )

            subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_yomi_reading_gold_review.py"),
                    "--queue-jsonl",
                    str(queue_path),
                    "--regression-jsonl",
                    str(regression_path),
                    "--scored-jsonl",
                    str(scored_path),
                    "--output-tsv",
                    str(output_tsv),
                    "--output-jsonl",
                    str(output_jsonl),
                    "--summary-json",
                    str(summary_path),
                    "--target-size",
                    "4",
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            rows = list(csv.DictReader(output_tsv.open(encoding="utf-8"), dialect="excel-tab"))
            self.assertEqual(len(rows), 4)
            by_id = {row["item_id"]: row for row in rows}
            self.assertEqual(by_id["gold_001"]["expected_reading"], "いた")
            self.assertEqual(by_id["gold_001"]["seed_source"], "regression_gold_seed")
            self.assertEqual(by_id["u1:r1"]["expected_reading"], "")
            self.assertEqual(by_id["u1:r1"]["current_reading_hiragana"], "の")
            self.assertEqual(by_id["u1:r1"]["llm_reading"], "のり")
            self.assertEqual(by_id["u1:r1"]["seed_source"], "llm_failure_seed")

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["selected_count"], 4)
            self.assertEqual(summary["seed_source_counts"]["regression_gold_seed"], 1)
            self.assertEqual(summary["seed_source_counts"]["llm_failure_seed"], 1)


def queue_row(
    item_id: str,
    surface: str,
    token_surface: str,
    current_reading_hiragana: str,
    marked_text: str,
) -> dict[str, str | int]:
    return {
        "unit_id": "u1",
        "item_id": item_id,
        "surface": surface,
        "token_surface": token_surface,
        "current_reading_hiragana": current_reading_hiragana,
        "marked_text": marked_text,
        "chunk_index": 0,
    }


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
