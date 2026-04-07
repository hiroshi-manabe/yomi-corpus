from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic import (
    apply_global_decisions,
    aggregate_occurrences,
    build_occurrences_for_unit,
    extract_alphabetic_entities,
    load_alphabetic_config,
    project_minor_alphabetic_judgment,
)
from yomi_corpus.alphabetic_state import (
    AlphabeticDecision,
    AlphabeticEvidence,
    append_alphabetic_evidence,
    load_alphabetic_decisions,
    upsert_alphabetic_decision,
)


class AlphabeticPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_alphabetic_config("config/alphabetic/default.toml")

    def test_occurrence_builder_uses_case_insensitive_key_for_single_long_entity(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "ANDROIDを使っています。",
        }
        occurrences = build_occurrences_for_unit(unit, self.config)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].entity_text, "ANDROID")
        self.assertEqual(occurrences[0].entity_key, "android")
        self.assertEqual(occurrences[0].base_list_status, "whitelist")
        self.assertEqual(occurrences[0].resolved_status, "whitelist")

    def test_occurrence_builder_uses_exact_key_for_single_short_entity(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "AIを使っています。",
        }
        occurrences = build_occurrences_for_unit(unit, self.config)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].entity_key, "AI")
        self.assertTrue(occurrences[0].strict_case)
        self.assertEqual(occurrences[0].base_list_status, "unknown")

    def test_entity_extractor_merges_space_separated_tokens(self) -> None:
        entities = extract_alphabetic_entities("Led Zeppelinが好きです。", self.config)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "Led Zeppelin")
        self.assertEqual(entities[0].normalized, "led zeppelin")
        self.assertEqual(entities[0].component_texts, ["Led", "Zeppelin"])
        self.assertFalse(entities[0].strict_case)

    def test_entity_extractor_keeps_alphanumeric_token_with_letters(self) -> None:
        entities = extract_alphabetic_entities("V6が好きです。", self.config)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "V6")
        self.assertEqual(entities[0].normalized, "v6")
        self.assertEqual(entities[0].component_texts, ["V6"])

    def test_entity_extractor_does_not_absorb_standalone_number(self) -> None:
        entities = extract_alphabetic_entities("iPhone 16が発売されました。", self.config)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "iPhone")
        self.assertEqual(entities[0].normalized, "iphone")

    def test_entity_extractor_merges_hyphen_separated_tokens(self) -> None:
        entities = extract_alphabetic_entities("Jean - Lucが来た。", self.config)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "Jean - Luc")
        self.assertEqual(entities[0].normalized, "jean-luc")
        self.assertEqual(entities[0].component_texts, ["Jean", "Luc"])

    def test_entity_extractor_merges_apostrophe_separated_tokens(self) -> None:
        entities = extract_alphabetic_entities("rock 'n' rollが好き。", self.config)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "rock 'n' roll")
        self.assertEqual(entities[0].normalized, "rock'n'roll")
        self.assertEqual(entities[0].component_texts, ["rock", "n", "roll"])

    def test_projection_with_no_tokens_is_safe(self) -> None:
        judgment = project_minor_alphabetic_judgment([])
        self.assertFalse(judgment.value)
        self.assertTrue(judgment.certain)
        self.assertEqual(judgment.signals, ["no_latin_entity_tokens"])

    def test_projection_with_blacklist_occurrence_is_certain_out_of_scope(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "Concertsが開催されます。",
        }
        occurrences = build_occurrences_for_unit(unit, self.config)
        judgment = project_minor_alphabetic_judgment(occurrences)
        self.assertTrue(judgment.value)
        self.assertTrue(judgment.certain)
        self.assertIn("Concerts", judgment.matches)

    def test_aggregation_groups_same_long_entity_case_insensitively(self) -> None:
        unit_a = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "Androidを使っています。",
        }
        unit_b = {
            "doc_id": "d2",
            "unit_id": "d2:u0001",
            "unit_seq": 1,
            "text": "ANDROID対応です。",
        }
        occurrences = build_occurrences_for_unit(unit_a, self.config) + build_occurrences_for_unit(
            unit_b, self.config
        )
        token_types = aggregate_occurrences(occurrences)
        self.assertEqual(len(token_types), 1)
        self.assertEqual(token_types[0].entity_key, "android")
        self.assertEqual(token_types[0].occurrence_count, 2)
        self.assertEqual(token_types[0].unit_count, 2)

    def test_aggregation_groups_same_multiword_entity_case_insensitively(self) -> None:
        unit_a = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "Led Zeppelinが好きです。",
        }
        unit_b = {
            "doc_id": "d2",
            "unit_id": "d2:u0001",
            "unit_seq": 1,
            "text": "LED ZEPPELINを聴く。",
        }
        occurrences = build_occurrences_for_unit(unit_a, self.config) + build_occurrences_for_unit(
            unit_b, self.config
        )
        token_types = aggregate_occurrences(occurrences)
        self.assertEqual(len(token_types), 1)
        self.assertEqual(token_types[0].entity_key, "led zeppelin")
        self.assertEqual(token_types[0].occurrence_count, 2)

    def test_global_decision_override_changes_projection(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "zoomで参加します。",
        }
        occurrences = build_occurrences_for_unit(unit, self.config)
        overridden = apply_global_decisions(occurrences, {"zoom": "whitelist"})
        judgment = project_minor_alphabetic_judgment(overridden)
        self.assertFalse(judgment.value)
        self.assertTrue(judgment.certain)
        self.assertIn("zoom", judgment.matches)

    def test_upsert_and_load_decision_registry(self) -> None:
        temp_path = PROJECT_ROOT / "tests" / "tmp_token_decisions.jsonl"
        if temp_path.exists():
            temp_path.unlink()
        upsert_alphabetic_decision(
            temp_path,
            AlphabeticDecision(
                entity_key="zoom",
                strict_case=False,
                status="whitelist",
                source="manual",
                note="accepted modern usage",
            ),
        )
        decisions = load_alphabetic_decisions(temp_path)
        self.assertIn("zoom", decisions)
        self.assertEqual(decisions["zoom"].status, "whitelist")
        temp_path.unlink()

    def test_append_evidence_replaces_same_batch_rows(self) -> None:
        temp_path = PROJECT_ROOT / "tests" / "tmp_token_evidence.jsonl"
        if temp_path.exists():
            temp_path.unlink()
        append_alphabetic_evidence(
            temp_path,
            [
                AlphabeticEvidence(
                    batch_name="batch_0001",
                    entity_key="zoom",
                    strict_case=False,
                    resolved_status="unknown",
                    base_list_status="unknown",
                    occurrence_count=1,
                    unit_count=1,
                    surface_forms=["zoom"],
                    example_unit_ids=["u1"],
                )
            ],
        )
        append_alphabetic_evidence(
            temp_path,
            [
                AlphabeticEvidence(
                    batch_name="batch_0001",
                    entity_key="zoom meeting",
                    strict_case=False,
                    resolved_status="unknown",
                    base_list_status="unknown",
                    occurrence_count=1,
                    unit_count=1,
                    surface_forms=["zoom meeting"],
                    example_unit_ids=["u2"],
                )
            ],
        )
        rows = [line for line in temp_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertIn("zoom meeting", rows[0])
        temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
