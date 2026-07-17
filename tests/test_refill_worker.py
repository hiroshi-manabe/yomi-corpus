from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.pipeline import STAGE_FINAL_REVIEW_PREPARED, STAGE_SEQUENCE
from yomi_corpus.refill_worker import (
    advance_batch_to_bulk_review_ready,
    find_resumable_refill_batch,
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
            self.assertEqual({mode for _, mode in workspace.calls}, {"background"})
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


if __name__ == "__main__":
    unittest.main()
