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
            self.assertEqual(summary["current_stage"], "alphabetic_judged")
            ledger_path = root / "data" / "state" / "alphabetic" / "llm_judgments.jsonl"
            rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["entity_key"], "ok")
            self.assertEqual(rows[0]["llm_status"], "in_scope")

    def test_working_track_blocks_on_alphabetic_promotion_candidates(self) -> None:
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
            saved.current_stage = "alphabetic_judged"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="working",
                    current_batch_name="batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("working")

            self.assertFalse(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_judged")
            self.assertIn("human review", summary["blocking_reason"])
            self.assertEqual(summary["artifacts"]["human_review_required"], "true")
            self.assertEqual(summary["artifacts"]["human_review_gate"], "promotion_candidate_review")
            self.assertTrue((batch_dir / "alphabetic_promotion_candidates_summary.json").exists())
            saved_after = workspace.load_batch_state("batch_0001")
            self.assertEqual(saved_after.current_stage, "alphabetic_judged")

    def test_dev_track_blocks_on_alphabetic_promotion_candidates_without_skip(self) -> None:
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
            saved.current_stage = "alphabetic_judged"
            workspace.save_batch_state(saved)
            workspace.save_track_state(
                TrackState(
                    track_name="dev",
                    current_batch_name="dev_batch_0001",
                    updated_at="2026-04-09T00:00:00Z",
                )
            )

            summary = workspace.advance("dev")

            self.assertFalse(summary["advanced"])
            self.assertEqual(summary["current_stage"], "alphabetic_judged")
            self.assertEqual(summary["artifacts"]["human_review_required"], "true")
            self.assertEqual(summary["skipped_review_gates"], [])
            saved_after = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved_after.current_stage, "alphabetic_judged")

    def test_dev_track_can_skip_alphabetic_promotion_review_explicitly(self) -> None:
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
            saved.current_stage = "alphabetic_judged"
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
            self.assertEqual(summary["artifacts"]["human_review_required"], "true")
            self.assertEqual(summary["artifacts"]["human_review_skipped"], "true")
            self.assertEqual(summary["skipped_review_gates"], ["promotion_candidate_review"])
            saved_after = workspace.load_batch_state("dev_batch_0001")
            self.assertEqual(saved_after.current_stage, "alphabetic_promotion_candidates")
            self.assertEqual(saved_after.skipped_review_gates, ["promotion_candidate_review"])

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
                self.assertEqual(kwargs["execution_mode"], "background")
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
                summary = workspace.advance("dev")

            self.assertTrue(summary["advanced"])
            self.assertEqual(summary["current_stage"], "scope_triage_completed")
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(summary["artifacts"]["scope_triage_llm_profile"], "economy")
            self.assertEqual(summary["artifacts"]["scope_triage_model"], "gpt-5.4-mini")
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
