from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from yomi_corpus.mechanical_preflight import (
    MechanicalPreflightOptions,
    run_mechanical_preflight,
)
from yomi_corpus.pipeline import (
    STAGE_PREPARED,
    STAGE_YOMI_AUTO_ACCEPTED,
    STAGE_YOMI_GENERATED,
    STAGE_YOMI_READING_QUEUED,
    TrackState,
)


class MechanicalPreflightTests(unittest.TestCase):
    def test_runs_to_queue_boundary_without_llm_and_removes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "datasets").mkdir(parents=True)
            live = MagicMock()
            isolated = MagicMock()
            configure_live_workspace(live)
            configure_isolated_workspace(isolated, root)

            with patch(
                "yomi_corpus.mechanical_preflight.PipelineWorkspace",
                side_effect=[live, isolated],
            ), patch("yomi_corpus.mechanical_preflight.copy_processing_order_state"):
                result = run_mechanical_preflight(
                    root,
                    MechanicalPreflightOptions(track_name="dev", target_documents=2),
                )

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["current_stage"], STAGE_YOMI_READING_QUEUED)
            self.assertEqual(result["llm_requests_sent"], 0)
            self.assertFalse(Path(result["workspace_path"]).exists())
            self.assertTrue(Path(result["report_path"]).exists())
            self.assertEqual(isolated.advance_batch.call_count, 3)

    def test_failure_is_reported_and_workspace_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "datasets").mkdir(parents=True)
            live = MagicMock()
            isolated = MagicMock()
            configure_live_workspace(live)
            configure_isolated_workspace(isolated, root)
            isolated.advance_batch.side_effect = RuntimeError("mechanical failure")

            with patch(
                "yomi_corpus.mechanical_preflight.PipelineWorkspace",
                side_effect=[live, isolated],
            ), patch("yomi_corpus.mechanical_preflight.copy_processing_order_state"):
                result = run_mechanical_preflight(
                    root,
                    MechanicalPreflightOptions(track_name="dev", target_documents=2),
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], "mechanical failure")
            self.assertEqual(result["llm_requests_sent"], 0)
            self.assertFalse(Path(result["workspace_path"]).exists())
            persisted = json.loads(Path(result["report_path"]).read_text())
            self.assertEqual(persisted["status"], "failed")


def configure_live_workspace(workspace: MagicMock) -> None:
    workspace.preview_next_source_documents.return_value = {
        "track_name": "dev",
        "dataset_name": "dataset",
        "dataset_config_path": "config/datasets/ja_cc_level2.toml",
        "dataset_source_path": "/tmp/source.jsonl.gz",
        "processing_order_cursor": 101,
        "selected_document_count": 2,
        "selected_documents": [],
    }
    workspace.load_track_state.return_value = TrackState(
        track_name="dev",
        current_batch_name="dev_batch_0010",
        decoder_model_dir="/tmp/model",
        updated_at="2026-08-25T00:00:00Z",
    )


def configure_isolated_workspace(workspace: MagicMock, root: Path) -> None:
    units_root = root / "fake-units"
    manifest = root / "fake-manifest.json"
    manifest.write_text(
        json.dumps({"source_start_line_no": 101, "source_end_line_no": 102}),
        encoding="utf-8",
    )
    workspace.units_root.return_value = units_root
    workspace.manifest_path.return_value = manifest
    workspace.batch_dir.return_value = root / "fake-batch"
    workspace.prepare_next_batch.return_value = {
        "batch_name": "dev_batch_0001",
        "docs_written": 2,
        "units_written": 5,
    }
    workspace.load_batch_state.side_effect = [
        SimpleNamespace(current_stage=STAGE_PREPARED),
        SimpleNamespace(current_stage=STAGE_YOMI_GENERATED),
        SimpleNamespace(current_stage=STAGE_YOMI_AUTO_ACCEPTED),
        SimpleNamespace(current_stage=STAGE_YOMI_READING_QUEUED),
        SimpleNamespace(current_stage=STAGE_YOMI_READING_QUEUED),
    ]
    workspace._next_stage_name.side_effect = [
        STAGE_YOMI_GENERATED,
        STAGE_YOMI_AUTO_ACCEPTED,
        STAGE_YOMI_READING_QUEUED,
    ]
    workspace.advance_batch.side_effect = [
        {"advanced": True, "current_stage": STAGE_YOMI_GENERATED},
        {"advanced": True, "current_stage": STAGE_YOMI_AUTO_ACCEPTED},
        {"advanced": True, "current_stage": STAGE_YOMI_READING_QUEUED},
    ]


if __name__ == "__main__":
    unittest.main()
