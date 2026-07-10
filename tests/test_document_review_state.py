from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.document_review_state import (
    STATE_COMPLETE,
    STATE_FINAL_IN_REVIEW,
    STATE_FINAL_PENDING,
    STATE_FINAL_REVIEWED,
    STATE_SKIPPED,
    STATE_STRONG_PENDING,
    STATE_STRONG_REVIEWED,
    WORKFLOW_STATE_BULK_REVIEW,
    WORKFLOW_STATE_BULK_SUBMITTED,
    WORKFLOW_STATE_ESCALATED_REPAIR,
    WORKFLOW_STATE_ESCALATED_SUBMITTED,
    WORKFLOW_STATE_RESOLVED,
    build_initial_document_review_state,
    document_review_queue_summary,
    document_workflow_queue_stage,
    document_workflow_state,
    load_document_review_state,
    mark_document_review_state_finalized,
    update_document_review_state_after_final_review,
    update_document_review_state_after_strong_queue,
    update_document_review_state_after_strong_review,
    write_document_review_state,
)


class DocumentReviewStateTests(unittest.TestCase):
    def test_initial_state_groups_units_by_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            units = Path(tmp) / "units.jsonl"
            write_jsonl(
                units,
                [
                    {"doc_id": "doc1", "unit_id": "u1"},
                    {"doc_id": "doc1", "unit_id": "u2"},
                    {"doc_id": "doc2", "unit_id": "u3"},
                ],
            )

            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )

            self.assertEqual(state["summary"]["document_count"], 2)
            self.assertEqual(state["summary"]["state_counts"][STATE_FINAL_PENDING], 2)
            self.assertEqual(state["summary"]["queue_counts"]["bulk_review_selectable"], 2)
            self.assertEqual(state["documents"][0]["doc_id"], "doc1")
            self.assertEqual(state["documents"][0]["unit_count"], 2)
            self.assertEqual(state["documents"][1]["doc_seq"], 2)

    def test_final_review_update_marks_reviewed_skipped_and_partial_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            reviewed = root / "reviewed.jsonl"
            write_jsonl(
                units,
                [
                    {"doc_id": "doc1", "unit_id": "u1"},
                    {"doc_id": "doc1", "unit_id": "u2"},
                    {"doc_id": "doc2", "unit_id": "u3"},
                    {"doc_id": "doc3", "unit_id": "u4"},
                ],
            )
            write_jsonl(
                reviewed,
                [
                    reviewed_unit("doc1", "u1"),
                    reviewed_unit("doc1", "u2"),
                    reviewed_unit("doc2", "u3", skip=True),
                    {"doc_id": "doc3", "unit_id": "u4"},
                ],
            )

            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )
            state = update_document_review_state_after_final_review(
                state=state,
                reviewed_units_jsonl=reviewed,
            )

            states = {row["doc_id"]: row["state"] for row in state["documents"]}
            self.assertEqual(states["doc1"], STATE_FINAL_REVIEWED)
            self.assertEqual(states["doc2"], STATE_SKIPPED)
            self.assertEqual(states["doc3"], STATE_FINAL_PENDING)

    def test_final_review_update_marks_partial_document_in_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            reviewed = root / "reviewed.jsonl"
            write_jsonl(
                units,
                [
                    {"doc_id": "doc1", "unit_id": "u1"},
                    {"doc_id": "doc1", "unit_id": "u2"},
                ],
            )
            write_jsonl(reviewed, [reviewed_unit("doc1", "u1"), {"doc_id": "doc1", "unit_id": "u2"}])
            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )

            state = update_document_review_state_after_final_review(
                state=state,
                reviewed_units_jsonl=reviewed,
            )

            self.assertEqual(state["documents"][0]["state"], STATE_FINAL_IN_REVIEW)

    def test_strong_queue_update_marks_pending_or_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            reviewed = root / "reviewed.jsonl"
            queue = root / "queue.jsonl"
            write_jsonl(
                units,
                [
                    {"doc_id": "doc1", "unit_id": "u1"},
                    {"doc_id": "doc2", "unit_id": "u2"},
                ],
            )
            write_jsonl(reviewed, [reviewed_unit("doc1", "u1"), reviewed_unit("doc2", "u2")])
            write_jsonl(queue, [{"doc_id": "doc2", "unit_id": "u2", "item_id": "q1"}])
            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )
            state = update_document_review_state_after_final_review(
                state=state,
                reviewed_units_jsonl=reviewed,
            )

            state = update_document_review_state_after_strong_queue(
                state=state,
                queue_jsonl=queue,
            )

            states = {row["doc_id"]: row["state"] for row in state["documents"]}
            self.assertEqual(states["doc1"], STATE_COMPLETE)
            self.assertEqual(states["doc2"], STATE_STRONG_PENDING)
            self.assertEqual(state["summary"]["queue_counts"]["bulk_review_selectable"], 0)
            self.assertEqual(state["summary"]["queue_counts"]["bulk_review_submitted"], 0)
            self.assertEqual(state["summary"]["queue_counts"]["escalated_repair_selectable"], 1)
            self.assertEqual(state["summary"]["queue_counts"]["resolved"], 1)

    def test_strong_review_update_marks_reviewed_or_partial_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            reviewed = root / "reviewed.jsonl"
            queue = root / "queue.jsonl"
            pack = root / "pack.json"
            submission = root / "submission.json"
            write_jsonl(
                units,
                [
                    {"doc_id": "doc1", "unit_id": "u1"},
                    {"doc_id": "doc2", "unit_id": "u2"},
                ],
            )
            write_jsonl(reviewed, [reviewed_unit("doc1", "u1"), reviewed_unit("doc2", "u2")])
            write_jsonl(
                queue,
                [
                    {"doc_id": "doc1", "unit_id": "u1", "item_id": "q1"},
                    {"doc_id": "doc2", "unit_id": "u2", "item_id": "q2"},
                ],
            )
            pack.write_text(
                json.dumps(
                    {
                        "pack_id": "strong_pack",
                        "items": [
                            {"item_id": "q1", "seq": 1, "doc_id": "doc1"},
                            {"item_id": "q2", "seq": 2, "doc_id": "doc2"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            submission.write_text(
                json.dumps(
                    {
                        "submission_type": "review_patch",
                        "review_stage": "yomi_strong_repair_review",
                        "pack_id": "strong_pack",
                        "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                        "overrides": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )
            state = update_document_review_state_after_final_review(
                state=state,
                reviewed_units_jsonl=reviewed,
            )
            state = update_document_review_state_after_strong_queue(
                state=state,
                queue_jsonl=queue,
            )

            state = update_document_review_state_after_strong_review(
                state=state,
                pack_json=pack,
                review_summary={
                    "submission_paths": [str(submission)],
                    "rejected_items": [],
                    "manual_segment_overrides": {"invalid_items": 0},
                },
            )

            states = {row["doc_id"]: row["state"] for row in state["documents"]}
            self.assertEqual(states["doc1"], STATE_STRONG_REVIEWED)
            self.assertEqual(states["doc2"], STATE_STRONG_PENDING)

    def test_strong_review_counts_regions_for_grouped_sentence_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            reviewed = root / "reviewed.jsonl"
            queue = root / "queue.jsonl"
            pack = root / "pack.json"
            submission = root / "submission.json"
            write_jsonl(units, [{"doc_id": "doc1", "unit_id": "u1"}])
            write_jsonl(reviewed, [reviewed_unit("doc1", "u1")])
            write_jsonl(
                queue,
                [
                    {"doc_id": "doc1", "unit_id": "u1", "item_id": "q1"},
                    {"doc_id": "doc1", "unit_id": "u1", "item_id": "q2"},
                    {"doc_id": "doc1", "unit_id": "u1", "item_id": "q3"},
                    {"doc_id": "doc1", "unit_id": "u1", "item_id": "q4"},
                    {"doc_id": "doc1", "unit_id": "u1", "item_id": "q5"},
                ],
            )
            pack.write_text(
                json.dumps(
                    {
                        "pack_id": "strong_pack",
                        "items": [
                            {"item_id": "u1::strong_repair", "seq": 1, "doc_id": "doc1", "region_count": 5}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            submission.write_text(
                json.dumps(
                    {
                        "submission_type": "review_patch",
                        "review_stage": "yomi_strong_repair_review",
                        "pack_id": "strong_pack",
                        "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                        "overrides": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )
            state = update_document_review_state_after_final_review(
                state=state,
                reviewed_units_jsonl=reviewed,
            )
            state = update_document_review_state_after_strong_queue(
                state=state,
                queue_jsonl=queue,
            )

            state = update_document_review_state_after_strong_review(
                state=state,
                pack_json=pack,
                review_summary={
                    "submission_paths": [str(submission)],
                    "rejected_items": [],
                    "manual_segment_overrides": {"invalid_items": 0},
                },
            )

            self.assertEqual(state["documents"][0]["strong_repair_item_count"], 5)
            self.assertEqual(state["documents"][0]["state"], STATE_STRONG_REVIEWED)

    def test_finalized_state_marks_non_skipped_documents_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            reviewed = root / "reviewed.jsonl"
            write_jsonl(
                units,
                [
                    {"doc_id": "doc1", "unit_id": "u1"},
                    {"doc_id": "doc2", "unit_id": "u2"},
                ],
            )
            write_jsonl(reviewed, [reviewed_unit("doc1", "u1"), reviewed_unit("doc2", "u2", skip=True)])
            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )
            state = update_document_review_state_after_final_review(
                state=state,
                reviewed_units_jsonl=reviewed,
            )

            state = mark_document_review_state_finalized(state)

            states = {row["doc_id"]: row["state"] for row in state["documents"]}
            self.assertEqual(states["doc1"], STATE_COMPLETE)
            self.assertEqual(states["doc2"], STATE_SKIPPED)

    def test_state_round_trips_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "units.jsonl"
            state_path = root / "state.json"
            write_jsonl(units, [{"doc_id": "doc1", "unit_id": "u1"}])
            state = build_initial_document_review_state(
                units_jsonl=units,
                batch_name="dev_batch_0001",
                track_name="dev",
            )

            write_document_review_state(state_path, state)
            loaded = load_document_review_state(state_path)

            self.assertEqual(loaded["documents"][0]["doc_id"], "doc1")

    def test_document_review_queue_summary_reports_refill_relevant_counts(self) -> None:
        state = {
            "schema_version": 1,
            "batch_name": "dev_batch_0001",
            "track_name": "dev",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "documents": [
                {"doc_id": "doc1", "state": STATE_FINAL_PENDING},
                {"doc_id": "doc2", "state": STATE_FINAL_IN_REVIEW},
                {"doc_id": "doc3", "state": STATE_FINAL_REVIEWED},
                {"doc_id": "doc4", "state": STATE_STRONG_PENDING},
                {"doc_id": "doc5", "state": STATE_STRONG_REVIEWED},
                {"doc_id": "doc6", "state": STATE_COMPLETE},
                {"doc_id": "doc7", "state": STATE_SKIPPED},
            ],
        }

        summary = document_review_queue_summary(state)

        self.assertEqual(summary["queue_counts"]["bulk_review_selectable"], 2)
        self.assertEqual(summary["queue_counts"]["bulk_review_submitted"], 1)
        self.assertEqual(summary["queue_counts"]["escalated_repair_selectable"], 1)
        self.assertEqual(summary["queue_counts"]["escalated_repair_submitted"], 1)
        self.assertEqual(summary["queue_counts"]["resolved"], 2)

    def test_document_workflow_state_maps_pipeline_states_to_ui_buckets(self) -> None:
        self.assertEqual(document_workflow_state(STATE_FINAL_PENDING), WORKFLOW_STATE_BULK_REVIEW)
        self.assertEqual(document_workflow_state(STATE_FINAL_IN_REVIEW), WORKFLOW_STATE_BULK_REVIEW)
        self.assertEqual(document_workflow_state(STATE_FINAL_REVIEWED), WORKFLOW_STATE_BULK_SUBMITTED)
        self.assertEqual(document_workflow_state(STATE_STRONG_PENDING), WORKFLOW_STATE_ESCALATED_REPAIR)
        self.assertEqual(document_workflow_state(STATE_STRONG_REVIEWED), WORKFLOW_STATE_ESCALATED_SUBMITTED)
        self.assertEqual(document_workflow_state(STATE_COMPLETE), WORKFLOW_STATE_RESOLVED)
        self.assertEqual(document_workflow_state(STATE_SKIPPED), WORKFLOW_STATE_RESOLVED)

    def test_document_workflow_queue_stage_maps_submitted_to_original_queue(self) -> None:
        self.assertEqual(document_workflow_queue_stage(STATE_FINAL_REVIEWED), "yomi_final_review")
        self.assertEqual(document_workflow_queue_stage(STATE_STRONG_REVIEWED), "yomi_strong_repair_review")
        self.assertIsNone(document_workflow_queue_stage(STATE_COMPLETE))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def reviewed_unit(doc_id: str, unit_id: str, *, skip: bool = False) -> dict:
    return {
        "doc_id": doc_id,
        "unit_id": unit_id,
        "analysis": {
            "human_review": {
                "yomi_final": {
                    "reviewed": True,
                    "skip": skip,
                }
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
