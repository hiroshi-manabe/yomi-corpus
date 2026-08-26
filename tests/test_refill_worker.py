from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.pipeline import STAGE_FINAL_REVIEW_PREPARED, STAGE_SEQUENCE
from yomi_corpus.refill_worker import (
    advance_batch_to_bulk_review_ready,
    find_resumable_refill_batch,
    run_refill_worker_until_target,
    RefillWorkerOptions,
)
from yomi_corpus.review_sync import ReviewSyncLock


class FakeBatch:
    def __init__(self, batch_name: str, current_stage: str) -> None:
        self.batch_name = batch_name
        self.current_stage = current_stage


class ExplicitBatchWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.current_batch_name = "dev_batch_0099"
        self.stages = {
            "dev_batch_0001": "prepared",
            "dev_batch_0002": STAGE_FINAL_REVIEW_PREPARED,
            "dev_batch_0099": STAGE_FINAL_REVIEW_PREPARED,
        }
        self.calls: list[tuple[str, str | None]] = []

    def batches_root(self) -> Path:
        return self.root / "data" / "pipeline" / "batches"

    def load_batch_state(self, batch_name: str) -> FakeBatch:
        return FakeBatch(batch_name, self.stages[batch_name])

    def _next_stage_name(self, current_stage: str) -> str | None:
        index = STAGE_SEQUENCE.index(current_stage)
        if index + 1 >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[index + 1]

    def advance_batch(
        self,
        batch_name: str,
        *,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        self.calls.append((batch_name, llm_execution_mode_override))
        next_stage = self._next_stage_name(self.stages[batch_name])
        assert next_stage is not None
        self.stages[batch_name] = next_stage
        return {
            "batch_name": batch_name,
            "advanced": True,
            "current_stage": next_stage,
            "blocking_reason": None,
        }


class RefillWorkerTests(unittest.TestCase):
    @staticmethod
    def refill_iteration(
        *,
        action: str,
        changed: bool,
        batch_name: str | None = None,
        advance_status: str | None = None,
        reason: str | None = None,
        ready_before: int = 0,
        ready_after: int = 0,
    ) -> dict[str, object]:
        action_payload: dict[str, object] = {
            "action": action,
            "changed": changed,
        }
        if batch_name is not None:
            action_payload["batch_name"] = batch_name
        if advance_status is not None:
            action_payload["advance_result"] = {
                "status": advance_status,
                "reason": reason,
            }
        if reason is not None and action == "none":
            action_payload["reason"] = reason
        return {
            "schema_version": 1,
            "track_name": "dev",
            "started_at": "2026-08-26T00:00:00Z",
            "completed_at": "2026-08-26T00:00:01Z",
            "duration_seconds": 1,
            "dry_run": False,
            "policy": {"target_ready_docs": 100},
            "refill_plan": {
                "target_ready_docs": 100,
                "bulk_review_ready_docs": ready_before,
            },
            "post_refill_queue_summary": {
                "pool_counts": {"bulk-ready": ready_after},
            },
            "action": action_payload,
        }

    def test_refill_and_review_locks_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with ReviewSyncLock(state / "review_sync" / "dev.lock"):
                with ReviewSyncLock(state / "refill" / "dev.lock"):
                    self.assertTrue((state / "review_sync" / "dev.lock").exists())
                    self.assertTrue((state / "refill" / "dev.lock").exists())

    def test_find_resumable_batch_ignores_current_pointer_and_ready_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = ExplicitBatchWorkspace(root)
            batches_root = workspace.batches_root()
            batches_root.mkdir(parents=True)
            for batch_name in workspace.stages:
                (batches_root / f"{batch_name}.json").write_text(
                    json.dumps(
                        {
                            "batch_name": batch_name,
                            "track_name": "dev",
                            "updated_at": "2026-07-17T00:00:00Z",
                        }
                    ),
                    encoding="utf-8",
                )

            self.assertEqual(find_resumable_refill_batch(workspace, "dev"), "dev_batch_0001")

    def test_advance_uses_explicit_batch_name_until_review_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ExplicitBatchWorkspace(Path(tmp))

            result = advance_batch_to_bulk_review_ready(
                workspace=workspace,
                batch_name="dev_batch_0001",
                max_stages=50,
                llm_execution_mode_override="background",
            )

            self.assertEqual(result["status"], "bulk_review_ready")
            self.assertTrue(result["changed"])
            self.assertEqual(workspace.stages["dev_batch_0001"], STAGE_FINAL_REVIEW_PREPARED)
            self.assertTrue(workspace.calls)
            self.assertEqual({name for name, _ in workspace.calls}, {"dev_batch_0001"})
            self.assertIn(("dev_batch_0001", "background"), workspace.calls)
            self.assertEqual(workspace.calls[-1], ("dev_batch_0001", None))
            self.assertEqual(workspace.stages["dev_batch_0099"], STAGE_FINAL_REVIEW_PREPARED)

    def test_advance_stops_without_losing_resumable_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ExplicitBatchWorkspace(Path(tmp))
            original = workspace.advance_batch

            def stop_once(
                batch_name: str,
                *,
                llm_execution_mode_override: str | None = None,
            ) -> dict[str, object]:
                workspace.advance_batch = original  # type: ignore[method-assign]
                return {
                    "batch_name": batch_name,
                    "advanced": False,
                    "current_stage": workspace.stages[batch_name],
                    "blocking_reason": "remote job still running",
                }

            workspace.advance_batch = stop_once  # type: ignore[method-assign]
            result = advance_batch_to_bulk_review_ready(
                workspace=workspace,
                batch_name="dev_batch_0001",
                max_stages=50,
            )

            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["reason"], "remote job still running")
            self.assertEqual(workspace.stages["dev_batch_0001"], "prepared")

    def test_until_target_runs_completed_batches_without_timer_gap(self) -> None:
        iterations = [
            self.refill_iteration(
                action="prepare_next_batch",
                changed=True,
                batch_name="dev_batch_0001",
                advance_status="bulk_review_ready",
                ready_before=80,
                ready_after=90,
            ),
            self.refill_iteration(
                action="prepare_next_batch",
                changed=True,
                batch_name="dev_batch_0002",
                advance_status="bulk_review_ready",
                ready_before=90,
                ready_after=100,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yomi_corpus.refill_worker._run_refill_worker_pass_unlocked",
                side_effect=iterations,
            ) as run_iteration:
                summary = run_refill_worker_until_target(
                    Path(tmp),
                    RefillWorkerOptions(
                        track_name="dev",
                        target_ready_docs=100,
                        pass_limit=10,
                    ),
                )

        self.assertEqual(run_iteration.call_count, 2)
        self.assertEqual(summary["iteration_count"], 2)
        self.assertEqual(
            summary["completed_batches"],
            ["dev_batch_0001", "dev_batch_0002"],
        )
        self.assertEqual(summary["stop_reason"], "bulk_review_target_satisfied")
        self.assertTrue(summary["changed"])

    def test_until_target_stops_when_resumable_batch_makes_no_progress(self) -> None:
        iteration = self.refill_iteration(
            action="resume_batch",
            changed=False,
            batch_name="dev_batch_0001",
            advance_status="incomplete",
            reason="remote job still running",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yomi_corpus.refill_worker._run_refill_worker_pass_unlocked",
                return_value=iteration,
            ) as run_iteration:
                summary = run_refill_worker_until_target(
                    Path(tmp),
                    RefillWorkerOptions(
                        track_name="dev",
                        target_ready_docs=100,
                        pass_limit=10,
                    ),
                )

        run_iteration.assert_called_once()
        self.assertEqual(summary["stop_reason"], "no_progress")
        self.assertFalse(summary["changed"])

    def test_until_target_stops_when_ready_count_does_not_increase(self) -> None:
        iteration = self.refill_iteration(
            action="prepare_next_batch",
            changed=True,
            batch_name="dev_batch_0001",
            advance_status="bulk_review_ready",
            ready_before=90,
            ready_after=90,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "yomi_corpus.refill_worker._run_refill_worker_pass_unlocked",
                return_value=iteration,
            ) as run_iteration:
                summary = run_refill_worker_until_target(
                    Path(tmp),
                    RefillWorkerOptions(
                        track_name="dev",
                        target_ready_docs=100,
                        pass_limit=10,
                    ),
                )

        run_iteration.assert_called_once()
        self.assertEqual(summary["stop_reason"], "ready_count_not_increasing")


if __name__ == "__main__":
    unittest.main()
