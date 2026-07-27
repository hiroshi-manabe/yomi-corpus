from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.scope import (
    apply_scope_triage_results_file,
    build_scope_triage_queue_file,
)


class ScopeTriageTests(unittest.TestCase):
    def test_provisional_alphabetic_skip_is_not_queued_for_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "units.alphabetic.jsonl"
            queue_path = root / "scope_triage_input.jsonl"
            summary_path = root / "scope_triage_queue_summary.json"
            input_path.write_text(
                json.dumps(provisional_skip_unit("u1"), ensure_ascii=False) + "\n"
                + json.dumps(keep_unit("u2"), ensure_ascii=False) + "\n"
                + json.dumps(symbol_only_unit("u3"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            summary = build_scope_triage_queue_file(
                input_jsonl=input_path,
                output_jsonl=queue_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.read, 3)
            self.assertEqual(summary.queued, 1)
            self.assertEqual(summary.provisional_alphabetic_skip, 1)
            self.assertEqual(summary.symbol_only_keep, 1)
            rows = [
                json.loads(line)
                for line in queue_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([row["unit_id"] for row in rows], ["u2"])

    def test_provisional_alphabetic_skip_is_materialized_as_scope_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.alphabetic.jsonl"
            results_path = root / "scope_triage_results.jsonl"
            output_path = root / "units.scope_triaged.jsonl"
            summary_path = root / "scope_triage_apply_summary.json"
            units_path.write_text(
                json.dumps(provisional_skip_unit("u1"), ensure_ascii=False) + "\n"
                + json.dumps(keep_unit("u2"), ensure_ascii=False) + "\n"
                + json.dumps(symbol_only_unit("u3"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u2",
                        "parsed": {"status": "Keep"},
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_scope_triage_results_file(
                units_jsonl=units_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.keep, 2)
            self.assertEqual(summary.skip, 1)
            self.assertEqual(summary.provisional_alphabetic_skip, 1)
            self.assertEqual(summary.symbol_only_keep, 1)
            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_id = {row["unit_id"]: row for row in rows}
            self.assertEqual(
                by_id["u1"]["analysis"]["llm"]["scope_triage"]["source"],
                "provisional_alphabetic_skip",
            )
            self.assertEqual(by_id["u1"]["analysis"]["llm"]["scope_triage"]["status"], "Skip")
            self.assertEqual(by_id["u2"]["analysis"]["llm"]["scope_triage"]["status"], "Keep")
            self.assertEqual(
                by_id["u3"]["analysis"]["llm"]["scope_triage"],
                {
                    "status": "Keep",
                    "source": "mechanical_symbol_only_keep",
                    "parse_error": None,
                    "raw_text": None,
                    "result_item_id": "u3",
                },
            )

    def test_latin_only_unit_still_uses_scope_triage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "units.alphabetic.jsonl"
            queue_path = root / "scope_triage_input.jsonl"
            summary_path = root / "scope_triage_queue_summary.json"
            input_path.write_text(
                json.dumps(keep_unit("u1", text="MeguruQuruwa"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            summary = build_scope_triage_queue_file(
                input_jsonl=input_path,
                output_jsonl=queue_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.queued, 1)
            self.assertEqual(summary.symbol_only_keep, 0)

    def test_terminal_exclusion_is_preserved_for_human_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(keep_unit("u1"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1",
                        "parsed": {"status": "Exclude"},
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_scope_triage_results_file(
                units_jsonl=units_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            row = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary.exclude, 1)
            self.assertEqual(summary.keep, 0)
            self.assertEqual(row["analysis"]["llm"]["scope_triage"]["status"], "Exclude")


def provisional_skip_unit(unit_id: str) -> dict:
    return {
        "unit_id": unit_id,
        "text": "Concerts de Midiです。",
        "analysis": {
            "mechanical": {
                "alphabetic_scope": {
                    "provisional_skip": True,
                    "status": "provisional_skip",
                    "reasons": [
                        {
                            "entity_key": "concerts de midi",
                            "entity_text": "Concerts de Midi",
                            "effective_status": "out_of_scope",
                            "source": "llm",
                        }
                    ],
                }
            }
        },
    }


def keep_unit(unit_id: str, *, text: str = "普通の文です。") -> dict:
    return {
        "unit_id": unit_id,
        "text": text,
        "analysis": {"mechanical": {"alphabetic_scope": {"provisional_skip": False}}},
    }


def symbol_only_unit(unit_id: str) -> dict:
    return keep_unit(unit_id, text="！？")


if __name__ == "__main__":
    unittest.main()
