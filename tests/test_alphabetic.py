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
    project_alphabetic_scope,
    project_minor_alphabetic_judgment,
)
from yomi_corpus.alphabetic_state import (
    AlphabeticDecision,
    AlphabeticEvidence,
    append_alphabetic_evidence,
    decision_status_to_resolved_status,
    load_alphabetic_decisions,
    upsert_alphabetic_decision,
)
from yomi_corpus.yomi.types import SudachiToken


def sudachi_tokens(*surfaces: str) -> list[SudachiToken]:
    return [
        SudachiToken(
            surface=surface,
            pos="名詞,普通名詞,一般,*,*,*",
            dictionary_form=surface,
            normalized_form=surface,
            reading=surface,
        )
        for surface in surfaces
    ]


class AlphabeticPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_alphabetic_config("config/alphabetic/default.toml")

    def occurrences(self, unit: dict, *surfaces: str):
        return build_occurrences_for_unit(unit, self.config, sudachi_tokens(*surfaces))

    def entities(self, text: str, *surfaces: str):
        return extract_alphabetic_entities(text, self.config, sudachi_tokens(*surfaces))

    def test_occurrence_builder_uses_case_insensitive_key_for_single_long_entity(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "ANDROIDを使っています。",
        }
        occurrences = self.occurrences(unit, "ANDROID")
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
        occurrences = self.occurrences(unit, "AI")
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].entity_key, "AI")
        self.assertTrue(occurrences[0].strict_case)
        self.assertEqual(occurrences[0].base_list_status, "short_uppercase")

    def test_short_uppercase_initialisms_are_deterministically_resolved(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "TシャツとPSIについて。",
        }
        occurrences = self.occurrences(unit, "T", "PSI")
        self.assertEqual([row.entity_key for row in occurrences], ["T", "PSI"])
        self.assertTrue(
            all(row.base_list_status == "short_uppercase" for row in occurrences)
        )
        self.assertTrue(
            all(row.resolved_status == "short_uppercase" for row in occurrences)
        )

        judgment = project_minor_alphabetic_judgment(occurrences)
        self.assertFalse(judgment.value)
        self.assertTrue(judgment.certain)
        self.assertIn("short_uppercase_initialism_exception", judgment.signals)

    def test_fullwidth_short_uppercase_initialism_is_deterministically_resolved(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "ＰＳＩについて。",
        }
        occurrences = self.occurrences(unit, "ＰＳＩ")

        self.assertEqual(occurrences[0].entity_key, "PSI")
        self.assertEqual(occurrences[0].base_list_status, "short_uppercase")

    def test_short_lowercase_and_mixed_entities_still_require_judgment(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "psiとPsIとGI9について。",
        }
        occurrences = self.occurrences(unit, "psi", "PsI", "GI9")

        self.assertEqual([row.base_list_status for row in occurrences], ["unknown"] * 3)

    def test_global_decision_cannot_override_short_uppercase_initialism(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "PSIについて。",
        }
        occurrences = apply_global_decisions(
            self.occurrences(unit, "PSI"), {"PSI": "blacklist"}
        )

        self.assertEqual(occurrences[0].resolved_status, "short_uppercase")
        self.assertEqual(project_alphabetic_scope(occurrences)["status"], "in_scope")

    def test_numeric_measurements_are_deterministically_resolved(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "1kgから1.5kg増え、30km歩いた。",
        }
        occurrences = self.occurrences(
            unit, "1", "kg", "1", ".", "5", "kg", "3", "0", "km"
        )

        self.assertEqual([occ.entity_text for occ in occurrences], ["1kg", "1.5kg", "30km"])
        self.assertTrue(all(occ.base_list_status == "measurement" for occ in occurrences))
        judgment = project_minor_alphabetic_judgment(occurrences)
        self.assertFalse(judgment.value)
        self.assertTrue(judgment.certain)
        self.assertIn("measurement_exception", judgment.signals)

    def test_measurement_exception_uses_an_explicit_unit_list(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "CLA180とDay2020を比較する。",
        }
        occurrences = self.occurrences(
            unit, "CLA", "1", "8", "0", "Day", "2", "0", "2", "0"
        )

        self.assertEqual([occ.base_list_status for occ in occurrences], ["unknown", "unknown"])

    def test_global_decision_does_not_override_measurement_exception(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "50gを量る。",
        }
        occurrences = self.occurrences(unit, "5", "0", "g")
        overridden = apply_global_decisions(occurrences, {"50g": "blacklist"})

        self.assertEqual(overridden[0].resolved_status, "measurement")
        scope = project_alphabetic_scope(overridden)
        self.assertEqual(scope["status"], "in_scope")
        self.assertEqual(scope["in_scope"][0]["source"], "deterministic_measurement")

    def test_entity_extractor_splits_space_spanning_sudachi_token(self) -> None:
        entities = self.entities("Led Zeppelinが好きです。", "Led Zeppelin")
        self.assertEqual([entity.text for entity in entities], ["Led", "Zeppelin"])
        self.assertEqual([entity.normalized for entity in entities], ["led", "zeppelin"])

    def test_entity_extractor_keeps_alphanumeric_token_with_letters(self) -> None:
        entities = self.entities("V6が好きです。", "V6")
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "V6")
        self.assertEqual(entities[0].normalized, "v6")
        self.assertEqual(entities[0].component_texts, ["V6"])

    def test_entity_extractor_uses_sudachi_boundary_for_trailing_number(self) -> None:
        entities = self.entities("BGM8選です。", "BGM", "8", "選")
        self.assertEqual([entity.text for entity in entities], ["BGM"])

    def test_entity_extractor_keeps_punctuation_inside_sudachi_entity(self) -> None:
        entities = self.entities("ZE:Aの曲です。", "ZE:A")
        self.assertEqual([entity.text for entity in entities], ["ZE:A"])
        self.assertEqual(entities[0].normalized, "ze:a")

    def test_entity_extractor_extracts_alphabetic_part_of_mixed_script_token(self) -> None:
        entities = self.entities("AB型です。", "AB型")
        self.assertEqual([entity.text for entity in entities], ["AB"])

    def test_entity_extractor_normalizes_fullwidth_long_entity_key(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "ＩＯＮＤ大学について調べました。",
        }
        occurrences = self.occurrences(unit, "ＩＯＮＤ")
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].entity_text, "ＩＯＮＤ")
        self.assertEqual(occurrences[0].entity_key, "iond")
        self.assertFalse(occurrences[0].strict_case)

    def test_fullwidth_strict_case_entity_uses_ascii_key(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "ＡＩを使っています。",
        }
        occurrences = self.occurrences(unit, "ＡＩ")
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].entity_text, "ＡＩ")
        self.assertEqual(occurrences[0].entity_key, "AI")
        self.assertTrue(occurrences[0].strict_case)

    def test_entity_extractor_does_not_absorb_standalone_number(self) -> None:
        entities = self.entities("iPhone 16が発売されました。", "iPhone", " ", "1", "6")
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "iPhone")
        self.assertEqual(entities[0].normalized, "iphone")

    def test_entity_extractor_preserves_sudachi_boundaries_around_hyphen(self) -> None:
        entities = self.entities("Jean - Lucが来た。", "Jean", " ", "-", " ", "Luc")
        self.assertEqual([entity.text for entity in entities], ["Jean", "Luc"])

    def test_entity_extractor_splits_spaces_inside_sudachi_token(self) -> None:
        entities = self.entities("rock 'n' rollが好き。", "rock 'n' roll")
        self.assertEqual([entity.text for entity in entities], ["rock", "n", "roll"])

    def test_entity_extractor_splits_unicode_title_at_spaces(self) -> None:
        entities = self.entities(
            "『都会のアリス』（Alice in den Städten/1974）。",
            "Alice in den Städten",
            "/",
            "1",
            "9",
            "7",
            "4",
        )
        self.assertEqual(
            [entity.text for entity in entities],
            ["Alice", "in", "den", "Städten"],
        )

    def test_entity_extractor_accepts_decomposed_combining_marks(self) -> None:
        entities = self.entities("Cafe\u0301 Noirを見た。", "Cafe\u0301 Noir")
        self.assertEqual([entity.text for entity in entities], ["Cafe\u0301", "Noir"])
        self.assertEqual([entity.normalized for entity in entities], ["café", "noir"])

    def test_entity_extractor_accepts_greek_and_cyrillic_cased_letters(self) -> None:
        entities = self.entities("ΑθήναとМоскваを訪れた。", "Αθήνα", "Москва")
        self.assertEqual([entity.text for entity in entities], ["Αθήνα", "Москва"])

    def test_entity_extractor_keeps_dotted_name_together(self) -> None:
        entities = self.entities("国民的バンドであるMr.Children。", "Mr.Children")
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].text, "Mr.Children")
        self.assertEqual(entities[0].normalized, "mr.children")
        self.assertEqual(entities[0].component_texts, ["Mr.Children"])

    def test_single_greek_letter_requires_scope_judgment(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "プリウスαを見ました。",
        }
        occurrences = self.occurrences(unit, "α")
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].entity_text, "α")
        self.assertEqual(occurrences[0].base_list_status, "unknown")

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
        occurrences = self.occurrences(unit, "Concerts")
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
        occurrences = self.occurrences(unit_a, "Android") + self.occurrences(
            unit_b, "ANDROID"
        )
        token_types = aggregate_occurrences(occurrences)
        self.assertEqual(len(token_types), 1)
        self.assertEqual(token_types[0].entity_key, "android")
        self.assertEqual(token_types[0].occurrence_count, 2)
        self.assertEqual(token_types[0].unit_count, 2)

    def test_aggregation_groups_space_split_entities_case_insensitively(self) -> None:
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
        occurrences = self.occurrences(unit_a, "Led Zeppelin") + self.occurrences(
            unit_b, "LED ZEPPELIN"
        )
        token_types = aggregate_occurrences(occurrences)
        self.assertEqual([row.entity_key for row in token_types], ["LED", "Led", "zeppelin"])
        self.assertEqual(token_types[-1].occurrence_count, 2)

    def test_global_decision_override_changes_projection(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "zoomで参加します。",
        }
        occurrences = self.occurrences(unit, "zoom")
        overridden = apply_global_decisions(occurrences, {"zoom": "whitelist"})
        judgment = project_minor_alphabetic_judgment(overridden)
        self.assertFalse(judgment.value)
        self.assertTrue(judgment.certain)
        self.assertIn("zoom", judgment.matches)

    def test_llm_decision_status_maps_to_legacy_resolved_status(self) -> None:
        self.assertEqual(decision_status_to_resolved_status("in_scope"), "whitelist")
        self.assertEqual(decision_status_to_resolved_status("out_of_scope"), "blacklist")
        self.assertEqual(decision_status_to_resolved_status("blacklist"), "blacklist")

    def test_alphabetic_scope_projects_out_of_scope_as_provisional_skip(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "zoomで参加します。",
        }
        decision = AlphabeticDecision(
            entity_key="zoom",
            strict_case=False,
            status="out_of_scope",
            source="llm",
            note="low-value foreign term",
        )
        occurrences = apply_global_decisions(
            self.occurrences(unit, "zoom"),
            {"zoom": decision_status_to_resolved_status(decision.status)},
        )

        scope = project_alphabetic_scope(occurrences, {"zoom": decision})

        self.assertTrue(scope["provisional_skip"])
        self.assertEqual(scope["status"], "provisional_skip")
        self.assertEqual(scope["reasons"][0]["entity_key"], "zoom")
        self.assertEqual(scope["reasons"][0]["source"], "llm")

    def test_fullwidth_iond_decision_projects_as_provisional_skip(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "イオンド大学（ＩＯＮＤ大学）について。",
        }
        decision = AlphabeticDecision(
            entity_key="iond",
            strict_case=False,
            status="out_of_scope",
            source="manual",
            note="diploma mill name",
        )
        occurrences = apply_global_decisions(
            self.occurrences(unit, "ＩＯＮＤ"),
            {"iond": decision_status_to_resolved_status(decision.status)},
        )

        scope = project_alphabetic_scope(occurrences, {"iond": decision})

        self.assertTrue(scope["provisional_skip"])
        self.assertEqual(scope["reasons"][0]["entity_key"], "iond")
        self.assertEqual(scope["reasons"][0]["entity_text"], "ＩＯＮＤ")

    def test_fullwidth_iond_static_blacklist_projects_as_provisional_skip(self) -> None:
        unit = {
            "doc_id": "d1",
            "unit_id": "d1:u0001",
            "unit_seq": 1,
            "text": "イオンド大学（ＩＯＮＤ大学）について。",
        }
        config = load_alphabetic_config("config/alphabetic/default.toml")
        occurrences = build_occurrences_for_unit(unit, config, sudachi_tokens("ＩＯＮＤ"))

        scope = project_alphabetic_scope(occurrences, {})

        self.assertTrue(scope["provisional_skip"])
        self.assertEqual(scope["reasons"][0]["entity_key"], "iond")
        self.assertEqual(scope["reasons"][0]["entity_text"], "ＩＯＮＤ")
        self.assertEqual(scope["reasons"][0]["source"], "static_blacklist")

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
