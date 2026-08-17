from __future__ import annotations

import json
from pathlib import Path
import tempfile

from yomi_corpus.pipeline import (
    LLM_POLICY_TASKS,
    PipelineWorkspace,
    STAGE_PREPARED,
    STAGE_SEQUENCE,
    STAGE_YOMI_GENERATED,
)
from yomi_corpus.yomi.final_review import build_review_item
from yomi_corpus.yomi.scope_removal_migration import (
    migrate_scope_triage_removal,
    needs_review_pack_regeneration,
)


def test_active_scope_stage_migration_is_idempotent_and_backed_up() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_dir = root / "data" / "pipeline" / "batches"
        batch_dir = root / "data" / "units" / "dev_batch_0001"
        state_dir.mkdir(parents=True)
        batch_dir.mkdir(parents=True)
        state_path = state_dir / "dev_batch_0001.json"
        manifest_path = batch_dir / "manifest.json"
        state = {
            "batch_name": "dev_batch_0001",
            "track_name": "dev",
            "current_stage": "scope_triage_queued",
            "docs_written": 2,
            "units_written": 3,
            "llm_policy": {
                "scope_triage": "economy",
                "alphabetic_entity_judge": "standard",
                "yomi_reading": "standard",
            },
            "llm_execution_policy": {
                "scope_triage": "background",
                "yomi_reading": "background",
            },
            "artifacts": {
                "units_jsonl": str(batch_dir / "units.jsonl"),
                "scope_triage_input_jsonl": str(batch_dir / "scope_triage_input.jsonl"),
                "alphabetic_unresolved_jsonl": str(batch_dir / "alphabetic.jsonl"),
            },
            "blocking_reason": "remote classifier running",
            "updated_at": "2026-08-17T00:00:00Z",
        }
        manifest = {
            "batch_name": "dev_batch_0001",
            "llm_policy": dict(state["llm_policy"]),
            "llm_execution_policy": dict(state["llm_execution_policy"]),
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        remote_job = root / "data" / "llm" / "jobs" / "dev_batch_0001_scope_triage"
        remote_job.mkdir(parents=True)

        dry_run = migrate_scope_triage_removal(root=root, track_name="dev", dry_run=True)

        assert dry_run["changed_batches"] == 1
        assert dry_run["batches"][0]["returned_to_prepared"] is True
        assert dry_run["batches"][0]["remote_job_paths"] == [str(remote_job)]
        assert json.loads(state_path.read_text())["current_stage"] == "scope_triage_queued"

        applied = migrate_scope_triage_removal(root=root, track_name="dev", dry_run=False)
        migrated = json.loads(state_path.read_text())
        migrated_manifest = json.loads(manifest_path.read_text())

        assert applied["changed_batches"] == 1
        assert migrated["current_stage"] == STAGE_PREPARED
        assert migrated["blocking_reason"] is None
        assert migrated["llm_policy"] == {"yomi_reading": "standard"}
        assert migrated["llm_execution_policy"] == {"yomi_reading": "background"}
        assert migrated["artifacts"] == {"units_jsonl": str(batch_dir / "units.jsonl")}
        assert migrated_manifest["llm_policy"] == {"yomi_reading": "standard"}
        backup = (
            root
            / "data"
            / "migrations"
            / "scope_triage_removal"
            / "backups"
            / "dev_batch_0001"
            / state_path.name
        )
        assert json.loads(backup.read_text())["current_stage"] == "scope_triage_queued"

        repeated = migrate_scope_triage_removal(root=root, track_name="dev", dry_run=False)
        assert repeated["changed_batches"] == 0


def test_active_pipeline_starts_yomi_directly_and_has_no_classifier_tasks() -> None:
    assert STAGE_SEQUENCE[:2] == [STAGE_PREPARED, STAGE_YOMI_GENERATED]
    assert "alphabetic_entity_judge" not in LLM_POLICY_TASKS
    assert "scope_triage" not in LLM_POLICY_TASKS


def test_review_item_ignores_historical_machine_scope_default() -> None:
    item = build_review_item(
        {
            "doc_id": "doc1",
            "unit_id": "doc1:u1",
            "text": "京都タワーです。",
            "analysis": {
                "llm": {"scope_triage": {"status": "Skip"}},
                "mechanical": {
                    "alphabetic_scope": {"provisional_skip": True},
                    "yomi": {"rendered": "京都/キョウト タワー/タワー です/デス 。/。"},
                },
                "safety": {"yomi": {"targets": []}},
            },
        },
        seq=1,
        doc_seq=1,
        track_doc_seq=1,
    )

    assert "scope_default" not in item
    assert "skip_default" not in item
    assert "provisional_skip" not in item


def test_review_pack_regeneration_detects_interrupted_and_legacy_packs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = PipelineWorkspace(Path(tmp))
        pack_path = workspace.batch_dir("dev_batch_0001") / "final_review_pack.json"
        pack_path.parent.mkdir(parents=True)

        pack_path.write_text('{"items": [', encoding="utf-8")
        assert needs_review_pack_regeneration(workspace, "dev_batch_0001") is True

        pack_path.write_text(
            json.dumps({"items": [{"item_id": "u1", "scope_default": "Skip"}]}),
            encoding="utf-8",
        )
        assert needs_review_pack_regeneration(workspace, "dev_batch_0001") is True

        pack_path.write_text(
            json.dumps({"items": [{"item_id": "u1"}]}),
            encoding="utf-8",
        )
        assert needs_review_pack_regeneration(workspace, "dev_batch_0001") is False
