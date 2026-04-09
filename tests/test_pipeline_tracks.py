from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.pipeline import (
    DEFAULT_TRACK,
    DEV_TRACK,
    WORKING_TRACK,
    PipelineWorkspace,
    TrackState,
    is_protected_track,
    is_working_track,
    normalize_track_name,
    requires_strict_human_review_gates,
)


class PipelineTrackTests(unittest.TestCase):
    def test_working_track_is_default_and_protected(self) -> None:
        self.assertEqual(DEFAULT_TRACK, WORKING_TRACK)
        self.assertTrue(is_working_track(WORKING_TRACK))
        self.assertTrue(is_protected_track(WORKING_TRACK))
        self.assertTrue(requires_strict_human_review_gates(WORKING_TRACK))
        self.assertFalse(is_protected_track(DEV_TRACK))
        self.assertFalse(requires_strict_human_review_gates(DEV_TRACK))
        self.assertEqual(normalize_track_name(None), WORKING_TRACK)

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
            self.assertEqual(status["track_policy"], "strict")
            self.assertEqual(status["requires_strict_human_review_gates"], True)
            self.assertEqual(status["current_batch_name"], "batch_0001")
            self.assertEqual(status["current_stage"], "prepared")
            self.assertEqual(status["skipped_review_gates"], [])

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
            self.assertEqual(summary["track_policy"], "relaxed")
            self.assertEqual(summary["requires_strict_human_review_gates"], False)
            self.assertEqual(summary["current_stage"], "alphabetic_analyzed")
            self.assertEqual(
                summary["skipped_review_gates"],
                [
                    "promotion_candidate_review",
                    "sentence_review_pass1",
                    "sentence_review_pass2",
                    "final_edit_review",
                ],
            )
            saved = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved.current_stage, "alphabetic_analyzed")
            self.assertEqual(saved.skipped_review_gates, summary["skipped_review_gates"])

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

    def test_force_stage_reruns_current_stage_on_dev(self) -> None:
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
            workspace.save_batch_state(
                workspace._infer_batch_state("dev_batch_0001")
            )
            saved = workspace.load_batch_state("dev_batch_0001")
            saved.current_stage = "yomi_generated"
            saved.blocking_reason = "No later automated stage is implemented yet after mechanical yomi generation."
            saved.artifacts["units_yomi_jsonl"] = str(batch_dir / "units.yomi.aligned_hybrid.jsonl")
            (batch_dir / "units.yomi.aligned_hybrid.jsonl").write_text("", encoding="utf-8")
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch.object(workspace, "_generate_mechanical_yomi") as mocked_stage:
                mocked_stage.return_value = {
                    "artifacts": {
                        "units_yomi_jsonl": str(batch_dir / "units.yomi.aligned_hybrid.jsonl"),
                        "yomi_variant": "aligned_hybrid",
                    }
                }
                summary = workspace.advance("dev", force_stage="yomi_generated")

            self.assertTrue(summary["advanced"])
            self.assertTrue(summary["forced"])
            self.assertEqual(summary["current_stage"], "yomi_generated")
            self.assertEqual(mocked_stage.call_count, 1)

    def test_force_stage_requires_confirmation_on_working_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "batch_name": "batch_0001",
                        "track_name": "working",
                        "batch_kind": "working",
                        "pipeline_profile": "working",
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
            workspace.save_batch_state(
                workspace._infer_batch_state("batch_0001")
            )
            saved = workspace.load_batch_state("batch_0001")
            saved.current_stage = "yomi_generated"
            saved.blocking_reason = "No later automated stage is implemented yet after mechanical yomi generation."
            saved.artifacts["units_yomi_jsonl"] = str(batch_dir / "units.yomi.aligned_hybrid.jsonl")
            yomi_path = batch_dir / "units.yomi.aligned_hybrid.jsonl"
            yomi_path.write_text("", encoding="utf-8")
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="working",
                    current_batch_name="batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("working", force_stage="yomi_generated")

            self.assertFalse(summary["advanced"])
            self.assertTrue(summary["requires_confirmation"])
            self.assertEqual(summary["requested_force_stage"], "yomi_generated")
            self.assertEqual(
                [str(Path(path).resolve()) for path in summary["overwrite_paths"]],
                [str(yomi_path.resolve())],
            )


if __name__ == "__main__":
    unittest.main()
