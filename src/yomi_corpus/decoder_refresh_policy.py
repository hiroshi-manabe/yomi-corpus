from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.decoder_models import list_finalized_batches
from yomi_corpus.pipeline import PipelineWorkspace


DECODER_REFRESH_MODE_NEVER = "never"
DECODER_REFRESH_MODE_ON_FINALIZE = "on-finalize"
DECODER_REFRESH_MODE_ALWAYS = "always"
DECODER_REFRESH_MODES = {
    DECODER_REFRESH_MODE_NEVER,
    DECODER_REFRESH_MODE_ON_FINALIZE,
    DECODER_REFRESH_MODE_ALWAYS,
}


@dataclass(frozen=True)
class DecoderRefreshPolicy:
    mode: str = DECODER_REFRESH_MODE_NEVER
    min_new_batches: int = 1
    min_interval_minutes: float = 0.0
    skip_kenlm: bool = False


def build_decoder_refresh_plan(
    *,
    workspace: PipelineWorkspace,
    track_name: str,
    policy: DecoderRefreshPolicy,
    now: datetime | None = None,
) -> dict[str, Any]:
    mode = policy.mode.replace("_", "-")
    if mode not in DECODER_REFRESH_MODES:
        raise ValueError(f"Unsupported decoder refresh mode: {policy.mode}")
    finalized_batches = list_finalized_batches(workspace, track_name)
    previous_batches = previous_decoder_refresh_batches(workspace, track_name)
    new_since_refresh = sorted(set(finalized_batches) - set(previous_batches))
    previous_refreshed_at = previous_decoder_refresh_time(workspace, track_name)
    min_interval_seconds = max(0.0, policy.min_interval_minutes) * 60.0
    current_time = now or datetime.now(timezone.utc)
    interval_elapsed = (
        None
        if previous_refreshed_at is None
        else max(0.0, current_time.timestamp() - previous_refreshed_at.timestamp())
    )
    interval_satisfied = interval_elapsed is None or interval_elapsed >= min_interval_seconds
    min_new = max(1, int(policy.min_new_batches or 1))
    enough_new_batches = len(new_since_refresh) >= min_new
    mode_allows = mode == DECODER_REFRESH_MODE_ALWAYS or (
        mode == DECODER_REFRESH_MODE_ON_FINALIZE and bool(new_since_refresh)
    )
    will_refresh = (
        mode != DECODER_REFRESH_MODE_NEVER
        and mode_allows
        and enough_new_batches
        and interval_satisfied
    )
    reason = None
    if mode == DECODER_REFRESH_MODE_NEVER:
        reason = "mode_never"
    elif not mode_allows:
        reason = "no_unrefreshed_finalized_batches"
    elif not enough_new_batches:
        reason = "min_new_batches_not_met"
    elif not interval_satisfied:
        reason = "min_interval_not_met"
    return {
        "status": "planned" if will_refresh else "skipped",
        "will_refresh": will_refresh,
        "reason": reason,
        "mode": mode,
        "track_name": track_name,
        "finalized_batches": finalized_batches,
        "previous_refreshed_batches": previous_batches,
        "new_since_refresh": new_since_refresh,
        "min_new_batches": min_new,
        "min_interval_minutes": max(0.0, policy.min_interval_minutes),
        "interval_elapsed_seconds": interval_elapsed,
        "skip_kenlm": policy.skip_kenlm,
    }


def previous_decoder_refresh_batches(
    workspace: PipelineWorkspace,
    track_name: str,
) -> list[str]:
    manifest = previous_decoder_refresh_manifest(workspace, track_name)
    if not manifest:
        return []
    batches = manifest.get("finalized_batches")
    if not isinstance(batches, list):
        return []
    return [str(batch) for batch in batches]


def previous_decoder_refresh_time(
    workspace: PipelineWorkspace,
    track_name: str,
) -> datetime | None:
    manifest = previous_decoder_refresh_manifest(workspace, track_name)
    value = (
        manifest.get("refreshed_at") or manifest.get("created_at")
        if manifest
        else None
    )
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def previous_decoder_refresh_manifest(
    workspace: PipelineWorkspace,
    track_name: str,
) -> dict[str, Any] | None:
    track_state = workspace.load_track_state(track_name)
    model_dir = getattr(track_state, "decoder_model_dir", None)
    if not model_dir:
        return None
    manifest_path = Path(model_dir) / "yomi_corpus_refresh.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
