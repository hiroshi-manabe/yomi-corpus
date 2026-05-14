from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.triage import (
    apply_yomi_triage_results_file,
    build_yomi_triage_item,
    build_yomi_triage_queue_file,
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
    def test_build_yomi_triage_item_keeps_minimal_llm_input(self) -> None:
        item = build_yomi_triage_item(
            unit("u1", "大学です。", "大学/ダイガク です/デス 。/。", accepted=False)
        )

        self.assertEqual(item["unit_id"], "u1")
        self.assertEqual(item["text"], "大学です。")
        self.assertEqual(item["rendered"], "大学/ダイガク です/デス 。/。")
        self.assertFalse(item["auto_accept"]["value"])

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


if __name__ == "__main__":
    unittest.main()
