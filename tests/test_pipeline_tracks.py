from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
            self.assertEqual(
                status["yomi_policy"],
                {
                    "unit_mode": "sentence",
                    "auto_accept_profile": "strict",
                },
            )
            self.assertEqual(status["llm_policy"]["yomi_reading"], "standard")
            self.assertEqual(status["llm_execution_policy"]["yomi_reading"], "background")

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
                    mocked_extract.return_value = (5, 12, 1, 5)
                    summary = workspace.prepare_next_batch(
                        track_name="working",
                        target_documents=5,
                    )

            self.assertEqual(summary["batch_name"], "batch_0002")
            self.assertEqual(summary["docs_written"], 5)
            self.assertEqual(summary["units_written"], 12)
            self.assertEqual(
                summary["yomi_policy"],
                {
                    "unit_mode": "sentence",
                    "auto_accept_profile": "strict",
                },
            )
            self.assertEqual(summary["llm_policy"]["yomi_reading"], "standard")
            self.assertEqual(summary["llm_execution_policy"]["yomi_reading"], "background")
            track_state = workspace.load_track_state("working")
            self.assertEqual(track_state.current_batch_name, "batch_0002")
            self.assertEqual(mocked_extract.call_args.kwargs["skip_source_line_no"], 0)

    def test_prepare_next_batch_pins_track_decoder_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name=None,
                    updated_at="2026-04-09T00:00:00Z",
                    decoder_model_dir=str(root / "data" / "decoder_models" / "dev" / "m1"),
                )
            )

            with patch.object(workspace, "_load_dataset_config") as mocked_load:
                mocked_load.return_value = {
                    "name": "demo",
                    "source_path": root / "source.jsonl.gz",
                }
                with patch.object(workspace, "_extract_batch_documents") as mocked_extract:
                    mocked_extract.return_value = (2, 4, 1, 2)
                    summary = workspace.prepare_next_batch(
                        track_name="dev",
                        target_documents=2,
                    )

            batch_state = workspace.load_batch_state(str(summary["batch_name"]))
            self.assertEqual(
                batch_state.decoder_model_dir,
                str(root / "data" / "decoder_models" / "dev" / "m1"),
            )
            self.assertEqual(summary["decoder_model_dir"], batch_state.decoder_model_dir)
            manifest = json.loads(
                (root / "data" / "units" / str(summary["batch_name"]) / "manifest.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["decoder_model_dir"], batch_state.decoder_model_dir)

    def test_prepare_next_batch_accepts_explicit_yomi_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)

            with patch.object(workspace, "_load_dataset_config") as mocked_load:
                mocked_load.return_value = {
                    "name": "demo",
                    "source_path": root / "source.jsonl.gz",
                }
                with patch.object(workspace, "_extract_batch_documents") as mocked_extract:
                    mocked_extract.return_value = (3, 7, 1, 3)
                    summary = workspace.prepare_next_batch(
                        track_name="dev",
                        target_documents=3,
                        yomi_policy={
                            "unit_mode": "comma_span",
                            "auto_accept_profile": "off",
                        },
                        llm_policy={
                            "yomi_reading": "smoke",
                        },
                        llm_execution_policy={
                            "yomi_reading": "batch",
                        },
                    )

            self.assertEqual(
                summary["yomi_policy"],
                {
                    "unit_mode": "comma_span",
                    "auto_accept_profile": "off",
                },
            )
            self.assertEqual(summary["llm_policy"]["yomi_reading"], "smoke")
            self.assertEqual(summary["llm_execution_policy"]["yomi_reading"], "batch")
            state = workspace.load_batch_state(summary["batch_name"])
            self.assertEqual(
                state.yomi_policy,
                {
                    "unit_mode": "comma_span",
                    "auto_accept_profile": "off",
                },
            )
            self.assertEqual(state.llm_policy["yomi_reading"], "smoke")
            self.assertEqual(state.llm_execution_policy["yomi_reading"], "batch")
            manifest = json.loads(
                (root / "data" / "units" / summary["batch_name"] / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["yomi_policy"],
                {
                    "unit_mode": "comma_span",
                    "auto_accept_profile": "off",
                },
            )
            self.assertEqual(manifest["llm_policy"]["yomi_reading"], "smoke")
            self.assertEqual(manifest["llm_execution_policy"]["yomi_reading"], "batch")

    def test_prepare_next_batch_skips_previous_source_lines_on_same_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.jsonl.gz"
            with gzip.open(source_path, "wt", encoding="utf-8") as handle:
                for index in range(1, 5):
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

            config_dir = root / "config" / "datasets"
            config_dir.mkdir(parents=True)
            (config_dir / "demo.toml").write_text(
                f'name = "demo"\nsource_path = "{source_path}"\n',
                encoding="utf-8",
            )

            workspace = PipelineWorkspace(root)
            first = workspace.prepare_next_batch(
                track_name="dev",
                target_documents=2,
                dataset_config_path="config/datasets/demo.toml",
            )
            second = workspace.prepare_next_batch(
                track_name="dev",
                target_documents=2,
                dataset_config_path="config/datasets/demo.toml",
            )

            self.assertEqual(first["batch_name"], "dev_batch_0001")
            self.assertEqual(second["batch_name"], "dev_batch_0002")

            first_manifest = json.loads(
                (root / "data" / "units" / "dev_batch_0001" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            second_manifest = json.loads(
                (root / "data" / "units" / "dev_batch_0002" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(first_manifest["source_start_line_no"], 1)
            self.assertEqual(first_manifest["source_end_line_no"], 2)
            self.assertEqual(second_manifest["source_start_line_no"], 3)
            self.assertEqual(second_manifest["source_end_line_no"], 4)

            first_unit = json.loads(
                (root / "data" / "units" / "dev_batch_0001" / "units.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            second_unit = json.loads(
                (root / "data" / "units" / "dev_batch_0002" / "units.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertEqual(first_unit["source_line_no"], 1)
            self.assertEqual(second_unit["source_line_no"], 3)
            self.assertEqual(first_unit["track_doc_seq"], 1)
            self.assertEqual(second_unit["track_doc_seq"], 3)

            ledger = json.loads(
                (root / "data" / "pipeline" / "document_ledger" / "dev.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [(row["doc_id"], row["track_doc_seq"]) for row in ledger["documents"]],
                [
                    ("demo:0000000001", 1),
                    ("demo:0000000002", 2),
                    ("demo:0000000003", 3),
                    ("demo:0000000004", 4),
                ],
            )

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
            self.assertEqual(summary["next_stage"], "alphabetic_reported")
            self.assertEqual(summary["skipped_review_gates"], [])
            saved = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved.current_stage, "alphabetic_analyzed")
            self.assertEqual(saved.skipped_review_gates, summary["skipped_review_gates"])

    def test_llm_execution_override_is_rejected_on_non_llm_stage(self) -> None:
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
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("dev", llm_execution_mode_override="sync")

            self.assertFalse(summary["advanced"])
            self.assertEqual(summary["current_stage"], "prepared")
            self.assertEqual(summary["next_stage"], "alphabetic_analyzed")
            self.assertIn("does not call the LLM", summary["blocking_reason"])
            saved = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved.current_stage, "prepared")

    def test_set_stage_rewinds_current_batch_without_touching_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.jsonl").write_text("", encoding="utf-8")
            artifact_path = batch_dir / "units.yomi.llm_readings.jsonl"
            artifact_path.write_text("kept\n", encoding="utf-8")
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
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.set_stage("dev", "yomi_auto_accepted")

            self.assertTrue(summary["stage_changed"])
            self.assertEqual(summary["previous_stage"], "yomi_reading_llm_completed")
            self.assertEqual(summary["current_stage"], "yomi_auto_accepted")
            self.assertEqual(summary["next_stage"], "yomi_reading_queued")
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), "kept\n")
            saved = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved.current_stage, "yomi_auto_accepted")

    def test_set_stage_refuses_forward_moves(self) -> None:
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
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.set_stage("dev", "yomi_generated")

            self.assertFalse(summary["stage_changed"])
            self.assertIn("Refusing to move stage forward", summary["blocking_reason"])
            saved = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved.current_stage, "prepared")

    def test_set_stage_requires_confirmation_on_working(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "units.yomi.llm_readings.jsonl").write_text("", encoding="utf-8")
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
            workspace.save_batch_state(workspace._infer_batch_state("batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="working",
                    current_batch_name="batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            blocked = workspace.set_stage("working", "yomi_auto_accepted")

            self.assertFalse(blocked["stage_changed"])
            self.assertTrue(blocked["requires_confirmation"])
            self.assertIn("requires confirmation", blocked["blocking_reason"])
            saved = workspace.load_batch_state("batch_0001")
            self.assertEqual(saved.current_stage, "yomi_reading_llm_completed")

            changed = workspace.set_stage("working", "yomi_auto_accepted", allow_protected=True)

            self.assertTrue(changed["stage_changed"])
            saved = workspace.load_batch_state("batch_0001")
            self.assertEqual(saved.current_stage, "yomi_auto_accepted")

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

    def test_infer_stage_prefers_yomi_auto_acceptance_artifact(self) -> None:
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
            (batch_dir / "units.yomi.aligned_hybrid.jsonl").write_text("", encoding="utf-8")
            (batch_dir / "units.yomi.auto_accept.jsonl").write_text("", encoding="utf-8")

            workspace = PipelineWorkspace(root)
            state = workspace.load_batch_state("batch_0003")
            self.assertEqual(state.current_stage, "yomi_auto_accepted")

    def test_advance_runs_yomi_auto_acceptance_after_yomi_generation(self) -> None:
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
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            saved = workspace.load_batch_state("dev_batch_0001")
            saved.current_stage = "yomi_generated"
            saved.artifacts["units_yomi_jsonl"] = str(batch_dir / "units.yomi.aligned_hybrid.jsonl")
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch.object(workspace, "_auto_accept_mechanical_yomi") as mocked_stage:
                mocked_stage.return_value = {
                    "artifacts": {
                        "units_yomi_auto_accept_jsonl": str(batch_dir / "units.yomi.auto_accept.jsonl"),
                    }
                }
                summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "yomi_auto_accepted")
            self.assertIsNone(summary["blocking_reason"])
            self.assertEqual(mocked_stage.call_count, 1)

    def test_yomi_reading_queue_uses_yomi_auto_accept_artifact(self) -> None:
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
                        "units_written": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (batch_dir / "units.yomi.auto_accept.jsonl").write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "学校です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "sudachi": {"tokens": []},
                                    "auto_accept": {"value": False},
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            batch_state = workspace._infer_batch_state("dev_batch_0001")
            batch_state.decoder_model_dir = "/tmp/pinned-decoder-model"
            workspace.save_batch_state(batch_state)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch("yomi_corpus.pipeline.apply_yomi_safety_pre_llm_file") as mocked_safety:
                mocked_safety.return_value = SimpleNamespace(
                    target_count=1,
                    safe_targets=0,
                    unresolved_targets=1,
                    stable_two_kanji_safe=0,
                    corpus_frequency_safe=0,
                )
                with patch("yomi_corpus.pipeline.build_yomi_llm_reading_queue_file") as mocked_queue:
                    mocked_queue.return_value = SimpleNamespace(
                        queued_items=1,
                        skipped_items=0,
                        stable_two_kanji_skipped=0,
                        safety_skipped=0,
                    )
                    summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "yomi_reading_queued")
            self.assertEqual(
                mocked_safety.call_args.kwargs["input_jsonl"].resolve(),
                (batch_dir / "units.yomi.auto_accept.jsonl").resolve(),
            )
            self.assertEqual(
                mocked_safety.call_args.kwargs["decoder_model_dir"],
                "/tmp/pinned-decoder-model",
            )
            self.assertEqual(
                mocked_queue.call_args.kwargs["input_jsonl"].resolve(),
                (batch_dir / "units.yomi.safety_pre_llm.jsonl").resolve(),
            )

    def test_advance_retries_yomi_llm_reading_parse_errors(self) -> None:
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
                        "target_documents": 1,
                        "docs_written": 1,
                        "units_written": 1,
                        "llm_execution_policy": {"yomi_reading": "sync"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (batch_dir / "units.yomi.safety_pre_llm.jsonl").write_text(
                json.dumps({"unit_id": "u1", "text": "上です。"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            item = {
                "unit_id": "u1",
                "item_id": "u1:r0001c01",
                "token_index": 0,
                "chunk_index": 0,
                "surface": "上",
                "token_surface": "上",
                "current_reading": "ウエ",
                "current_reading_hiragana": "うえ",
                "text": "上です。",
                "marked_text": "**上**です。",
                "marked_furigana_text": "**上**です。",
                "token_start": 0,
                "token_end": 1,
                "target_start": 0,
                "target_end": 1,
                "pos": "名詞,普通名詞,副詞可能,*,*,*",
                "dictionary_form": "上",
                "normalized_form": "上",
                "queue_status": "queued",
            }
            (batch_dir / "yomi_reading_input.jsonl").write_text(
                json.dumps(item, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            calls: list[str] = []

            def fake_run_llm_task(config_path: str, input_path: str, output_path: str, **kwargs):
                calls.append(config_path)
                if output_path.endswith("yomi_reading_results.jsonl"):
                    result = {
                        "item_id": item["item_id"],
                        "raw_text": '{"下":"した"}',
                        "parsed": {"下": "した"},
                    }
                else:
                    result = {
                        "item_id": item["item_id"],
                        "raw_text": '{"上":"うえ"}',
                        "parsed": {"上": "うえ"},
                    }
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_text(
                    json.dumps(result, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(
                    status="completed",
                    remote_status="",
                    remote_batch_id="",
                    completed_items=1,
                    failed_items=0,
                    total_items=1,
                )

            with patch("yomi_corpus.pipeline.run_llm_task", side_effect=fake_run_llm_task):
                summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "yomi_reading_llm_completed")
            self.assertEqual(
                calls,
                [
                    "config/llm/yomi_reading.toml",
                    "config/llm/yomi_reading.toml",
                ],
            )
            artifacts = summary["artifacts"]
            self.assertEqual(artifacts["yomi_reading_retry2_queued"], "1")
            self.assertEqual(artifacts["yomi_reading_matched"], "1")
            self.assertEqual(artifacts["yomi_reading_parse_error"], "0")
            output_row = json.loads(
                (batch_dir / "units.yomi.llm_readings.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            judgment = output_row["analysis"]["llm"]["yomi_readings"]["items"][0]
            self.assertEqual(judgment["status"], "matched")

    def test_advance_generates_yomi_from_scope_triaged_keep_units(self) -> None:
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
                        "units_written": 2,
                    }
                ),
                encoding="utf-8",
            )
            scope_triaged_path = batch_dir / "units.scope_triaged.jsonl"
            scope_triaged_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "大学です。",
                        "analysis": {"llm": {"scope_triage": {"status": "Keep"}}},
                    },
                    ensure_ascii=False,
                )
                + "\n"
                + json.dumps(
                    {
                        "unit_id": "u2",
                        "text": "古文です。",
                        "analysis": {"llm": {"scope_triage": {"status": "Skip"}}},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch("yomi_corpus.pipeline.export_named_variant") as mocked:
                mocked.return_value = {"variant_name": "aligned_hybrid"}
                summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "yomi_generated")
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(
                Path(mocked.call_args.kwargs["input_jsonl"]).resolve(),
                scope_triaged_path.resolve(),
            )
            self.assertTrue(mocked.call_args.kwargs["skip_scope_skipped"])

    def test_yomi_auto_acceptance_uses_batch_policy_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            input_path = batch_dir / "units.yomi.aligned_hybrid.jsonl"
            input_path.write_text("", encoding="utf-8")
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
                        "yomi_policy": {
                            "unit_mode": "sentence",
                            "auto_accept_profile": "off",
                        },
                        "llm_policy": {
                            "yomi_reading": "economy",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = workspace._infer_batch_state("dev_batch_0001")
            state.current_stage = "yomi_generated"
            workspace.save_batch_state(state)

            class FakeSummary:
                rule = "rule"
                auto_accept_profile = "off"
                accepted = 0
                rejected = 0
                stable_two_kanji_enabled = False

            with patch("yomi_corpus.pipeline.apply_yomi_auto_acceptance_file") as mocked:
                mocked.return_value = FakeSummary()
                summary = workspace._auto_accept_mechanical_yomi("dev_batch_0001")

            self.assertEqual(mocked.call_args.kwargs["auto_accept_profile"], "off")
            self.assertEqual(summary["artifacts"]["yomi_auto_accept_profile"], "off")

    def test_final_review_apply_blocks_without_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = make_final_review_batch(root)
            state = workspace._infer_batch_state("dev_batch_0001")
            state.current_stage = "final_review_prepared"
            state.artifacts["final_review_pack_id"] = "pack_1"
            workspace.save_batch_state(state)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch("yomi_corpus.pipeline.import_open_issue_inbox") as mocked_import:
                mocked_import.return_value = {
                    "imported_submission_count": 0,
                    "summaries": [],
                    "skipped": [],
                }
                summary = workspace.advance("dev")

            self.assertFalse(summary["advanced"])
            self.assertEqual(summary["current_stage"], "final_review_prepared")
            self.assertIn("No yomi final review submissions", summary["blocking_reason"])
            self.assertEqual(mocked_import.call_count, 1)
            self.assertEqual(summary["artifacts"]["final_review_issue_import_status"], "ok")
            self.assertEqual(summary["artifacts"]["final_review_issue_imported_submissions"], "0")
            self.assertEqual(
                Path(summary["artifacts"]["final_review_submission_store"]).resolve(),
                (root / "data" / "review_submissions" / "yomi_final").resolve(),
            )
            self.assertTrue(batch_dir.exists())

    def test_final_review_prepare_creates_document_review_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            make_final_review_batch(root)

            with patch("yomi_corpus.review_site.publish_review_site") as mocked_publish:
                mocked_publish.return_value = {"default_stage": "yomi_final_review"}
                summary = workspace._prepare_final_review("dev_batch_0001")

            state_path = root / "data" / "pipeline" / "document_states" / "dev_batch_0001.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["document_count"], 1)
            self.assertEqual(payload["summary"]["state_counts"]["final_pending"], 1)
            self.assertEqual(
                Path(summary["artifacts"]["document_review_state_json"]).resolve(),
                state_path.resolve(),
            )
            self.assertEqual(summary["artifacts"]["document_review_state_final_pending"], "1")

    def test_final_review_no_escalation_path_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            make_final_review_batch(root)
            store_dir = root / "data" / "review_submissions" / "yomi_final"
            store_dir.mkdir(parents=True)
            (store_dir / "s1.json").write_text(
                json.dumps(
                    {
                        "submission_type": "review_patch",
                        "review_stage": "yomi_final_review",
                        "pack_id": "pack_1",
                        "submission_id": "s1",
                        "generated_at_epoch": 1,
                        "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                        "overrides": [
                            {
                                "item_id": "u1",
                                "targets": [
                                    {
                                        "item_id": "u1:r0001c01",
                                        "choice_source": "llm",
                                        "selected_reading": "ちかぢか",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            state = workspace._infer_batch_state("dev_batch_0001")
            state.current_stage = "final_review_prepared"
            state.artifacts["final_review_pack_id"] = "pack_1"
            workspace.save_batch_state(state)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            with patch("yomi_corpus.pipeline.import_open_issue_inbox") as mocked_import:
                mocked_import.return_value = {
                    "imported_submission_count": 0,
                    "summaries": [],
                    "skipped": [],
                }
                first = workspace.advance("dev")
            second = workspace.advance("dev")
            third = workspace.advance("dev")
            fourth = workspace.advance("dev")

            self.assertEqual(first["current_stage"], "final_review_applied")
            self.assertEqual(mocked_import.call_count, 1)
            self.assertEqual(first["artifacts"]["final_review_issue_import_status"], "ok")
            self.assertEqual(second["current_stage"], "yomi_strong_repair_queued")
            self.assertEqual(third["current_stage"], "yomi_strong_repair_llm_completed")
            self.assertEqual(fourth["current_stage"], "yomi_finalized")
            self.assertEqual(fourth["artifacts"]["yomi_final_written_units"], "1")
            final_row = json.loads(
                (root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertEqual(
                final_row["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/チカヂカ です/デス 。/。",
            )

    def test_finalize_imports_strong_repair_review_submission_from_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
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
                        "target_documents": 1,
                        "docs_written": 1,
                        "units_written": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            unit = {
                "unit_id": "u1",
                "text": "池尻中学校です。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "池尻/イケジリ 中学校/チュウガッコウ です/デス 。/。"
                        }
                    },
                    "human_review": {
                        "yomi_final": {
                            "reviewed": True,
                            "skip": False,
                        }
                    },
                },
            }
            (batch_dir / "units.yomi.strong_repaired.jsonl").write_text(
                json.dumps(unit, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (batch_dir / "yomi_strong_repair_queue_summary.json").write_text(
                json.dumps({"queued_items": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            (batch_dir / "yomi_strong_repair_apply_summary.json").write_text(
                json.dumps({"stage_complete": True, "applied_items": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            (batch_dir / "yomi_strong_repair_review_pack.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_stage": "yomi_strong_repair_review",
                        "pack_id": "strong_pack_1",
                        "track_name": "dev",
                        "batch_name": "dev_batch_0001",
                        "item_count": 1,
                        "items": [
                            {
                                "item_id": "u1::target_group:1",
                                "seq": 1,
                                "unit_id": "u1",
                                "rejected_span": "池尻中学校",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_import(**kwargs):
                self.assertEqual(kwargs["review_stage"], "yomi_strong_repair_review")
                store_dir = kwargs["submission_store_dir"]
                store_dir.mkdir(parents=True, exist_ok=True)
                (store_dir / "strong_sub_1.json").write_text(
                    json.dumps(
                        {
                            "submission_type": "review_patch",
                            "review_stage": "yomi_strong_repair_review",
                            "pack_id": "strong_pack_1",
                            "submission_id": "strong_sub_1",
                            "generated_at_epoch": 1,
                            "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                            "overrides": [],
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "review_stage": "yomi_strong_repair_review",
                    "imported_submission_count": 1,
                    "summaries": [],
                    "skipped": [],
                }

            with patch("yomi_corpus.pipeline.import_open_issue_inbox", side_effect=fake_import):
                summary = workspace._finalize_yomi("dev_batch_0001")

            self.assertEqual(summary["artifacts"]["yomi_final_written_units"], "1")
            self.assertEqual(
                summary["artifacts"]["yomi_strong_repair_review_issue_import_status"],
                "ok",
            )
            self.assertEqual(
                summary["artifacts"]["yomi_strong_repair_review_imported_submissions"],
                "1",
            )
            strong_apply = json.loads(
                (batch_dir / "yomi_strong_repair_apply_summary.json").read_text(encoding="utf-8")
            )
            self.assertTrue(strong_apply["confirmed"])
            self.assertTrue((batch_dir / "units.yomi.final.jsonl").exists())

    def test_advance_runs_alphabetic_judgment_and_ingests_ledger(self) -> None:
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
            unresolved = {
                "entity_key": "ok",
                "strict_case": True,
                "resolved_status": "unknown",
                "base_list_status": "unknown",
                "occurrence_count": 3,
                "unit_count": 3,
                "surface_forms": ["OK"],
                "example_unit_ids": ["u1"],
                "example_texts": ["OKを押してください。"],
            }
            (batch_dir / "alphabetic_unresolved_entities.jsonl").write_text(
                json.dumps(unresolved, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            saved = workspace.load_batch_state("dev_batch_0001")
            saved.current_stage = "alphabetic_reported"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            def fake_run_llm_task(*args, **kwargs):
                results_path = Path(args[2])
                results_path.parent.mkdir(parents=True, exist_ok=True)
                results_path.write_text(
                    json.dumps(
                        {
                            "item_id": "ok",
                            "parsed": {
                                "status": "in_scope",
                                "confidence": "high",
                                "note": "common usage",
                            },
                            "parse_error": None,
                            "metadata": {"source_row": unresolved},
                            "usage": {},
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(
                    status="completed",
                    remote_status="",
                    remote_batch_id="",
                    completed_items=1,
                    failed_items=0,
                    total_items=1,
                )

            with patch("yomi_corpus.pipeline.run_llm_task", side_effect=fake_run_llm_task):
                summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_llm_judged")
            ledger_path = root / "data" / "state" / "alphabetic" / "llm_judgments.jsonl"
            rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["entity_key"], "ok")
            self.assertEqual(rows[0]["llm_status"], "in_scope")
            decisions_path = root / "data" / "state" / "alphabetic" / "token_decisions.jsonl"
            decisions = [
                json.loads(line)
                for line in decisions_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(decisions[0]["entity_key"], "ok")
            self.assertEqual(decisions[0]["status"], "in_scope")
            self.assertEqual(decisions[0]["source"], "llm")

    def test_working_track_projects_alphabetic_status_without_review_gate(self) -> None:
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
            ledger_path = root / "data" / "state" / "alphabetic" / "llm_judgments.jsonl"
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(
                json.dumps(
                    {
                        "batch_name": "batch_0001",
                        "entity_key": "ok",
                        "strict_case": True,
                        "llm_status": "in_scope",
                        "confidence": "high",
                        "note": "common",
                        "occurrence_count": 3,
                        "unit_count": 3,
                        "surface_forms": ["OK"],
                        "example_unit_ids": ["u1"],
                        "example_texts": ["OKを押してください。"],
                        "source_path": "x.jsonl",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("batch_0001"))
            saved = workspace.load_batch_state("batch_0001")
            saved.current_stage = "alphabetic_llm_judged"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="working",
                    current_batch_name="batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("working")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_promotion_candidates")
            self.assertIsNone(summary["blocking_reason"])
            self.assertNotIn("human_review_required", summary["artifacts"])
            self.assertTrue((batch_dir / "alphabetic_promotion_candidates_summary.json").exists())
            saved_after = workspace.load_batch_state("batch_0001")
            self.assertEqual(saved_after.current_stage, "alphabetic_promotion_candidates")

    def test_dev_track_projects_alphabetic_status_without_skip_review_gate(self) -> None:
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
            ledger_path = root / "data" / "state" / "alphabetic" / "llm_judgments.jsonl"
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(
                json.dumps(
                    {
                        "batch_name": "dev_batch_0001",
                        "entity_key": "ok",
                        "strict_case": True,
                        "llm_status": "in_scope",
                        "confidence": "high",
                        "note": "common",
                        "occurrence_count": 3,
                        "unit_count": 3,
                        "surface_forms": ["OK"],
                        "example_unit_ids": ["u1"],
                        "example_texts": ["OKを押してください。"],
                        "source_path": "x.jsonl",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            saved = workspace.load_batch_state("dev_batch_0001")
            saved.current_stage = "alphabetic_llm_judged"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_promotion_candidates")
            self.assertNotIn("human_review_required", summary["artifacts"])
            self.assertEqual(summary["skipped_review_gates"], [])
            saved_after = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved_after.current_stage, "alphabetic_promotion_candidates")

    def test_dev_skip_review_gates_is_ignored_for_alphabetic_projection(self) -> None:
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
            ledger_path = root / "data" / "state" / "alphabetic" / "llm_judgments.jsonl"
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(
                json.dumps(
                    {
                        "batch_name": "dev_batch_0001",
                        "entity_key": "ok",
                        "strict_case": True,
                        "llm_status": "in_scope",
                        "confidence": "high",
                        "note": "common",
                        "occurrence_count": 3,
                        "unit_count": 3,
                        "surface_forms": ["OK"],
                        "example_unit_ids": ["u1"],
                        "example_texts": ["OKを押してください。"],
                        "source_path": "x.jsonl",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            saved = workspace.load_batch_state("dev_batch_0001")
            saved.current_stage = "alphabetic_llm_judged"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("dev", skip_review_gates=True)

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_promotion_candidates")
            self.assertNotIn("human_review_required", summary["artifacts"])
            self.assertEqual(summary["skipped_review_gates"], [])
            saved_after = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved_after.current_stage, "alphabetic_promotion_candidates")
            self.assertEqual(saved_after.skipped_review_gates, [])

    def test_advance_queues_scope_triage_after_alphabetic_promotion_candidates(self) -> None:
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
            (batch_dir / "units.alphabetic.jsonl").write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "方です。",
                        "analysis": {"mechanical": {"alphabetic": {}}},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (batch_dir / "alphabetic_promotion_candidates_summary.json").write_text(
                "{}", encoding="utf-8"
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "scope_triage_queued")
            self.assertIsNone(summary["blocking_reason"])
            self.assertTrue((batch_dir / "scope_triage_input.jsonl").exists())
            queued = [
                json.loads(line)
                for line in (batch_dir / "scope_triage_input.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(queued[0]["unit_id"], "u1")

    def test_advance_runs_scope_triage_after_queueing(self) -> None:
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
                        "units_written": 2,
                    }
                ),
                encoding="utf-8",
            )
            (batch_dir / "units.alphabetic.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "unit_id": "u1",
                                "text": "大学です。",
                                "analysis": {"mechanical": {"alphabetic": {}}},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "unit_id": "u2",
                                "text": "方です。",
                                "analysis": {"mechanical": {"alphabetic": {}}},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (batch_dir / "scope_triage_input.jsonl").write_text(
                json.dumps(
                    {
                        "unit_id": "u2",
                        "text": "方です。",
                        "rendered": "方/ホウ です/デス 。/。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (batch_dir / "scope_triage_queue_summary.json").write_text("{}", encoding="utf-8")
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            def fake_run_llm_task(
                task_config_path: str,
                input_jsonl_path: str,
                output_jsonl_path: str,
                **kwargs: object,
            ) -> object:
                self.assertEqual(task_config_path, "config/llm/scope_triage.toml")
                self.assertEqual(Path(input_jsonl_path).resolve(), (batch_dir / "scope_triage_input.jsonl").resolve())
                self.assertEqual(kwargs["execution_mode"], "sync")
                self.assertEqual(kwargs["task_config_override"].model, "gpt-5.4-mini")
                Path(output_jsonl_path).write_text(
                    json.dumps(
                        {
                            "item_id": "u2",
                            "raw_text": "Keep",
                            "parsed": {"status": "Keep"},
                            "parse_error": None,
                            "usage": {
                                "input_tokens": 10,
                                "cached_input_tokens": 0,
                                "output_tokens": 1,
                                "reasoning_tokens": 0,
                                "total_tokens": 11,
                            },
                            "metadata": {},
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                class Summary:
                    status = "completed"
                    completed_items = 1
                    total_items = 1
                    failed_items = 0
                    remote_status = None
                    remote_batch_id = None

                return Summary()

            with patch("yomi_corpus.pipeline.run_llm_task", side_effect=fake_run_llm_task) as mocked:
                summary = workspace.advance("dev", llm_execution_mode_override="sync")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "scope_triage_llm_completed")
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(summary["artifacts"]["scope_triage_llm_profile"], "economy")
            self.assertEqual(summary["artifacts"]["scope_triage_model"], "gpt-5.4-mini")
            self.assertEqual(summary["artifacts"]["scope_triage_execution_mode"], "sync")
            rows = [
                json.loads(line)
                for line in (batch_dir / "units.scope_triaged.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            statuses = {
                row["unit_id"]: row["analysis"]["llm"]["scope_triage"]["status"]
                for row in rows
            }
            self.assertEqual(statuses, {"u1": "Keep", "u2": "Keep"})
            self.assertTrue((batch_dir / "scope_triage_usage_summary.json").exists())

    def test_batch_scope_triage_does_not_advance_until_results_are_fetched(self) -> None:
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
                        "units_written": 1,
                        "llm_execution_policy": {"scope_triage": "batch"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (batch_dir / "units.yomi.auto_accept.jsonl").write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "方です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "方/ホウ です/デス 。/。",
                                    "auto_accept": {"value": False},
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (batch_dir / "scope_triage_input.jsonl").write_text(
                json.dumps(
                    {"unit_id": "u1", "text": "方です。"},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))
            saved = workspace.load_batch_state("dev_batch_0001")
            saved.current_stage = "scope_triage_queued"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            class Summary:
                status = "running"
                completed_items = 0
                total_items = 1
                failed_items = 0
                remote_status = "in_progress"
                remote_batch_id = "batch_1"

            with patch("yomi_corpus.pipeline.run_llm_task", return_value=Summary()):
                summary = workspace.advance("dev")

            self.assertFalse(summary["advanced"])
            self.assertEqual(summary["current_stage"], "scope_triage_queued")
            self.assertIn("LLM batch job is running", summary["blocking_reason"])
            self.assertEqual(summary["artifacts"]["scope_triage_execution_mode"], "batch")
            self.assertEqual(summary["artifacts"]["scope_triage_llm_remote_status"], "in_progress")
            saved_after = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved_after.current_stage, "scope_triage_queued")

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

    def test_generate_mechanical_yomi_uses_pinned_decoder_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = PipelineWorkspace(root)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "units.scope_triaged.jsonl").write_text("", encoding="utf-8")
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
                        "decoder_model_dir": "/tmp/model-dev-m1",
                    }
                ),
                encoding="utf-8",
            )
            workspace.save_batch_state(workspace._infer_batch_state("dev_batch_0001"))

            with patch("yomi_corpus.pipeline.export_named_variant") as mocked:
                mocked.return_value = {"variant_name": "aligned_hybrid"}
                summary = workspace._generate_mechanical_yomi("dev_batch_0001")

            self.assertEqual(mocked.call_args.kwargs["decoder_model_dir"], "/tmp/model-dev-m1")
            self.assertEqual(summary["artifacts"]["decoder_model_dir"], "/tmp/model-dev-m1")

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


def make_final_review_batch(root: Path) -> Path:
    batch_dir = root / "data" / "units" / "dev_batch_0001"
    batch_dir.mkdir(parents=True)
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
                "units_written": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    unit = {
        "doc_id": "doc1",
        "unit_id": "u1",
        "unit_seq": 1,
        "text": "近々です。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": "近々/キンキン です/デス 。/。",
                }
            },
            "safety": {
                "yomi": {
                    "targets": [
                        {
                            "item_id": "u1:r0001c01",
                            "unit_id": "u1",
                            "token_index": 0,
                            "chunk_index": 0,
                            "surface": "近々",
                            "token_surface": "近々",
                            "current_reading": "キンキン",
                            "current_reading_hiragana": "きんきん",
                            "target_start": 0,
                            "target_end": 2,
                            "is_safe": False,
                            "review_status": "unresolved",
                            "highlight_level": "target",
                            "accepted_signal_names": [],
                            "signals": [
                                {
                                    "name": "safe_by_llm_match",
                                    "accepted": False,
                                    "status": "mismatched",
                                    "llm_reading": "ちかぢか",
                                    "current_reading_hiragana": "きんきん",
                                }
                            ],
                            "status_reason": "llm_reading_mismatched",
                        }
                    ]
                }
            },
        },
    }
    (batch_dir / "units.jsonl").write_text(json.dumps(unit, ensure_ascii=False) + "\n", encoding="utf-8")
    (batch_dir / "units.yomi.llm_readings.jsonl").write_text(
        json.dumps(unit, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    pack = {
        "schema_version": 1,
        "review_stage": "yomi_final_review",
        "pack_id": "pack_1",
        "track_name": "dev",
        "batch_name": "dev_batch_0001",
        "item_count": 1,
        "summary": {
            "document_count": 1,
            "unresolved_item_count": 1,
            "unresolved_target_count": 1,
            "provisional_skip_item_count": 0,
        },
        "items": [
            {
                "item_id": "u1",
                "seq": 1,
                "doc_id": "doc1",
                "doc_seq": 1,
                "unit_id": "u1",
                "text": "近々です。",
                "skip_default": False,
                "targets": [
                    {
                        "item_id": "u1:r0001c01",
                        "surface": "近々",
                        "token_surface": "近々",
                        "token_index": 0,
                        "chunk_index": 0,
                        "current_reading_hiragana": "きんきん",
                    }
                ],
            }
        ],
    }
    (batch_dir / "final_review_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return batch_dir


if __name__ == "__main__":
    unittest.main()
