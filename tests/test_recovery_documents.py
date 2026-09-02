from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.recovery_documents import (
    build_application_ledger,
    build_recovery_units,
    insertion_only_diff,
    pack_recovery_units,
    RestoredChunk,
    validate_restored_chunks,
)


def record(stable_id: str, text: str) -> dict[str, object]:
    return {"text": text, "meta": {"docId": stable_id}}


class RecoveryDocumentTests(unittest.TestCase):
    def test_insertion_only_diff_reconstructs_new_text(self) -> None:
        chunks = insertion_only_diff("前です。後です。", "前です。復元です。後です。")

        self.assertEqual([chunk.text for chunk in chunks], ["復元です。"])
        self.assertEqual(chunks[0].old_start, 4)

    def test_replacement_is_a_conflict(self) -> None:
        with self.assertRaisesRegex(ValueError, "not an insertion-only change"):
            insertion_only_diff("前です。", "別です。")

    def test_units_have_stable_destination_and_anchors(self) -> None:
        destination = {"doc_id": "doc-7", "track_doc_seq": 7, "source_line_no": 11}

        first = build_recovery_units(
            campaign_id="home_tag_v1",
            old_record=record("source-1", "前です。後です。"),
            new_record=record("source-1", "前です。復元一。復元二。後です。"),
            destination=destination,
        )
        second = build_recovery_units(
            campaign_id="home_tag_v1",
            old_record=record("source-1", "前です。後です。"),
            new_record=record("source-1", "前です。復元一。復元二。後です。"),
            destination=destination,
        )

        self.assertEqual(first, second)
        self.assertEqual([row["text"] for row in first], ["復元一。", "復元二。"])
        self.assertEqual(first[0]["destination_track_doc_seq"], 7)
        self.assertEqual(first[0]["preceding_anchor"]["text"], "前です。")
        self.assertEqual(first[0]["following_anchor"]["text"], "後です。")

    def test_packer_uses_character_target_and_unit_cap(self) -> None:
        rows = [
            {
                "destination_track_doc_seq": 1,
                "new_char_start": index,
                "recovery_unit_id": f"u{index}",
                "text": "あ" * 300,
            }
            for index in range(7)
        ]

        documents = pack_recovery_units(
            rows,
            campaign_id="test",
            target_chars=900,
            min_chars=600,
            max_units=32,
        )

        self.assertEqual([row["character_count"] for row in documents], [900, 900, 300])
        self.assertEqual([row["unit_count"] for row in documents], [3, 3, 1])

    def test_long_unit_is_not_split(self) -> None:
        rows = [
            {
                "destination_track_doc_seq": 1,
                "new_char_start": 0,
                "recovery_unit_id": "long",
                "text": "あ" * 1200,
            }
        ]

        documents = pack_recovery_units(rows, campaign_id="test")

        self.assertEqual(documents[0]["character_count"], 1200)
        self.assertEqual(documents[0]["recovery_unit_ids"], ["long"])

    def test_prepare_recovery_batch_does_not_replace_current_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign_dir = root / "campaign"
            campaign_dir.mkdir()
            (campaign_dir / "campaign.json").write_text(
                json.dumps({"campaign_id": "cleaner_v1"}), encoding="utf-8"
            )
            unit = {
                "recovery_unit_id": "recovery:cleaner_v1:source:0:hash",
                "destination_source_line_no": 7,
                "destination_track_doc_seq": 3,
                "destination_doc_id": "source-doc",
                "new_char_start": 10,
                "text": "復元した文です。",
            }
            (campaign_dir / "recovery_units.jsonl").write_text(
                json.dumps(unit, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            document = {
                "recovery_document_id": "recovery:cleaner_v1:d000001",
                "recovery_document_seq": 1,
                "recovery_unit_ids": [unit["recovery_unit_id"]],
            }
            (campaign_dir / "recovery_documents.jsonl").write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            workspace = PipelineWorkspace(root)
            current = workspace.load_track_state("dev")

            summary = workspace.prepare_recovery_batch(campaign_dir=campaign_dir)

            self.assertEqual(summary["batch_kind"], "recovery")
            self.assertEqual(workspace.load_track_state("dev").current_batch_name, current.current_batch_name)
            batch_state = workspace.load_batch_state(str(summary["batch_name"]))
            self.assertEqual(batch_state.batch_kind, "recovery")
            prepared = json.loads(
                (root / "data/units/dev_recovery_cleaner_v1/units.jsonl")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(prepared["text"], unit["text"])
            self.assertEqual(prepared["recovery"]["destination_doc_id"], "source-doc")

    def test_application_ledger_preserves_terminal_dispositions(self) -> None:
        provenance = {
            "campaign_id": "cleaner_v1",
            "recovery_unit_id": "recovery-unit",
            "destination_doc_id": "destination",
            "destination_track_doc_seq": 4,
            "destination_source_line_no": 8,
            "new_char_start": 10,
            "new_char_end": 15,
            "text": "復元文。",
            "text_sha256": "hash",
            "preceding_anchor": None,
            "following_anchor": None,
        }
        base = {
            "unit_id": "review-unit",
            "text": "復元文。",
            "recovery": provenance,
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "tokens": [["復元", "フクゲン"], ["文", "ブン"], ["。", "。"]]
                    }
                },
                "human_review": {"yomi_final": {"skip": False}},
            },
        }

        accepted = build_application_ledger([base])[0]
        skipped = build_application_ledger(
            [{**base, "analysis": {"human_review": {"yomi_final": {"skip": True}}}}]
        )[0]
        excluded = build_application_ledger([{**base, "excluded": True}])[0]

        self.assertEqual(accepted["state"], "ready_to_apply")
        self.assertEqual(
            accepted["final_yomi_tokens"],
            [["復元", "フクゲン"], ["文", "ブン"], ["。", "。"]],
        )
        self.assertEqual(skipped["state"], "skipped")
        self.assertEqual(excluded["state"], "excluded")

    def test_manual_alignment_override_must_reconstruct_new_text(self) -> None:
        valid = [RestoredChunk(old_start=4, new_start=-1, new_end=-1, text="復元です。")]

        validate_restored_chunks("前です。後です。", "前です。復元です。後です。", valid)
        with self.assertRaisesRegex(ValueError, "does not reconstruct"):
            validate_restored_chunks(
                "前です。後です。",
                "前です。復元です。後です。",
                [RestoredChunk(old_start=4, new_start=-1, new_end=-1, text="別です。")],
            )


if __name__ == "__main__":
    unittest.main()
