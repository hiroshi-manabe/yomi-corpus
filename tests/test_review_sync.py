from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.document_review_state import STATE_FINAL_PENDING, STATE_STRONG_PENDING
from yomi_corpus.review_sync import (
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    has_strong_pending_documents,
    maintain_strong_repair_for_reviewed_documents,
)


class FakeWorkspace:
    def __init__(self, queued: int = 1) -> None:
        self.queued = queued
        self.calls: list[str] = []

    def _queue_yomi_strong_repair(self, batch_name: str) -> dict[str, object]:
        self.calls.append(f"queue:{batch_name}")
        return {"artifacts": {"yomi_strong_repair_queued": str(self.queued)}}

    def _run_yomi_strong_repair(self, batch_name: str) -> dict[str, object]:
        self.calls.append(f"run:{batch_name}")
        return {"artifacts": {"ran": "true"}}

    def _apply_strong_repair_review(self, batch_name: str) -> dict[str, object]:
        self.calls.append(f"review:{batch_name}")
        return {"artifacts": {"review": "true"}}


class ReviewSyncTests(unittest.TestCase):
    def test_strong_pending_documents_allow_partial_repair_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_name = "dev_batch_0001"
            write_reviewed_units(root, batch_name)
            write_document_state(
                root,
                batch_name,
                [
                    {
                        "doc_id": "doc1",
                        "doc_seq": 1,
                        "state": STATE_STRONG_PENDING,
                        "strong_repair_item_count": 2,
                    },
                    {
                        "doc_id": "doc2",
                        "doc_seq": 2,
                        "state": STATE_FINAL_PENDING,
                        "strong_repair_item_count": 0,
                    },
                ],
            )
            workspace = FakeWorkspace(queued=2)

            results = maintain_strong_repair_for_reviewed_documents(
                root=root,
                workspace=workspace,  # type: ignore[arg-type]
                batch_name=batch_name,
                allow_queue=False,
            )

            self.assertEqual(workspace.calls, [f"queue:{batch_name}", f"run:{batch_name}"])
            self.assertEqual(results[0]["attempted_stage"], STAGE_YOMI_STRONG_REPAIR_QUEUED)
            self.assertEqual(results[1]["attempted_stage"], STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED)

    def test_no_strong_pending_documents_do_not_queue_without_allow_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_name = "dev_batch_0001"
            write_reviewed_units(root, batch_name)
            write_document_state(
                root,
                batch_name,
                [
                    {
                        "doc_id": "doc1",
                        "doc_seq": 1,
                        "state": STATE_FINAL_PENDING,
                        "strong_repair_item_count": 0,
                    }
                ],
            )
            workspace = FakeWorkspace(queued=1)

            results = maintain_strong_repair_for_reviewed_documents(
                root=root,
                workspace=workspace,  # type: ignore[arg-type]
                batch_name=batch_name,
                allow_queue=False,
            )

            self.assertEqual(workspace.calls, [])
            self.assertEqual(results, [])

    def test_has_strong_pending_documents_requires_pending_state_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_name = "dev_batch_0001"
            write_document_state(
                root,
                batch_name,
                [
                    {
                        "doc_id": "doc1",
                        "doc_seq": 1,
                        "state": STATE_STRONG_PENDING,
                        "strong_repair_item_count": 0,
                    }
                ],
            )
            self.assertFalse(has_strong_pending_documents(root=root, batch_name=batch_name))

            write_document_state(
                root,
                batch_name,
                [
                    {
                        "doc_id": "doc1",
                        "doc_seq": 1,
                        "state": STATE_STRONG_PENDING,
                        "strong_repair_item_count": 1,
                    }
                ],
            )
            self.assertTrue(has_strong_pending_documents(root=root, batch_name=batch_name))


def write_reviewed_units(root: Path, batch_name: str) -> None:
    batch_dir = root / "data" / "units" / batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "units.yomi.reviewed.jsonl").write_text(
        json.dumps({"doc_id": "doc1", "unit_id": "u1"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_document_state(root: Path, batch_name: str, documents: list[dict[str, object]]) -> None:
    state_path = root / "data" / "pipeline" / "document_states" / f"{batch_name}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "batch_name": batch_name,
                "track_name": "dev",
                "documents": documents,
                "summary": {"document_count": len(documents), "state_counts": {}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
