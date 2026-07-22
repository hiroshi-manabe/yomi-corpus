from __future__ import annotations

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.document_review_state import (
    STATE_COMPLETE,
    STATE_FINAL_IN_REVIEW,
    STATE_FINAL_PENDING,
    STATE_FINAL_REVIEWED,
    STATE_STRONG_PENDING,
)
from yomi_corpus.review_sync import (
    STAGE_FINAL_REVIEW_PREPARED,
    STAGE_SEQUENCE,
    STAGE_YOMI_FINALIZED,
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    ReviewSyncOptions,
    aggregate_document_queue_summary,
    build_decoder_refresh_plan,
    build_bulk_review_refill_plan,
    close_finalized_correction_issues,
    closable_issue_numbers,
    current_document_queue_summary,
    has_strong_pending_documents,
    load_review_sync_config,
    list_track_batches,
    maintain_strong_repair_for_reviewed_documents,
    request_decoder_model_refresh,
    reconcile_applied_final_review_issues,
    review_sync_lock_stale_reason,
    ReviewSyncLock,
    should_run_stage,
    strong_repair_apply_confirmed,
    sync_finalized_corrections,
    sweep_actionable_batches,
    update_runtime_status,
)
from yomi_corpus.pipeline import PipelineWorkspace, TrackState


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


class FakeSweepWorkspace:
    def __init__(self, *, root: Path, current_batch_name: str | None = "dev_batch_0002") -> None:
        self.root = root
        self.current_batch_name = current_batch_name
        self.batches: dict[str, dict[str, str]] = {}
        self.calls: list[str] = []

    def batches_root(self) -> Path:
        return self.root / "data" / "pipeline" / "batches"

    def load_track_state(self, track_name: str) -> TrackState:
        return TrackState(
            track_name=track_name,
            current_batch_name=self.current_batch_name,
            updated_at="2026-07-13T00:00:00Z",
            decoder_model_dir=None,
        )

    def load_batch_state(self, batch_name: str) -> object:
        payload = self.batches[batch_name]

        class Batch:
            pass

        batch = Batch()
        batch.batch_name = batch_name
        batch.track_name = payload["track_name"]
        batch.current_stage = payload["current_stage"]
        return batch

    def _next_stage_name(self, current_stage: str) -> str | None:
        try:
            index = STAGE_SEQUENCE.index(current_stage)
        except ValueError:
            return None
        next_index = index + 1
        if next_index >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[next_index]

    def advance_batch(self, batch_name: str) -> dict[str, object]:
        batch = self.batches[batch_name]
        next_stage = self._next_stage_name(batch["current_stage"])
        self.calls.append(f"{batch_name}:{next_stage}")
        if not next_stage:
            return {
                "batch_name": batch_name,
                "advanced": False,
                "current_stage": batch["current_stage"],
            }
        batch["current_stage"] = next_stage
        return {
            "track_name": batch["track_name"],
            "batch_name": batch_name,
            "advanced": True,
            "current_stage": next_stage,
            "next_stage": self._next_stage_name(next_stage),
            "blocking_reason": None,
            "artifacts": {},
        }


class ReviewSyncTests(unittest.TestCase):
    def test_apply_failed_submission_keeps_issue_open_until_retry_succeeds(self) -> None:
        import_summary = {
            "status": "ok",
            "summaries": [
                {"submission_id": "ok", "source": {"issue_number": 10}},
                {"submission_id": "failed", "source": {"issue_number": 11}},
            ],
            "skipped": [],
        }

        self.assertEqual(
            closable_issue_numbers(import_summary, failed_submission_ids={"failed"}),
            [10],
        )

        duplicate_retry = {
            "status": "ok",
            "summaries": [],
            "skipped": [
                {
                    "reason": "duplicate_submission_id",
                    "submission_id": "failed",
                    "source": {"issue_number": 11},
                }
            ],
        }
        self.assertEqual(closable_issue_numbers(duplicate_retry), [11])

    def test_reconcile_closes_issue_applied_before_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submission = root / "data" / "review_submissions" / "yomi_final" / "submission.json"
            submission.parent.mkdir(parents=True)
            submission.write_text("{}\n", encoding="utf-8")
            state_dir = root / "data" / "state" / "yomi_final"
            state_dir.mkdir(parents=True)
            (state_dir / "last_review_inbox_import_summary.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "summaries": [
                            {
                                "stored_path": str(submission),
                                "source": {"issue_number": 57},
                            }
                        ],
                        "skipped": [
                            {
                                "reason": "duplicate_submission_id",
                                "source": {"issue_number": 57},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "final_review_apply_summary.json").write_text(
                json.dumps({"submission_paths": [str(submission)]}),
                encoding="utf-8",
            )

            with patch("yomi_corpus.review_sync.close_github_issue") as close:
                close.side_effect = lambda *, repo, issue_number: {
                    "repo": repo,
                    "issue_number": issue_number,
                    "status": "closed",
                }
                result = reconcile_applied_final_review_issues(
                    root=root,
                    repo="owner/repo",
                    enabled=True,
                )

            self.assertEqual(result[0]["issue_number"], 57)
            close.assert_called_once_with(repo="owner/repo", issue_number=57)

    def test_sync_finalized_corrections_imports_applies_and_closes(self) -> None:
        import_summary = {
            "summaries": [{"submission_id": "ok_1", "source": {"issue_number": 10}}],
            "skipped": [],
        }
        apply_summary = {
            "applied_count": 1,
            "skipped_count": 0,
            "batches": [{"applied": [{"submission_id": "ok_1"}], "skipped": []}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("yomi_corpus.review_sync.import_open_issue_inbox", return_value=import_summary) as mocked_import,
                patch(
                    "yomi_corpus.review_sync.apply_finalized_correction_submissions_file",
                    return_value=apply_summary,
                ) as mocked_apply,
                patch("yomi_corpus.review_sync.close_github_issue") as mocked_close,
            ):
                mocked_close.side_effect = lambda *, repo, issue_number: {
                    "repo": repo,
                    "issue_number": issue_number,
                    "closed": True,
                }
                result = sync_finalized_corrections(
                    root=root,
                    repo="owner/repo",
                    track_name="dev",
                    close_issues=True,
                )

            self.assertTrue(result["changed"])
            self.assertTrue((root / "data" / "state" / "finalized_correction" / "last_review_inbox_import_summary.json").exists())
            self.assertEqual(result["close_results"], [{"repo": "owner/repo", "issue_number": 10, "closed": True}])
            mocked_import.assert_called_once()
            mocked_apply.assert_called_once()
            mocked_close.assert_called_once_with(repo="owner/repo", issue_number=10)

    def test_close_finalized_correction_issues_only_closes_applied_submissions(self) -> None:
        import_summary = {
            "summaries": [
                {
                    "submission_id": "ok_1",
                    "source": {"issue_number": 10},
                },
                {
                    "submission_id": "bad_1",
                    "source": {"issue_number": 11},
                },
            ],
            "skipped": [],
        }
        apply_summary = {
            "batches": [
                {
                    "applied": [{"submission_id": "ok_1"}],
                    "skipped": [{"submission_id": "bad_1", "reason": "invalid"}],
                }
            ]
        }

        with patch("yomi_corpus.review_sync.close_github_issue") as mocked_close:
            mocked_close.side_effect = lambda *, repo, issue_number: {
                "repo": repo,
                "issue_number": issue_number,
                "closed": True,
            }
            result = close_finalized_correction_issues(
                repo="owner/repo",
                import_summary=import_summary,
                apply_summary=apply_summary,
                enabled=True,
            )

        self.assertEqual(result, [{"repo": "owner/repo", "issue_number": 10, "closed": True}])
        mocked_close.assert_called_once_with(repo="owner/repo", issue_number=10)

    def test_review_sync_lock_blocks_active_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "dev.lock"
            lock_path.write_text(
                json.dumps({"pid": 12345, "created_at": "2026-07-13T00:00:00Z"}),
                encoding="utf-8",
            )

            with patch("yomi_corpus.review_sync.process_is_alive", return_value=True):
                self.assertIsNone(review_sync_lock_stale_reason(lock_path))
                with self.assertRaises(SystemExit):
                    with ReviewSyncLock(lock_path):
                        pass

            self.assertTrue(lock_path.exists())

    def test_review_sync_lock_recovers_dead_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "dev.lock"
            lock_path.write_text(
                json.dumps({"pid": 12345, "created_at": "2026-07-13T00:00:00Z"}),
                encoding="utf-8",
            )

            with patch("yomi_corpus.review_sync.process_is_alive", return_value=False):
                self.assertEqual(review_sync_lock_stale_reason(lock_path), "lock_pid_not_running")
                with ReviewSyncLock(lock_path):
                    payload = json.loads(lock_path.read_text(encoding="utf-8"))
                    self.assertEqual(payload["pid"], os.getpid())

            self.assertFalse(lock_path.exists())

    def test_review_sync_lock_recovers_malformed_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "dev.lock"
            lock_path.write_text("not-json\n", encoding="utf-8")

            self.assertEqual(review_sync_lock_stale_reason(lock_path), "malformed_lock_json")
            with ReviewSyncLock(lock_path):
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["pid"], os.getpid())

            self.assertFalse(lock_path.exists())

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

    def test_aggregate_document_queue_summary_counts_all_unfinished_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = FakeSweepWorkspace(root=root)
            workspace.batches = {
                "dev_batch_0001": {
                    "track_name": "dev",
                    "current_stage": STAGE_FINAL_REVIEW_PREPARED,
                },
                "dev_batch_0002": {
                    "track_name": "dev",
                    "current_stage": STAGE_FINAL_REVIEW_PREPARED,
                },
                "dev_batch_0003": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_FINALIZED,
                },
            }
            write_batch_state(
                root, "dev_batch_0001", current_stage=STAGE_FINAL_REVIEW_PREPARED
            )
            write_batch_state(
                root, "dev_batch_0002", current_stage=STAGE_FINAL_REVIEW_PREPARED
            )
            write_batch_state(root, "dev_batch_0003", current_stage=STAGE_YOMI_FINALIZED)
            write_document_state(
                root,
                "dev_batch_0001",
                [{"doc_id": "doc1", "doc_seq": 1, "state": STATE_FINAL_PENDING}],
            )
            write_document_state(
                root,
                "dev_batch_0002",
                [
                    {"doc_id": "doc2", "doc_seq": 2, "state": STATE_FINAL_IN_REVIEW},
                    {"doc_id": "doc3", "doc_seq": 3, "state": STATE_FINAL_REVIEWED},
                ],
            )
            write_document_state(
                root,
                "dev_batch_0003",
                [{"doc_id": "doc4", "doc_seq": 4, "state": STATE_COMPLETE}],
            )

            summary = aggregate_document_queue_summary(
                root=root, workspace=workspace, track_name="dev"
            )

            self.assertEqual(summary["scope"], "all_unfinished_batches")
            self.assertEqual(summary["batch_names"], ["dev_batch_0001", "dev_batch_0002"])
            self.assertEqual(summary["document_count"], 3)
            self.assertEqual(summary["pool_counts"]["bulk-ready"], 2)
            self.assertEqual(summary["pool_counts"]["bulk-submitted"], 1)
            self.assertEqual(summary["pool_counts"].get("resolved", 0), 0)

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

    def test_review_sync_config_loads_decoder_refresh_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "review_sync.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[tracks.dev.decoder_refresh]",
                        'mode = "on-finalize"',
                        "min_new_batches = 3",
                        "min_interval_minutes = 15",
                        "skip_kenlm = true",
                        "[tracks.dev.bulk_review_refill]",
                        "target_ready_docs = 12",
                        "pass_limit = 4",
                        "[tracks.dev.refill_worker]",
                        "max_stages = 17",
                        'llm_execution_mode = "batch"',
                    ]
                ),
                encoding="utf-8",
            )

            config = load_review_sync_config("dev", config_path)

            self.assertEqual(config.mode, "on-finalize")
            self.assertEqual(config.min_new_batches, 3)
            self.assertEqual(config.min_interval_minutes, 15)
            self.assertTrue(config.skip_kenlm)
            self.assertEqual(config.bulk_review_target_ready_docs, 12)
            self.assertEqual(config.refill_pass_limit, 4)
            self.assertEqual(config.refill_max_stages, 17)
            self.assertEqual(config.refill_llm_execution_mode, "batch")

    def test_runtime_status_only_revises_for_meaningful_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = ReviewSyncOptions(
                track_name="dev",
                runtime_status_interval_seconds=300,
                runtime_status_grace_seconds=90,
            )
            status = {
                "current_batch_name": "dev_batch_0001",
                "current_stage": "yomi_final_review_prepared",
                "next_stage": "yomi_final_review_applied",
            }
            queues = {
                "queue_counts": {"bulk_review_selectable": 2},
                "pool_counts": {"bulk-ready": 2},
            }

            first = update_runtime_status(
                root=root,
                options=options,
                started_at_epoch=1000,
                completed_at_epoch=1005,
                final_status=status,
                document_queue_summary=queues,
                workflow_changed=True,
            )
            unchanged = update_runtime_status(
                root=root,
                options=options,
                started_at_epoch=1300,
                completed_at_epoch=1305,
                final_status=status,
                document_queue_summary=queues,
                workflow_changed=False,
            )
            changed = update_runtime_status(
                root=root,
                options=options,
                started_at_epoch=1600,
                completed_at_epoch=1605,
                final_status=status,
                document_queue_summary={
                    "queue_counts": {"bulk_review_selectable": 1},
                    "pool_counts": {"bulk-ready": 1},
                },
                workflow_changed=False,
            )

            self.assertTrue(first["publish_required"])
            self.assertEqual(first["state_revision"], 1)
            self.assertFalse(unchanged["publish_required"])
            self.assertEqual(unchanged["state_revision"], 1)
            self.assertTrue(changed["publish_required"])
            self.assertEqual(changed["state_revision"], 2)
            payload = json.loads(
                (root / "data" / "state" / "review_sync" / "dev.runtime_status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["status"], "waiting_for_review")
            self.assertEqual(payload["state"]["active_queue_count"], 1)

    def test_runtime_status_revises_when_schedule_drift_exceeds_grace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = ReviewSyncOptions(
                track_name="dev",
                runtime_status_interval_seconds=300,
                runtime_status_grace_seconds=30,
            )
            common = {
                "root": root,
                "options": options,
                "final_status": {},
                "document_queue_summary": {},
            }
            update_runtime_status(
                **common,
                started_at_epoch=1000,
                completed_at_epoch=1001,
                workflow_changed=True,
            )
            result = update_runtime_status(
                **common,
                started_at_epoch=1340,
                completed_at_epoch=1341,
                workflow_changed=False,
            )

            self.assertTrue(result["publish_required"])
            self.assertEqual(result["drift_seconds"], 40)

    def test_decoder_refresh_plan_skips_never_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            write_finalized_batch(root, "dev_batch_0001")
            options = ReviewSyncOptions(track_name="dev", decoder_refresh_mode="never")

            plan = build_decoder_refresh_plan(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=["dev_batch_0001"],
            )

            self.assertFalse(plan["will_refresh"])
            self.assertEqual(plan["reason"], "mode_never")

    def test_decoder_refresh_plan_refreshes_on_finalize_with_new_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            write_finalized_batch(root, "dev_batch_0001")
            options = ReviewSyncOptions(track_name="dev", decoder_refresh_mode="on-finalize")

            plan = build_decoder_refresh_plan(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=["dev_batch_0001"],
            )

            self.assertTrue(plan["will_refresh"])
            self.assertEqual(plan["new_since_refresh"], ["dev_batch_0001"])

    def test_decoder_refresh_plan_retries_unrefreshed_finalized_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            write_finalized_batch(root, "dev_batch_0001")
            options = ReviewSyncOptions(track_name="dev", decoder_refresh_mode="on-finalize")

            plan = build_decoder_refresh_plan(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=[],
            )

            self.assertTrue(plan["will_refresh"])
            self.assertEqual(plan["new_since_refresh"], ["dev_batch_0001"])

    def test_decoder_refresh_plan_respects_min_new_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            write_finalized_batch(root, "dev_batch_0001")
            options = ReviewSyncOptions(
                track_name="dev",
                decoder_refresh_mode="on-finalize",
                decoder_refresh_min_new_batches=2,
            )

            plan = build_decoder_refresh_plan(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=["dev_batch_0001"],
            )

            self.assertFalse(plan["will_refresh"])
            self.assertEqual(plan["reason"], "min_new_batches_not_met")

    def test_decoder_refresh_plan_respects_min_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            model_dir = root / "models" / "dev" / "previous"
            model_dir.mkdir(parents=True)
            (model_dir / "yomi_corpus_refresh.json").write_text(
                json.dumps(
                    {
                        "track_name": "dev",
                        "finalized_batches": [],
                        "refreshed_at": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            track_state = workspace.load_track_state("dev")
            track_state.decoder_model_dir = str(model_dir)
            workspace.save_track_state(track_state)
            write_finalized_batch(root, "dev_batch_0001")
            options = ReviewSyncOptions(
                track_name="dev",
                decoder_refresh_mode="on-finalize",
                decoder_refresh_min_interval_minutes=60,
            )

            plan = build_decoder_refresh_plan(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=["dev_batch_0001"],
            )

            self.assertFalse(plan["will_refresh"])
            self.assertEqual(plan["reason"], "min_interval_not_met")

    def test_review_sync_queues_decoder_refresh_without_building(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            write_finalized_batch(root, "dev_batch_0001")
            options = ReviewSyncOptions(track_name="dev", decoder_refresh_mode="on-finalize")

            result = request_decoder_model_refresh(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=["dev_batch_0001"],
            )

            self.assertEqual(result["status"], "queued")
            self.assertTrue(result["request_created"])
            request_path = Path(result["request_path"])
            self.assertTrue(request_path.exists())
            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(request["plan"]["new_since_refresh"], ["dev_batch_0001"])

            duplicate = request_decoder_model_refresh(
                root=root,
                workspace=workspace,
                options=options,
                newly_finalized_batches=[],
            )
            self.assertEqual(duplicate["status"], "queued")
            self.assertFalse(duplicate["request_created"])
            self.assertEqual(duplicate["request_id"], result["request_id"])

    def test_list_track_batches_returns_only_requested_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            write_batch_state(root, "dev_batch_0002", track_name="dev", current_stage="final_review_prepared")
            write_batch_state(root, "batch_0001", track_name="working", current_stage="final_review_prepared")
            write_batch_state(root, "dev_batch_0001", track_name="dev", current_stage="yomi_finalized")

            self.assertEqual(
                list_track_batches(workspace, "dev"),
                ["dev_batch_0001", "dev_batch_0002"],
            )

    def test_sweep_actionable_batches_advances_non_current_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = FakeSweepWorkspace(root=root, current_batch_name="dev_batch_0002")
            workspace.batches = {
                "dev_batch_0001": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_STRONG_REPAIR_QUEUED,
                },
                "dev_batch_0002": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_STRONG_REPAIR_QUEUED,
                },
            }
            write_batch_state(root, "dev_batch_0001", track_name="dev", current_stage=STAGE_YOMI_STRONG_REPAIR_QUEUED)
            write_batch_state(root, "dev_batch_0002", track_name="dev", current_stage=STAGE_YOMI_STRONG_REPAIR_QUEUED)
            write_strong_repair_queue(root, "dev_batch_0001", item_count=2)
            write_strong_repair_apply_summary(root, "dev_batch_0001", queued_items=2, confirmed=True)
            write_strong_repair_queue(root, "dev_batch_0002", item_count=2)
            write_strong_repair_apply_summary(root, "dev_batch_0002", queued_items=2, confirmed=True)

            results = sweep_actionable_batches(
                root=root,
                workspace=workspace,  # type: ignore[arg-type]
                options=ReviewSyncOptions(track_name="dev"),
                max_stages=2,
            )

            self.assertEqual(
                [row["attempted_stage"] for row in results],
                [STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED, STAGE_YOMI_FINALIZED],
            )
            self.assertEqual(workspace.calls, [
                f"dev_batch_0001:{STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED}",
                f"dev_batch_0001:{STAGE_YOMI_FINALIZED}",
            ])
            self.assertEqual(workspace.batches["dev_batch_0001"]["current_stage"], STAGE_YOMI_FINALIZED)
            self.assertEqual(workspace.batches["dev_batch_0002"]["current_stage"], STAGE_YOMI_STRONG_REPAIR_QUEUED)

    def test_sweep_allows_newer_batch_to_finish_while_older_batch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = FakeSweepWorkspace(root=root, current_batch_name="dev_batch_0003")
            workspace.batches = {
                "dev_batch_0001": {
                    "track_name": "dev",
                    "current_stage": "yomi_reading_llm_completed",
                },
                "dev_batch_0002": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_STRONG_REPAIR_QUEUED,
                },
                "dev_batch_0003": {
                    "track_name": "dev",
                    "current_stage": STAGE_FINAL_REVIEW_PREPARED,
                },
            }
            for batch_name, payload in workspace.batches.items():
                write_batch_state(
                    root,
                    batch_name,
                    current_stage=payload["current_stage"],
                )
            write_strong_repair_queue(root, "dev_batch_0002", item_count=0)

            results = sweep_actionable_batches(
                root=root,
                workspace=workspace,  # type: ignore[arg-type]
                options=ReviewSyncOptions(track_name="dev"),
                max_stages=2,
            )

            self.assertEqual(
                [row["batch_name"] for row in results],
                ["dev_batch_0002", "dev_batch_0002"],
            )
            self.assertEqual(
                workspace.batches["dev_batch_0001"]["current_stage"],
                "yomi_reading_llm_completed",
            )
            self.assertEqual(
                workspace.batches["dev_batch_0002"]["current_stage"],
                STAGE_YOMI_FINALIZED,
            )

    def test_sweep_runs_partial_repair_workflow_for_non_current_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = FakeSweepWorkspace(root=root, current_batch_name="dev_batch_0002")
            workspace.batches = {
                "dev_batch_0001": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_STRONG_REPAIR_QUEUED,
                },
                "dev_batch_0002": {
                    "track_name": "dev",
                    "current_stage": STAGE_FINAL_REVIEW_PREPARED,
                },
            }
            for batch_name, payload in workspace.batches.items():
                write_batch_state(root, batch_name, current_stage=payload["current_stage"])

            partial = {
                "batch_name": "dev_batch_0001",
                "attempted_stage": STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
                "advanced": False,
                "artifacts": {"yomi_strong_repair_llm_job_status": "completed"},
            }
            with patch(
                "yomi_corpus.review_sync.maintain_strong_repair_for_reviewed_documents",
                return_value=[partial],
            ) as maintain:
                results = sweep_actionable_batches(
                    root=root,
                    workspace=workspace,  # type: ignore[arg-type]
                    options=ReviewSyncOptions(track_name="dev"),
                    max_stages=1,
                )

            maintain.assert_called_once_with(
                root=root,
                workspace=workspace,
                batch_name="dev_batch_0001",
                allow_queue=False,
            )
            self.assertTrue(results[0]["partial_document_workflow"])
            self.assertTrue(results[0]["sweep_batch"])

    def test_sweep_actionable_batches_dry_run_does_not_advance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = FakeSweepWorkspace(root=root, current_batch_name="dev_batch_0002")
            workspace.batches = {
                "dev_batch_0001": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_STRONG_REPAIR_QUEUED,
                },
                "dev_batch_0002": {
                    "track_name": "dev",
                    "current_stage": STAGE_YOMI_STRONG_REPAIR_QUEUED,
                },
            }
            write_batch_state(root, "dev_batch_0001", track_name="dev", current_stage=STAGE_YOMI_STRONG_REPAIR_QUEUED)
            write_batch_state(root, "dev_batch_0002", track_name="dev", current_stage=STAGE_YOMI_STRONG_REPAIR_QUEUED)
            write_strong_repair_queue(root, "dev_batch_0001", item_count=1)
            write_strong_repair_apply_summary(root, "dev_batch_0001", queued_items=1, confirmed=True)

            results = sweep_actionable_batches(
                root=root,
                workspace=workspace,  # type: ignore[arg-type]
                options=ReviewSyncOptions(track_name="dev"),
                max_stages=2,
                dry_run=True,
            )

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["dry_run"])
            self.assertEqual(results[0]["attempted_stage"], STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED)
            self.assertEqual(workspace.calls, [])
            self.assertEqual(workspace.batches["dev_batch_0001"]["current_stage"], STAGE_YOMI_STRONG_REPAIR_QUEUED)

    def test_strong_repair_confirmed_summary_allows_llm_completed_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_strong_repair_queue(root, "dev_batch_0001", item_count=3)
            write_strong_repair_apply_summary(root, "dev_batch_0001", queued_items=3, confirmed=True)

            self.assertTrue(strong_repair_apply_confirmed(root=root, batch_name="dev_batch_0001"))
            self.assertTrue(
                should_run_stage(
                    root=root,
                    batch_name="dev_batch_0001",
                    next_stage=STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
                )
            )

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


def write_finalized_batch(root: Path, batch_name: str, *, track_name: str = "dev") -> None:
    write_batch_state(root, batch_name, track_name=track_name, current_stage="yomi_finalized")
    batch_dir = root / "data" / "units" / batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "units.yomi.final.jsonl").write_text(
        json.dumps({"unit_id": f"{batch_name}:u1", "text": "テストです。"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_batch_state(
    root: Path,
    batch_name: str,
    *,
    track_name: str = "dev",
    current_stage: str,
) -> None:
    batch_state_path = root / "data" / "pipeline" / "batches" / f"{batch_name}.json"
    batch_state_path.parent.mkdir(parents=True, exist_ok=True)
    batch_state_path.write_text(
        json.dumps(
            {
                "batch_name": batch_name,
                "track_name": track_name,
                "current_stage": current_stage,
                "updated_at": "2026-07-13T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_strong_repair_queue(root: Path, batch_name: str, *, item_count: int) -> None:
    batch_dir = root / "data" / "units" / batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"item_id": f"{batch_name}:item{i}"}, ensure_ascii=False)
        for i in range(item_count)
    ]
    (batch_dir / "yomi_strong_repair_queue.jsonl").write_text(
        ("\n".join(lines) + "\n") if lines else "",
        encoding="utf-8",
    )


def write_strong_repair_apply_summary(
    root: Path,
    batch_name: str,
    *,
    queued_items: int,
    confirmed: bool,
) -> None:
    batch_dir = root / "data" / "units" / batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "yomi_strong_repair_apply_summary.json").write_text(
        json.dumps(
            {
                "queued_items": queued_items,
                "confirmed": confirmed,
            },
            ensure_ascii=False,
        ),
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
