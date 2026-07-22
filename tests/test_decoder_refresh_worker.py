from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.decoder_models import DecoderModelRefreshSummary
from yomi_corpus.decoder_refresh_worker import (
    DecoderRefreshWorkerOptions,
    clear_matching_decoder_refresh_request,
    run_decoder_refresh_worker_pass,
)
from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.review_sync import (
    ReviewSyncOptions,
    decoder_refresh_request_path,
    request_decoder_model_refresh,
)


def test_decoder_refresh_worker_is_idle_without_request(tmp_path: Path) -> None:
    result = run_decoder_refresh_worker_pass(
        tmp_path,
        DecoderRefreshWorkerOptions(track_name="dev", mode="on-finalize", dry_run=True),
    )

    assert result["status"] == "idle"


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
    workspace = PipelineWorkspace(root)
    batch_name = "dev_batch_0001"
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
    result = request_decoder_model_refresh(
        root=root,
        workspace=workspace,
        options=ReviewSyncOptions(track_name="dev", decoder_refresh_mode="on-finalize"),
        newly_finalized_batches=[batch_name],
    )
    assert result["status"] == "queued"
    return Path(result["request_path"])
