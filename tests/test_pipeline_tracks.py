from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.pipeline import PipelineWorkspace, TrackState


class PipelineTrackTests(unittest.TestCase):
    def test_status_infers_latest_working_batch_when_track_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "batch_name": "batch_0001",
                        "dataset_name": "demo",
                        "dataset_source_path": "/tmp/source.jsonl.gz",
                        "target_documents": 10,
                        "docs_written": 10,
                        "units_written": 20,
                    }
                ),
                encoding="utf-8",
            )

            workspace = PipelineWorkspace(root)
            status = workspace.status("working")

            self.assertEqual(status["track_name"], "working")
            self.assertEqual(status["current_batch_name"], "batch_0001")
            self.assertEqual(status["current_stage"], "prepared")

    def test_prepare_next_batch_allocates_track_specific_name_and_updates_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            (root / "data" / "units" / "batch_0001").mkdir(parents=True)

            with patch.object(workspace, "_load_dataset_config") as mocked_load:
                mocked_load.return_value = {
                    "name": "demo",
                    "source_path": root / "source.jsonl.gz",
                }
                with patch.object(workspace, "_extract_batch_documents") as mocked_extract:
                    mocked_extract.return_value = (5, 12)
                    summary = workspace.prepare_next_batch(
                        track_name="working",
                        target_documents=5,
                    )

            self.assertEqual(summary["batch_name"], "batch_0002")
            self.assertEqual(summary["docs_written"], 5)
            self.assertEqual(summary["units_written"], 12)
            track_state = workspace.load_track_state("working")
            self.assertEqual(track_state.current_batch_name, "batch_0002")

    def test_advance_runs_one_stage_and_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "batch_name": "dev_batch_0001",
                        "track_name": "dev",
                        "batch_kind": "dev",
                        "pipeline_profile": "dev",
                        "dataset_name": "demo",
                        "dataset_config_path": "config/datasets/demo.toml",
                        "dataset_source_path": "/tmp/source.jsonl.gz",
                        "target_documents": 5,
                        "docs_written": 5,
                        "units_written": 10,
                    }
                ),
                encoding="utf-8",
            )
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch.object(workspace, "_run_alphabetic_analysis") as mocked_stage:
                mocked_stage.return_value = {
                    "artifacts": {
                        "units_alphabetic_jsonl": str(batch_dir / "units.alphabetic.jsonl"),
                    }
                }
                summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_analyzed")
            saved = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved.current_stage, "alphabetic_analyzed")

    def test_infer_stage_prefers_latest_materialized_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "batch_0003"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "batch_name": "batch_0003",
                        "dataset_name": "demo",
                        "dataset_source_path": "/tmp/source.jsonl.gz",
                        "target_documents": 10,
                        "docs_written": 10,
                        "units_written": 20,
                    }
                ),
                encoding="utf-8",
            )
            (batch_dir / "units.alphabetic.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "alphabetic_unresolved_entities.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "units.yomi.aligned_hybrid.jsonl").write_text("", encoding="utf-8")

            workspace = PipelineWorkspace(root)
            state = workspace.load_batch_state("batch_0003")
            self.assertEqual(state.current_stage, "yomi_generated")


if __name__ == "__main__":
    unittest.main()
