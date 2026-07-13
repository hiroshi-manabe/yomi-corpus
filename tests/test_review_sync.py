from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.document_review_state import (
    STATE_COMPLETE,
    STATE_FINAL_IN_REVIEW,
    STATE_FINAL_PENDING,
    STATE_FINAL_REVIEWED,
    STATE_STRONG_PENDING,
)
from yomi_corpus.review_sync import (
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    build_bulk_review_refill_plan,
    current_document_queue_summary,
    has_strong_pending_documents,
    maintain_strong_repair_for_reviewed_documents,
)
from yomi_corpus.pipeline import PipelineWorkspace


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

    def test_current_document_queue_summary_reports_visible_queue_counts(self) -> None:
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
                        "state": STATE_FINAL_PENDING,
                        "strong_repair_item_count": 0,
                    },
                    {
                        "doc_id": "doc2",
                        "doc_seq": 2,
                        "state": STATE_FINAL_IN_REVIEW,
                        "strong_repair_item_count": 0,
                    },
                    {
                        "doc_id": "doc3",
                        "doc_seq": 3,
                        "state": STATE_FINAL_REVIEWED,
                        "strong_repair_item_count": 0,
                    },
                    {
                        "doc_id": "doc4",
                        "doc_seq": 4,
                        "state": STATE_STRONG_PENDING,
                        "strong_repair_item_count": 1,
                    },
                    {
                        "doc_id": "doc5",
                        "doc_seq": 5,
                        "state": STATE_COMPLETE,
                        "strong_repair_item_count": 0,
                    },
                ],
            )

            summary = current_document_queue_summary(root=root, batch_name=batch_name)

            self.assertIsNotNone(summary)
            assert summary is not None
            self.assertEqual(summary["queue_counts"]["bulk_review_selectable"], 2)
            self.assertEqual(summary["queue_counts"]["bulk_review_submitted"], 1)
            self.assertEqual(summary["queue_counts"]["escalated_repair_selectable"], 1)
            self.assertEqual(summary["queue_counts"]["resolved"], 1)
            self.assertEqual(summary["pool_counts"]["bulk-ready"], 2)
            self.assertEqual(summary["pool_counts"]["bulk-submitted"], 1)
            self.assertEqual(summary["pool_counts"]["escalated-ready"], 1)
            self.assertEqual(summary["pool_counts"]["resolved"], 1)

    def test_bulk_review_refill_plan_reports_deficit_without_preparing(self) -> None:
        plan = build_bulk_review_refill_plan(
            document_queue_summary={"pool_counts": {"bulk-ready": 12}},
            target_ready_docs=50,
            pass_limit=10,
        )

        self.assertTrue(plan["enabled"])
        self.assertEqual(plan["status"], "needs_refill")
        self.assertEqual(plan["bulk_review_ready_docs"], 12)
        self.assertEqual(plan["deficit"], 38)
        self.assertEqual(plan["planned_prepare_documents"], 10)
        self.assertFalse(plan["will_prepare"])

    def test_bulk_review_refill_plan_is_disabled_by_default(self) -> None:
        plan = build_bulk_review_refill_plan(
            document_queue_summary={"pool_counts": {"bulk-ready": 12}},
            target_ready_docs=0,
            pass_limit=10,
        )

        self.assertFalse(plan["enabled"])
        self.assertEqual(plan["status"], "disabled")
        self.assertEqual(plan["planned_prepare_documents"], 0)

    def test_bulk_review_refill_plan_is_satisfied_when_ready_count_meets_target(self) -> None:
        plan = build_bulk_review_refill_plan(
            document_queue_summary={"pool_counts": {"bulk-ready": 50}},
            target_ready_docs=50,
            pass_limit=10,
        )

        self.assertEqual(plan["status"], "satisfied")
        self.assertEqual(plan["deficit"], 0)
        self.assertEqual(plan["planned_prepare_documents"], 0)

    def test_preview_next_source_documents_uses_prepare_cursor_without_mutating_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_demo_dataset(root, document_count=5)
            workspace = PipelineWorkspace(root)
            workspace.prepare_next_batch(
                track_name="dev",
                target_documents=2,
                dataset_config_path="config/datasets/demo.toml",
            )
            ledger_path = root / "data" / "pipeline" / "document_ledger" / "dev.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["documents"].append(
                {
                    "doc_id": "demo:0000000003",
                    "track_doc_seq": 3,
                    "dataset_name": "demo",
                    "dataset_source_path": str(root / "source.jsonl.gz"),
                    "source_line_no": 3,
                    "first_batch_name": "partial_batch",
                }
            )
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
            before_ledger = (
                root / "data" / "pipeline" / "document_ledger" / "dev.json"
            ).read_text(encoding="utf-8")

            preview = workspace.preview_next_source_documents(
                track_name="dev",
                target_documents=2,
                dataset_config_path="config/datasets/demo.toml",
            )

            self.assertEqual(preview["skip_source_line_no"], 2)
            self.assertEqual(
                [
                    (row["doc_id"], row["track_doc_seq"], row["source_line_no"])
                    for row in preview["selected_documents"]
                ],
                [
                    ("demo:0000000004", 4, 4),
                    ("demo:0000000005", 5, 5),
                ],
            )
            after_ledger = (
                root / "data" / "pipeline" / "document_ledger" / "dev.json"
            ).read_text(encoding="utf-8")
            self.assertEqual(after_ledger, before_ledger)


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


def write_demo_dataset(root: Path, *, document_count: int) -> None:
    config_dir = root / "config" / "datasets"
    config_dir.mkdir(parents=True, exist_ok=True)
    source_path = root / "source.jsonl.gz"
    with gzip.open(source_path, "wt", encoding="utf-8") as handle:
        for index in range(1, document_count + 1):
            handle.write(
                json.dumps(
                    {
                        "text": f"文書{index}です。",
                        "source_file": "source.jsonl.gz",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    (config_dir / "demo.toml").write_text(
        f'name = "demo"\nsource_path = "{source_path}"\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
