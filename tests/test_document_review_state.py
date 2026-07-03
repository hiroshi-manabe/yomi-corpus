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
    build_initial_document_review_state,
    load_document_review_state,
    mark_document_review_state_finalized,
    update_document_review_state_after_final_review,
    update_document_review_state_after_strong_queue,
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
