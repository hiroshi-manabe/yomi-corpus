from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.decoder_models import refresh_decoder_model
from yomi_corpus.decoder_refresh_policy import (
    DECODER_REFRESH_MODE_NEVER,
    DecoderRefreshPolicy,
    build_decoder_refresh_plan,
)
from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.review_sync import ReviewSyncLock


DECODER_REFRESH_WORKER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DecoderRefreshWorkerOptions:
    track_name: str
    mode: str
    min_new_batches: int = 1
    min_interval_minutes: float = 0.0
    skip_kenlm: bool = False
    dry_run: bool = False


def run_decoder_refresh_worker_pass(
    root: Path,
    options: DecoderRefreshWorkerOptions,
) -> dict[str, Any]:
    state_dir = root / "data" / "state" / "decoder_refresh"
    lock_path = state_dir / f"{options.track_name}.lock"
    with ReviewSyncLock(lock_path, label="Decoder refresh worker"):
        summary = _run_decoder_refresh_worker_pass_unlocked(root=root, options=options)
    if not options.dry_run:
        summary_path = write_decoder_refresh_worker_summary(
            root,
            options.track_name,
            summary,
        )
        summary["summary_json"] = str(summary_path)
    return summary


def _run_decoder_refresh_worker_pass_unlocked(
    *,
    root: Path,
    options: DecoderRefreshWorkerOptions,
) -> dict[str, Any]:
    started_at_epoch = int(time.time())
    request_path = decoder_refresh_request_path(root, options.track_name)
    request = read_json_object(request_path)
    workspace = PipelineWorkspace(root)
    plan = build_decoder_refresh_plan(
        workspace=workspace,
        track_name=options.track_name,
        policy=DecoderRefreshPolicy(
            mode=options.mode,
            min_new_batches=options.min_new_batches,
            min_interval_minutes=options.min_interval_minutes,
            skip_kenlm=options.skip_kenlm,
        ),
    )
    if not plan["will_refresh"]:
        terminal = plan.get("reason") in {
            "mode_never",
            "no_unrefreshed_finalized_batches",
        }
        cleared = (
            clear_matching_decoder_refresh_request(request_path, request)
            if request is not None and terminal and not options.dry_run
            else False
        )
        return worker_summary(
            options=options,
            started_at_epoch=started_at_epoch,
            status=(
                "disabled"
                if plan.get("reason") == "mode_never"
                else ("idle" if terminal else "waiting")
            ),
            request_path=request_path,
            request=request,
            plan=plan,
            request_cleared=cleared,
        )

    if options.dry_run:
        return worker_summary(
            options=options,
            started_at_epoch=started_at_epoch,
            status="planned",
            request_path=request_path,
            request=request,
            plan=plan,
        )

    try:
        refresh = refresh_decoder_model(
            root=root,
            track_name=options.track_name,
            skip_kenlm=options.skip_kenlm,
            capture_build_output=True,
        )
    except Exception as exc:  # noqa: BLE001 - retain the request for a later retry.
        return worker_summary(
            options=options,
            started_at_epoch=started_at_epoch,
            status="failed",
            request_path=request_path,
            request=request,
            plan=plan,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    cleared = (
        clear_matching_decoder_refresh_request(request_path, request)
        if request is not None
        else False
    )
    return worker_summary(
        options=options,
        started_at_epoch=started_at_epoch,
        status="refreshed",
        request_path=request_path,
        request=request,
        plan=plan,
        request_cleared=cleared,
        refresh=asdict(refresh),
    )


def clear_matching_decoder_refresh_request(
    request_path: Path,
    completed_request: dict[str, Any],
) -> bool:
    current = read_json_object(request_path)
    if current is None:
        return False
    if current.get("request_id") != completed_request.get("request_id"):
        return False
    try:
        request_path.unlink()
    except FileNotFoundError:
        return False
    return True


def decoder_refresh_request_path(root: Path, track_name: str) -> Path:
    return root / "data" / "state" / "decoder_refresh" / f"{track_name}.request.json"


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def worker_summary(
    *,
    options: DecoderRefreshWorkerOptions,
    started_at_epoch: int,
    status: str,
    request_path: Path,
    request: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    request_cleared: bool = False,
    refresh: dict[str, Any] | None = None,
    error: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    completed_at_epoch = int(time.time())
    return {
        "schema_version": DECODER_REFRESH_WORKER_SCHEMA_VERSION,
        "track_name": options.track_name,
        "started_at": epoch_iso(started_at_epoch),
        "completed_at": epoch_iso(completed_at_epoch),
        "duration_seconds": completed_at_epoch - started_at_epoch,
        "status": status,
        "dry_run": options.dry_run,
        "request_path": str(request_path),
        "request_id": None if request is None else request.get("request_id"),
        "request_cleared": request_cleared,
        "policy": {
            "mode": options.mode,
            "min_new_batches": options.min_new_batches,
            "min_interval_minutes": options.min_interval_minutes,
            "skip_kenlm": options.skip_kenlm,
        },
        "plan": plan,
        "refresh": refresh,
        "error": error,
        "error_type": error_type,
    }


def write_decoder_refresh_worker_summary(
    root: Path,
    track_name: str,
    summary: dict[str, Any],
) -> Path:
    state_dir = root / "data" / "state" / "decoder_refresh"
    state_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    historical = state_dir / f"{track_name}.{timestamp}.json"
    latest = state_dir / f"{track_name}.last.json"
    write_json_atomic(historical, summary)
    write_json_atomic(latest, summary)
    return latest


def epoch_iso(epoch: int) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
