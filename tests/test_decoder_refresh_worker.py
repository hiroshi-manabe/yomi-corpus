from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.decoder_models import DecoderModelRefreshSummary
from yomi_corpus.decoder_refresh_worker import (
    DecoderRefreshWorkerOptions,
    clear_matching_decoder_refresh_request,
    decoder_refresh_request_path,
    run_decoder_refresh_worker_pass,
)
from yomi_corpus.decoder_refresh_policy import (
    DecoderRefreshPolicy,
    build_decoder_refresh_plan,
)
from yomi_corpus.pipeline import PipelineWorkspace


def test_decoder_refresh_worker_is_idle_without_request(tmp_path: Path) -> None:
    result = run_decoder_refresh_worker_pass(
        tmp_path,
        DecoderRefreshWorkerOptions(track_name="dev", mode="on-finalize", dry_run=True),
    )

    assert result["status"] == "idle"


def test_scheduled_worker_waits_without_request_below_threshold(tmp_path: Path) -> None:
    write_finalized_batch(tmp_path, "dev_batch_0001")

    result = run_decoder_refresh_worker_pass(
        tmp_path,
        DecoderRefreshWorkerOptions(
            track_name="dev",
            mode="on-finalize",
            min_new_batches=20,
            dry_run=True,
        ),
    )

    assert result["status"] == "waiting"
    assert result["request_id"] is None
    assert result["plan"]["reason"] == "min_new_batches_not_met"


def test_scheduled_worker_refreshes_without_request_at_threshold(tmp_path: Path) -> None:
    for number in range(1, 3):
        write_finalized_batch(tmp_path, f"dev_batch_{number:04d}")
    refresh = DecoderModelRefreshSummary(
        track_name="dev",
        finalized_batches=["dev_batch_0001", "dev_batch_0002"],
        exported_corpora=["corpus.txt"],
        model_dir="model",
        build_script="build.py",
        base_corpus="base.txt",
        corpus_frequency_stats_artifact="stats.tsv",
        corpus_frequency_manifest="stats.json",
        skip_kenlm=False,
        track_state_path="track.json",
        refreshed_at="2026-07-22T00:00:00Z",
    )

    with patch(
        "yomi_corpus.decoder_refresh_worker.refresh_decoder_model",
        return_value=refresh,
    ):
        result = run_decoder_refresh_worker_pass(
            tmp_path,
            DecoderRefreshWorkerOptions(
                track_name="dev",
                mode="on-finalize",
                min_new_batches=2,
            ),
        )

    assert result["status"] == "refreshed"
    assert result["request_id"] is None
    assert result["request_cleared"] is False


def test_decoder_refresh_plan_respects_min_interval(tmp_path: Path) -> None:
    workspace = PipelineWorkspace(tmp_path)
    model_dir = tmp_path / "models" / "dev" / "previous"
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
    write_finalized_batch(tmp_path, "dev_batch_0001")

    plan = build_decoder_refresh_plan(
        workspace=workspace,
        track_name="dev",
        policy=DecoderRefreshPolicy(
            mode="on-finalize",
            min_interval_minutes=60,
        ),
    )

    assert plan["will_refresh"] is False
    assert plan["reason"] == "min_interval_not_met"


def test_decoder_refresh_worker_retains_failed_request(tmp_path: Path) -> None:
    request_path = prepare_request(tmp_path)

    with patch(
        "yomi_corpus.decoder_refresh_worker.refresh_decoder_model",
        side_effect=RuntimeError("build failed"),
    ):
        result = run_decoder_refresh_worker_pass(
            tmp_path,
            DecoderRefreshWorkerOptions(track_name="dev", mode="on-finalize"),
        )

    assert result["status"] == "failed"
    assert result["error"] == "build failed"
    assert request_path.exists()


def test_decoder_refresh_worker_clears_successful_request(tmp_path: Path) -> None:
    request_path = prepare_request(tmp_path)
    refresh = DecoderModelRefreshSummary(
        track_name="dev",
        finalized_batches=["dev_batch_0001"],
        exported_corpora=["corpus.txt"],
        model_dir="model",
        build_script="build.py",
        base_corpus="base.txt",
        corpus_frequency_stats_artifact="stats.tsv",
        corpus_frequency_manifest="stats.json",
        skip_kenlm=False,
        track_state_path="track.json",
        refreshed_at="2026-07-22T00:00:00Z",
    )

    with patch(
        "yomi_corpus.decoder_refresh_worker.refresh_decoder_model",
        return_value=refresh,
    ):
        result = run_decoder_refresh_worker_pass(
            tmp_path,
            DecoderRefreshWorkerOptions(track_name="dev", mode="on-finalize"),
        )

    assert result["status"] == "refreshed"
    assert result["request_cleared"] is True
    assert not request_path.exists()
    assert result["refresh"]["model_dir"] == "model"


def test_completed_worker_does_not_clear_newer_request(tmp_path: Path) -> None:
    request_path = decoder_refresh_request_path(tmp_path, "dev")
    request_path.parent.mkdir(parents=True)
    request_path.write_text(json.dumps({"request_id": "new"}), encoding="utf-8")

    cleared = clear_matching_decoder_refresh_request(
        request_path,
        {"request_id": "old"},
    )

    assert cleared is False
    assert request_path.exists()


def prepare_request(root: Path) -> Path:
    batch_name = "dev_batch_0001"
    write_finalized_batch(root, batch_name)
    request_path = decoder_refresh_request_path(root, "dev")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "legacy-request",
                "track_name": "dev",
            }
        ),
        encoding="utf-8",
    )
    return request_path


def write_finalized_batch(root: Path, batch_name: str) -> None:
    workspace = PipelineWorkspace(root)
    workspace.ensure_dirs()
    workspace.batch_state_path(batch_name).write_text(
        json.dumps(
            {
                "batch_name": batch_name,
                "track_name": "dev",
                "current_stage": "yomi_finalized",
                "updated_at": "2026-07-22T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    batch_dir = workspace.batch_dir(batch_name)
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "units.yomi.final.jsonl").write_text("{}\n", encoding="utf-8")
