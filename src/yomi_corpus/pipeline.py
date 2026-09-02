from __future__ import annotations

import gzip
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import tomllib

from yomi_corpus.document_review_state import (
    build_initial_document_review_state,
    load_document_review_state,
    mark_document_review_state_finalized,
    update_document_review_state_after_final_review,
    update_document_review_state_after_strong_queue,
    update_document_review_state_after_strong_review,
    write_document_review_state,
)
from yomi_corpus.llm.config import apply_llm_profile, load_llm_task_config
from yomi_corpus.llm.parsers import validate_yomi_repair_surface
from yomi_corpus.paths import resolve_repo_path
from yomi_corpus.llm.pricing import DEFAULT_PRICING_CONFIG_PATH
from yomi_corpus.llm.runner import run_llm_task
from yomi_corpus.llm.usage_report import summarize_results_jsonl
from yomi_corpus.models import UnitRecord, empty_analysis
from yomi_corpus.processing_order import ProcessingOrderStore
from yomi_corpus.recovery_documents import build_application_ledger, iter_jsonl, write_jsonl
from yomi_corpus.splitter import split_text_into_units
from yomi_corpus.yomi.acceptance import apply_yomi_auto_acceptance_file
from yomi_corpus.yomi.adapters import run_sudachi_many
from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.export import export_named_variant
from yomi_corpus.yomi.final_review import (
    STRONG_REPAIR_REVIEW_STAGE,
    apply_final_review_file,
    apply_strong_repair_review_file,
    apply_yomi_strong_repair_results_file,
    build_strong_repair_queue_file,
    build_yomi_strong_repair_review_pack_file,
    build_yomi_final_review_pack_file,
    finalize_reviewed_yomi_file,
    harvest_yomi_finalization_artifacts_file,
    materialize_yomi_review_units_file,
    write_summary as write_yomi_final_review_summary,
)
from yomi_corpus.yomi.final_review_issue_import import import_open_issue_inbox
from yomi_corpus.yomi.llm_readings import (
    apply_yomi_llm_reading_results_file,
    build_yomi_llm_reading_queue_file,
    build_yomi_llm_reading_retry_queue_file,
)
from yomi_corpus.yomi.safety import apply_yomi_safety_pre_llm_file


WORKING_TRACK = "working"
DEV_TRACK = "dev"
DEFAULT_TRACK = WORKING_TRACK
PROTECTED_TRACKS = frozenset({WORKING_TRACK})
DEPRECATED_ARTIFACT_KEYS = frozenset(
    {
        "final_review_escalated_units",
        "yomi_strong_repair_sentence_escalations",
    }
)
DEFAULT_PIPELINE_DEFAULTS_CONFIG_PATH = "config/pipeline/defaults.toml"
YOMI_STRONG_REPAIR_RESPONSE_RETRIES = 3
YOMI_UNIT_MODE_SENTENCE = "sentence"
YOMI_UNIT_MODE_COMMA_SPAN = "comma_span"
YOMI_UNIT_MODES = frozenset({YOMI_UNIT_MODE_SENTENCE, YOMI_UNIT_MODE_COMMA_SPAN})
YOMI_AUTO_ACCEPT_PROFILE_OFF = "off"
YOMI_AUTO_ACCEPT_PROFILE_STRICT = "strict"
YOMI_AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI = "stable_two_kanji"
YOMI_AUTO_ACCEPT_PROFILES = frozenset(
    {
        YOMI_AUTO_ACCEPT_PROFILE_OFF,
        YOMI_AUTO_ACCEPT_PROFILE_STRICT,
        YOMI_AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI,
    }
)
LLM_PROFILE_SMOKE = "smoke"
LLM_PROFILE_ECONOMY = "economy"
LLM_PROFILE_STANDARD = "standard"
LLM_PROFILE_STRONG = "strong"
LLM_PROFILES = frozenset(
    {
        LLM_PROFILE_SMOKE,
        LLM_PROFILE_ECONOMY,
        LLM_PROFILE_STANDARD,
        LLM_PROFILE_STRONG,
    }
)
LLM_EXECUTION_MODE_SYNC = "sync"
LLM_EXECUTION_MODE_BACKGROUND = "background"
LLM_EXECUTION_MODE_BATCH = "batch"
LLM_EXECUTION_MODES = frozenset(
    {LLM_EXECUTION_MODE_SYNC, LLM_EXECUTION_MODE_BACKGROUND, LLM_EXECUTION_MODE_BATCH}
)
LLM_TASK_YOMI_READING = "yomi_reading"
LLM_TASK_YOMI_REPAIR = "yomi_repair"
LLM_TASK_YOMI_RESCUE = "yomi_rescue"
RETIRED_LLM_POLICY_TASKS = frozenset(
    {"alphabetic_entity_judge", "scope_triage"}
)
LLM_POLICY_TASKS = (
    LLM_TASK_YOMI_READING,
    LLM_TASK_YOMI_REPAIR,
    LLM_TASK_YOMI_RESCUE,
)
LLM_POLICY_TASK_SET = frozenset(LLM_POLICY_TASKS)
YOMI_LLM_PROFILE_PRODUCTION = "production"
YOMI_LLM_PROFILE_DEV = "dev"
YOMI_LLM_PROFILE_SMOKE = "smoke"
YOMI_LLM_PROFILE_RESCUE = "rescue"
LEGACY_YOMI_LLM_PROFILE_MAP = {
    YOMI_LLM_PROFILE_PRODUCTION: LLM_PROFILE_STANDARD,
    YOMI_LLM_PROFILE_DEV: LLM_PROFILE_ECONOMY,
    YOMI_LLM_PROFILE_SMOKE: LLM_PROFILE_SMOKE,
    YOMI_LLM_PROFILE_RESCUE: LLM_PROFILE_STRONG,
}
LEGACY_YOMI_LLM_PROFILES = frozenset(
    LEGACY_YOMI_LLM_PROFILE_MAP
)
STAGE_PREPARED = "prepared"
STAGE_YOMI_GENERATED = "yomi_generated"
STAGE_YOMI_AUTO_ACCEPTED = "yomi_auto_accepted"
STAGE_YOMI_READING_QUEUED = "yomi_reading_queued"
STAGE_YOMI_READING_LLM_COMPLETED = "yomi_reading_llm_completed"
STAGE_FINAL_REVIEW_PREPARED = "final_review_prepared"
STAGE_FINAL_REVIEW_APPLIED = "final_review_applied"
STAGE_YOMI_STRONG_REPAIR_QUEUED = "yomi_strong_repair_queued"
STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED = "yomi_strong_repair_llm_completed"
STAGE_YOMI_FINALIZED = "yomi_finalized"
LEGACY_STAGE_MAP = {
    "alphabetic_analyzed": STAGE_PREPARED,
    "alphabetic_reported": STAGE_PREPARED,
    "alphabetic_judged": STAGE_PREPARED,
    "alphabetic_llm_judged": STAGE_PREPARED,
    "alphabetic_promotion_candidates": STAGE_PREPARED,
    "scope_triage_queued": STAGE_PREPARED,
    "scope_triage_completed": STAGE_PREPARED,
    "scope_triage_llm_completed": STAGE_PREPARED,
    "yomi_reading_completed": STAGE_YOMI_READING_LLM_COMPLETED,
}
STAGE_LLM_TASKS = {
    STAGE_YOMI_READING_LLM_COMPLETED: LLM_TASK_YOMI_READING,
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED: LLM_TASK_YOMI_REPAIR,
}

TRACKS: dict[str, dict[str, str]] = {
    WORKING_TRACK: {
        "batch_prefix": "batch_",
        "batch_kind": WORKING_TRACK,
        "pipeline_profile": WORKING_TRACK,
    },
    DEV_TRACK: {
        "batch_prefix": "dev_batch_",
        "batch_kind": DEV_TRACK,
        "pipeline_profile": DEV_TRACK,
    },
}

STAGE_SEQUENCE = [
    STAGE_PREPARED,
    STAGE_YOMI_GENERATED,
    STAGE_YOMI_AUTO_ACCEPTED,
    STAGE_YOMI_READING_QUEUED,
    STAGE_YOMI_READING_LLM_COMPLETED,
    STAGE_FINAL_REVIEW_PREPARED,
    STAGE_FINAL_REVIEW_APPLIED,
    STAGE_YOMI_STRONG_REPAIR_QUEUED,
    STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
    STAGE_YOMI_FINALIZED,
]

RERUNNABLE_STAGES = frozenset(
    {
        STAGE_YOMI_GENERATED,
        STAGE_YOMI_AUTO_ACCEPTED,
        STAGE_YOMI_READING_QUEUED,
        STAGE_YOMI_READING_LLM_COMPLETED,
        STAGE_FINAL_REVIEW_PREPARED,
        STAGE_FINAL_REVIEW_APPLIED,
        STAGE_YOMI_STRONG_REPAIR_QUEUED,
        STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
        STAGE_YOMI_FINALIZED,
    }
)


def existing_pack_created_at_epoch(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        value = int(payload.get("created_at_epoch"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@dataclass
class TrackState:
    track_name: str
    current_batch_name: str | None
    updated_at: str
    decoder_model_dir: str | None = None


@dataclass
class BatchState:
    batch_name: str
    track_name: str
    batch_kind: str
    pipeline_profile: str
    dataset_name: str
    dataset_config_path: str
    dataset_source_path: str
    target_documents: int
    docs_written: int
    units_written: int
    current_stage: str
    yomi_policy: dict[str, str]
    llm_policy: dict[str, str]
    llm_execution_policy: dict[str, str]
    blocking_reason: str | None
    skipped_review_gates: list[str]
    artifacts: dict[str, str]
    updated_at: str
    decoder_model_dir: str | None = None


@dataclass(frozen=True)
class EmptyJobSummary:
    status: str = "completed"
    remote_status: str = ""
    remote_batch_id: str = ""
    completed_items: int = 0
    failed_items: int = 0
    total_items: int = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_working_track(track_name: str) -> bool:
    return track_name == WORKING_TRACK


def is_protected_track(track_name: str) -> bool:
    return track_name in PROTECTED_TRACKS


def requires_strict_human_review_gates(track_name: str) -> bool:
    return is_protected_track(track_name)


def prune_deprecated_artifact_keys(artifacts: dict[str, str]) -> None:
    for key in DEPRECATED_ARTIFACT_KEYS:
        artifacts.pop(key, None)


def track_policy_name(track_name: str) -> str:
    return "strict" if requires_strict_human_review_gates(track_name) else "relaxed"


def normalize_track_name(name: str | None) -> str:
    track_name = name or DEFAULT_TRACK
    if track_name not in TRACKS:
        raise ValueError(f"Unknown track: {track_name}")
    return track_name


def normalize_stage_name(stage_name: str) -> str:
    return LEGACY_STAGE_MAP.get(stage_name, stage_name)


def llm_task_for_stage(stage_name: str) -> str | None:
    return STAGE_LLM_TASKS.get(normalize_stage_name(stage_name))


def default_yomi_policy(track_name: str) -> dict[str, str]:
    config = load_pipeline_defaults_config()
    tracks = config.get("tracks")
    if not isinstance(tracks, dict):
        raise ValueError("Pipeline defaults config must define [tracks]")
    track = tracks.get(track_name)
    if not isinstance(track, dict):
        raise ValueError(f"Pipeline defaults config has no track: {track_name}")
    yomi_policy = track.get("yomi_policy")
    if not isinstance(yomi_policy, dict):
        raise ValueError(f"Pipeline defaults config has no yomi_policy for track: {track_name}")
    return {
        "unit_mode": str(yomi_policy["unit_mode"]),
        "auto_accept_profile": str(yomi_policy["auto_accept_profile"]),
    }


def load_pipeline_defaults_config(
    path: str | Path = DEFAULT_PIPELINE_DEFAULTS_CONFIG_PATH,
) -> dict[str, object]:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def normalize_yomi_policy(
    policy: dict[str, object] | None,
    *,
    track_name: str,
) -> dict[str, str]:
    normalized = default_yomi_policy(track_name)
    if policy:
        if "unit_mode" in policy:
            normalized["unit_mode"] = str(policy["unit_mode"])
        if "auto_accept_profile" in policy:
            normalized["auto_accept_profile"] = str(policy["auto_accept_profile"])
    if normalized["unit_mode"] not in YOMI_UNIT_MODES:
        raise ValueError(f"Unsupported yomi unit mode: {normalized['unit_mode']}")
    if normalized["auto_accept_profile"] not in YOMI_AUTO_ACCEPT_PROFILES:
        raise ValueError(
            f"Unsupported yomi auto-accept profile: {normalized['auto_accept_profile']}"
        )
    return normalized


def default_llm_policy(track_name: str) -> dict[str, str]:
    config = load_pipeline_defaults_config()
    tracks = config.get("tracks")
    if not isinstance(tracks, dict):
        raise ValueError("Pipeline defaults config must define [tracks]")
    track = tracks.get(track_name)
    if not isinstance(track, dict):
        raise ValueError(f"Pipeline defaults config has no track: {track_name}")
    llm_policy = track.get("llm_policy")
    if not isinstance(llm_policy, dict):
        raise ValueError(f"Pipeline defaults config has no llm_policy for track: {track_name}")
    return {task: str(llm_policy[task]) for task in LLM_POLICY_TASKS}


def normalize_llm_policy(
    policy: dict[str, object] | None,
    *,
    track_name: str,
    legacy_yomi_policy: dict[str, object] | None = None,
) -> dict[str, str]:
    normalized = default_llm_policy(track_name)
    legacy_profile = None
    if legacy_yomi_policy and "llm_profile" in legacy_yomi_policy:
        legacy_profile = str(legacy_yomi_policy["llm_profile"])
    if legacy_profile:
        if legacy_profile not in LEGACY_YOMI_LLM_PROFILES:
            raise ValueError(f"Unsupported legacy yomi LLM profile: {legacy_profile}")
        normalized[LLM_TASK_YOMI_READING] = LEGACY_YOMI_LLM_PROFILE_MAP[legacy_profile]
    if policy:
        for task, profile in policy.items():
            task_name = str(task)
            if task_name in RETIRED_LLM_POLICY_TASKS:
                continue
            if task_name not in LLM_POLICY_TASK_SET:
                raise ValueError(f"Unsupported LLM task in policy: {task_name}")
            normalized[task_name] = str(profile)
    for task, profile in normalized.items():
        if profile not in LLM_PROFILES:
            raise ValueError(f"Unsupported LLM profile for {task}: {profile}")
    return normalized


def default_llm_execution_policy(track_name: str) -> dict[str, str]:
    config = load_pipeline_defaults_config()
    tracks = config.get("tracks")
    if not isinstance(tracks, dict):
        raise ValueError("Pipeline defaults config must define [tracks]")
    track = tracks.get(track_name)
    if not isinstance(track, dict):
        raise ValueError(f"Pipeline defaults config has no track: {track_name}")
    policy = track.get("llm_execution_policy")
    if not isinstance(policy, dict):
        return {task: LLM_EXECUTION_MODE_SYNC for task in LLM_POLICY_TASKS}
    return {task: str(policy.get(task, LLM_EXECUTION_MODE_SYNC)) for task in LLM_POLICY_TASKS}


def normalize_llm_execution_policy(
    policy: dict[str, object] | None,
    *,
    track_name: str,
) -> dict[str, str]:
    normalized = default_llm_execution_policy(track_name)
    if policy:
        for task, mode in policy.items():
            task_name = str(task)
            if task_name in RETIRED_LLM_POLICY_TASKS:
                continue
            if task_name not in LLM_POLICY_TASK_SET:
                raise ValueError(f"Unsupported LLM task in execution policy: {task_name}")
            normalized[task_name] = str(mode)
    for task, mode in normalized.items():
        if mode not in LLM_EXECUTION_MODES:
            raise ValueError(f"Unsupported LLM execution mode for {task}: {mode}")
    return normalized


def strong_repair_review_results_path(batch_dir: Path) -> Path:
    effective = batch_dir / "yomi_strong_repair_effective_results.jsonl"
    if effective.exists():
        return effective
    return batch_dir / "yomi_strong_repair_results.jsonl"


class PipelineWorkspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def units_root(self) -> Path:
        return self.root / "data" / "units"

    def pipeline_root(self) -> Path:
        return self.root / "data" / "pipeline"

    def tracks_root(self) -> Path:
        return self.pipeline_root() / "tracks"

    def batches_root(self) -> Path:
        return self.pipeline_root() / "batches"

    def document_states_root(self) -> Path:
        return self.pipeline_root() / "document_states"

    def document_ledger_root(self) -> Path:
        return self.pipeline_root() / "document_ledger"

    def track_state_path(self, track_name: str) -> Path:
        return self.tracks_root() / f"{track_name}.json"

    def batch_state_path(self, batch_name: str) -> Path:
        return self.batches_root() / f"{batch_name}.json"

    def document_review_state_path(self, batch_name: str) -> Path:
        return self.document_states_root() / f"{batch_name}.json"

    def document_ledger_path(self, track_name: str) -> Path:
        return self.document_ledger_root() / f"{normalize_track_name(track_name)}.json"

    def processing_order_store(self, track_name: str) -> ProcessingOrderStore:
        return ProcessingOrderStore(self.root, normalize_track_name(track_name))

    def batch_dir(self, batch_name: str) -> Path:
        return self.units_root() / batch_name

    def manifest_path(self, batch_name: str) -> Path:
        return self.batch_dir(batch_name) / "manifest.json"

    def ensure_dirs(self) -> None:
        self.tracks_root().mkdir(parents=True, exist_ok=True)
        self.batches_root().mkdir(parents=True, exist_ok=True)
        self.document_states_root().mkdir(parents=True, exist_ok=True)
        self.document_ledger_root().mkdir(parents=True, exist_ok=True)

    def load_track_state(self, track_name: str) -> TrackState:
        normalized = normalize_track_name(track_name)
        path = self.track_state_path(normalized)
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            return TrackState(
                track_name=str(payload["track_name"]),
                current_batch_name=payload.get("current_batch_name"),
                updated_at=str(payload["updated_at"]),
                decoder_model_dir=payload.get("decoder_model_dir"),
            )

        inferred_batch_name = self._infer_latest_batch_name_for_track(normalized)
        state = TrackState(
            track_name=normalized,
            current_batch_name=inferred_batch_name,
            updated_at=now_iso(),
            decoder_model_dir=None,
        )
        self.save_track_state(state)
        return state

    def save_track_state(self, state: TrackState) -> None:
        self.ensure_dirs()
        self.track_state_path(state.track_name).write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_batch_state(self, batch_name: str) -> BatchState:
        path = self.batch_state_path(batch_name)
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            track_name = str(payload["track_name"])
            payload.setdefault("skipped_review_gates", [])
            payload["current_stage"] = normalize_stage_name(str(payload["current_stage"]))
            raw_yomi_policy = payload.get("yomi_policy")
            payload["yomi_policy"] = normalize_yomi_policy(
                raw_yomi_policy,
                track_name=track_name,
            )
            payload["llm_policy"] = normalize_llm_policy(
                payload.get("llm_policy"),
                track_name=track_name,
                legacy_yomi_policy=raw_yomi_policy,
            )
            payload["llm_execution_policy"] = normalize_llm_execution_policy(
                payload.get("llm_execution_policy"),
                track_name=track_name,
            )
            payload.setdefault("decoder_model_dir", None)
            return BatchState(**payload)
        return self._infer_batch_state(batch_name)

    def save_batch_state(self, state: BatchState) -> None:
        self.ensure_dirs()
        self.batch_state_path(state.batch_name).write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def status(self, track_name: str | None = None) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        track_state = self.load_track_state(normalized)
        if not track_state.current_batch_name:
            return {
                "track_name": normalized,
                "current_batch_name": None,
                "message": "No current batch is set for this track.",
            }
        batch_state = self.load_batch_state(track_state.current_batch_name)
        return {
            "track_name": normalized,
            "track_policy": track_policy_name(normalized),
            "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
            "current_batch_name": batch_state.batch_name,
            "current_stage": batch_state.current_stage,
            "blocking_reason": batch_state.blocking_reason,
            "skipped_review_gates": batch_state.skipped_review_gates,
            "yomi_policy": batch_state.yomi_policy,
            "llm_policy": batch_state.llm_policy,
            "llm_execution_policy": batch_state.llm_execution_policy,
            "decoder_model_dir": batch_state.decoder_model_dir,
            "artifacts": batch_state.artifacts,
            "target_documents": batch_state.target_documents,
            "docs_written": batch_state.docs_written,
            "units_written": batch_state.units_written,
            "next_stage": self._next_stage_name(batch_state.current_stage),
            "updated_at": batch_state.updated_at,
        }

    def batch_status(self, batch_name: str) -> dict[str, object]:
        batch_state = self.load_batch_state(batch_name)
        normalized = normalize_track_name(batch_state.track_name)
        return {
            "track_name": normalized,
            "track_policy": track_policy_name(normalized),
            "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
            "current_batch_name": batch_state.batch_name,
            "batch_name": batch_state.batch_name,
            "current_stage": batch_state.current_stage,
            "blocking_reason": batch_state.blocking_reason,
            "skipped_review_gates": batch_state.skipped_review_gates,
            "yomi_policy": batch_state.yomi_policy,
            "llm_policy": batch_state.llm_policy,
            "llm_execution_policy": batch_state.llm_execution_policy,
            "decoder_model_dir": batch_state.decoder_model_dir,
            "artifacts": batch_state.artifacts,
            "target_documents": batch_state.target_documents,
            "docs_written": batch_state.docs_written,
            "units_written": batch_state.units_written,
            "next_stage": self._next_stage_name(batch_state.current_stage),
            "updated_at": batch_state.updated_at,
        }

    def set_stage(
        self,
        track_name: str | None,
        stage_name: str,
        *,
        allow_protected: bool = False,
        allow_forward: bool = False,
    ) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        target_stage = normalize_stage_name(stage_name)
        if target_stage not in STAGE_SEQUENCE:
            raise ValueError(f"Unsupported pipeline stage: {stage_name}")
        track_state = self.load_track_state(normalized)
        if not track_state.current_batch_name:
            return {
                "track_name": normalized,
                "track_policy": track_policy_name(normalized),
                "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                "stage_changed": False,
                "message": "No current batch is set for this track. Run prepare first.",
            }
        batch_state = self.load_batch_state(track_state.current_batch_name)
        previous_stage = batch_state.current_stage
        previous_index = STAGE_SEQUENCE.index(previous_stage)
        target_index = STAGE_SEQUENCE.index(target_stage)
        base_summary = {
            "track_name": normalized,
            "track_policy": track_policy_name(normalized),
            "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
            "batch_name": batch_state.batch_name,
            "stage_changed": False,
            "previous_stage": previous_stage,
            "current_stage": previous_stage,
            "requested_stage": target_stage,
            "next_stage": self._next_stage_name(previous_stage),
            "skipped_review_gates": batch_state.skipped_review_gates,
            "yomi_policy": batch_state.yomi_policy,
            "llm_policy": batch_state.llm_policy,
            "llm_execution_policy": batch_state.llm_execution_policy,
            "artifacts": batch_state.artifacts,
        }
        if target_stage == previous_stage:
            return {
                **base_summary,
                "message": "Stage is already set.",
            }
        if target_index > previous_index and not allow_forward:
            return {
                **base_summary,
                "blocking_reason": (
                    f"Refusing to move stage forward from {previous_stage} to {target_stage}. "
                    "Use ./next to advance the pipeline."
                ),
            }
        if requires_strict_human_review_gates(normalized) and not allow_protected:
            return {
                **base_summary,
                "requires_confirmation": True,
                "blocking_reason": (
                    f"Changing the protected working-track stage from {previous_stage} "
                    f"to {target_stage} requires confirmation."
                ),
            }

        batch_state.current_stage = target_stage
        batch_state.blocking_reason = self._blocking_reason_for_stage(target_stage)
        batch_state.updated_at = now_iso()
        self.save_batch_state(batch_state)
        return {
            **base_summary,
            "stage_changed": True,
            "current_stage": target_stage,
            "next_stage": self._next_stage_name(target_stage),
            "blocking_reason": batch_state.blocking_reason,
            "updated_at": batch_state.updated_at,
        }

    def prepare_next_batch(
        self,
        *,
        track_name: str | None,
        target_documents: int,
        dataset_config_path: str = "config/datasets/ja_cc_level2.toml",
        yomi_policy: dict[str, object] | None = None,
        llm_policy: dict[str, object] | None = None,
        llm_execution_policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        normalized_yomi_policy = normalize_yomi_policy(yomi_policy, track_name=normalized)
        normalized_llm_policy = normalize_llm_policy(llm_policy, track_name=normalized)
        normalized_llm_execution_policy = normalize_llm_execution_policy(
            llm_execution_policy,
            track_name=normalized,
        )
        dataset = self._load_dataset_config(dataset_config_path)
        track_state = self.load_track_state(normalized)
        decoder_model_dir = track_state.decoder_model_dir
        ledger = self._load_document_ledger(normalized)
        order_store = self.processing_order_store(normalized)
        order_manifest = order_store.ensure(
            source_path=Path(dataset["source_path"]),
            dataset_name=str(dataset["name"]),
            ledger_rows=ledger.get("documents", []),
        )
        active_reservation = order_manifest.get("reservation")
        if isinstance(active_reservation, dict):
            batch_name = str(active_reservation["batch_name"])
            reservation = active_reservation
        else:
            batch_name = self._allocate_next_batch_name(normalized)
            reservation = order_store.reserve(
                batch_name=batch_name,
                count=target_documents,
            )
        assignments = ProcessingOrderStore.reservation_assignments(reservation)

        (
            docs_written,
            units_written,
            source_start_line_no,
            source_end_line_no,
        ) = self._extract_batch_documents(
            source_path=dataset["source_path"],
            dataset_name=dataset["name"],
            batch_name=batch_name,
            track_name=normalized,
            assignments=assignments,
        )

        manifest_payload = {
            "batch_name": batch_name,
            "track_name": normalized,
            "batch_kind": TRACKS[normalized]["batch_kind"],
            "pipeline_profile": TRACKS[normalized]["pipeline_profile"],
            "dataset_name": dataset["name"],
            "dataset_config_path": dataset_config_path,
            "dataset_source_path": str(dataset["source_path"]),
            "target_documents": target_documents,
            "docs_written": docs_written,
            "units_written": units_written,
            "source_start_line_no": source_start_line_no,
            "source_end_line_no": source_end_line_no,
            "processing_order_generation": order_manifest["order_generation"],
            "processing_slot_start": assignments[0]["processing_slot"] if assignments else None,
            "processing_slot_end": assignments[-1]["processing_slot"] if assignments else None,
            "processing_order_assignments": assignments,
            "unit_schema_version": 1,
            "mechanical_analysis_initialized": True,
            "yomi_policy": normalized_yomi_policy,
            "llm_policy": normalized_llm_policy,
            "llm_execution_policy": normalized_llm_execution_policy,
            "decoder_model_dir": decoder_model_dir,
        }
        self.batch_dir(batch_name).mkdir(parents=True, exist_ok=True)
        self.manifest_path(batch_name).write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        batch_state = BatchState(
            batch_name=batch_name,
            track_name=normalized,
            batch_kind=TRACKS[normalized]["batch_kind"],
            pipeline_profile=TRACKS[normalized]["pipeline_profile"],
            dataset_name=dataset["name"],
            dataset_config_path=dataset_config_path,
            dataset_source_path=str(dataset["source_path"]),
            target_documents=target_documents,
            docs_written=docs_written,
            units_written=units_written,
            current_stage="prepared",
            yomi_policy=normalized_yomi_policy,
            llm_policy=normalized_llm_policy,
            llm_execution_policy=normalized_llm_execution_policy,
            decoder_model_dir=decoder_model_dir,
            blocking_reason=None,
            skipped_review_gates=[],
            artifacts={
                "units_jsonl": str(self.batch_dir(batch_name) / "units.jsonl"),
                "manifest": str(self.manifest_path(batch_name)),
            },
            updated_at=now_iso(),
        )
        self.save_batch_state(batch_state)
        self.save_track_state(
            TrackState(
                track_name=normalized,
                current_batch_name=batch_name,
                decoder_model_dir=decoder_model_dir,
                updated_at=now_iso(),
            )
        )
        order_store.commit_reservation(batch_name)
        return {
            "track_name": normalized,
            "batch_name": batch_name,
            "target_documents": target_documents,
            "docs_written": docs_written,
            "units_written": units_written,
            "current_stage": "prepared",
            "yomi_policy": normalized_yomi_policy,
            "llm_policy": normalized_llm_policy,
            "llm_execution_policy": normalized_llm_execution_policy,
            "decoder_model_dir": decoder_model_dir,
        }

    def prepare_recovery_batch(
        self,
        *,
        campaign_dir: Path,
        track_name: str = DEV_TRACK,
        batch_name: str | None = None,
        yomi_policy: dict[str, object] | None = None,
        llm_policy: dict[str, object] | None = None,
        llm_execution_policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        campaign_manifest = json.loads(
            (campaign_dir / "campaign.json").read_text(encoding="utf-8")
        )
        campaign_id = str(campaign_manifest.get("campaign_id") or "").strip()
        if not campaign_id:
            raise ValueError("Recovery campaign manifest does not define campaign_id.")
        recovery_units = list(iter_jsonl(campaign_dir / "recovery_units.jsonl"))
        recovery_documents = list(iter_jsonl(campaign_dir / "recovery_documents.jsonl"))
        units_by_id = {
            str(row["recovery_unit_id"]): row
            for row in recovery_units
        }
        if len(units_by_id) != len(recovery_units):
            raise ValueError("Recovery campaign contains duplicate recovery unit IDs.")
        safe_campaign = re.sub(r"[^a-zA-Z0-9_]+", "_", campaign_id).strip("_")
        resolved_batch_name = batch_name or f"{normalized}_recovery_{safe_campaign}"
        state_path = self.batch_state_path(resolved_batch_name)
        if state_path.exists() or self.batch_dir(resolved_batch_name).exists():
            raise FileExistsError(f"Recovery batch already exists: {resolved_batch_name}")

        normalized_yomi_policy = normalize_yomi_policy(yomi_policy, track_name=normalized)
        normalized_llm_policy = normalize_llm_policy(llm_policy, track_name=normalized)
        normalized_llm_execution_policy = normalize_llm_execution_policy(
            llm_execution_policy,
            track_name=normalized,
        )
        track_state = self.load_track_state(normalized)
        output_dir = self.batch_dir(resolved_batch_name)
        output_dir.mkdir(parents=True, exist_ok=False)
        units_path = output_dir / "units.jsonl"
        written_unit_ids: set[str] = set()
        with units_path.open("w", encoding="utf-8") as handle:
            for document in recovery_documents:
                recovery_document_id = str(document["recovery_document_id"])
                recovery_document_seq = int(document["recovery_document_seq"])
                review_doc_seq = 900_000_000 + recovery_document_seq
                char_offset = 0
                for unit_seq, recovery_unit_id in enumerate(
                    document.get("recovery_unit_ids", []),
                    start=1,
                ):
                    recovery_unit_id = str(recovery_unit_id)
                    if recovery_unit_id in written_unit_ids:
                        raise ValueError(f"Recovery unit occurs more than once: {recovery_unit_id}")
                    try:
                        recovery_unit = units_by_id[recovery_unit_id]
                    except KeyError as exc:
                        raise ValueError(
                            f"Recovery document references unknown unit: {recovery_unit_id}"
                        ) from exc
                    text = str(recovery_unit["text"])
                    record = UnitRecord(
                        doc_id=recovery_document_id,
                        unit_id=recovery_unit_id,
                        unit_seq=unit_seq,
                        track_doc_seq=review_doc_seq,
                        char_start=char_offset,
                        char_end=char_offset + len(text),
                        text=text,
                        source_file=f"recovery:{campaign_id}",
                        source_line_no=int(recovery_unit["destination_source_line_no"]),
                        analysis=empty_analysis(),
                    ).to_dict()
                    record["recovery"] = recovery_unit
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    written_unit_ids.add(recovery_unit_id)
                    char_offset += len(text)
        missing = sorted(units_by_id.keys() - written_unit_ids)
        if missing:
            raise ValueError(f"Recovery units were not assigned to documents: {missing[:10]}")

        manifest_payload = {
            "batch_name": resolved_batch_name,
            "track_name": normalized,
            "batch_kind": "recovery",
            "pipeline_profile": TRACKS[normalized]["pipeline_profile"],
            "dataset_name": f"recovery:{campaign_id}",
            "dataset_config_path": "",
            "dataset_source_path": str(campaign_dir.resolve()),
            "target_documents": len(recovery_documents),
            "docs_written": len(recovery_documents),
            "units_written": len(written_unit_ids),
            "unit_schema_version": 1,
            "mechanical_analysis_initialized": True,
            "recovery_campaign_id": campaign_id,
            "recovery_campaign_manifest": str((campaign_dir / "campaign.json").resolve()),
            "canonical_export": False,
            "yomi_policy": normalized_yomi_policy,
            "llm_policy": normalized_llm_policy,
            "llm_execution_policy": normalized_llm_execution_policy,
            "decoder_model_dir": track_state.decoder_model_dir,
        }
        self.manifest_path(resolved_batch_name).write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifacts = {
            "units_jsonl": str(units_path),
            "manifest": str(self.manifest_path(resolved_batch_name)),
            "recovery_campaign_manifest_json": str(campaign_dir / "campaign.json"),
            "recovery_campaign_units_jsonl": str(campaign_dir / "recovery_units.jsonl"),
            "recovery_campaign_documents_jsonl": str(
                campaign_dir / "recovery_documents.jsonl"
            ),
        }
        self.save_batch_state(
            BatchState(
                batch_name=resolved_batch_name,
                track_name=normalized,
                batch_kind="recovery",
                pipeline_profile=TRACKS[normalized]["pipeline_profile"],
                dataset_name=f"recovery:{campaign_id}",
                dataset_config_path="",
                dataset_source_path=str(campaign_dir.resolve()),
                target_documents=len(recovery_documents),
                docs_written=len(recovery_documents),
                units_written=len(written_unit_ids),
                current_stage=STAGE_PREPARED,
                yomi_policy=normalized_yomi_policy,
                llm_policy=normalized_llm_policy,
                llm_execution_policy=normalized_llm_execution_policy,
                decoder_model_dir=track_state.decoder_model_dir,
                blocking_reason=None,
                skipped_review_gates=[],
                artifacts=artifacts,
                updated_at=now_iso(),
            )
        )
        return {
            "track_name": normalized,
            "batch_name": resolved_batch_name,
            "batch_kind": "recovery",
            "recovery_campaign_id": campaign_id,
            "docs_written": len(recovery_documents),
            "units_written": len(written_unit_ids),
            "current_stage": STAGE_PREPARED,
            "artifacts": artifacts,
        }

    def preview_next_source_documents(
        self,
        *,
        track_name: str | None,
        target_documents: int,
        dataset_config_path: str = "config/datasets/ja_cc_level2.toml",
    ) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        target = max(0, int(target_documents or 0))
        dataset = self._load_dataset_config(dataset_config_path)
        ledger = self._load_document_ledger(normalized)
        order_store = self.processing_order_store(normalized)
        order_manifest = order_store.ensure(
            source_path=Path(dataset["source_path"]),
            dataset_name=str(dataset["name"]),
            ledger_rows=ledger.get("documents", []),
        )
        assigned_doc_ids = {
            str(row.get("doc_id") or "")
            for row in ledger.get("documents", [])
            if isinstance(row, dict) and str(row.get("doc_id") or "")
        }
        documents: list[dict[str, object]] = []
        assignments: list[dict[str, int]] = []
        if target > 0:
            assignments = order_store.peek(target)
            documents = self._select_source_documents(
                source_path=Path(dataset["source_path"]),
                dataset_name=str(dataset["name"]),
                excluded_doc_ids=assigned_doc_ids,
                assignments=assignments,
            )
        return {
            "track_name": normalized,
            "dataset_name": dataset["name"],
            "dataset_config_path": dataset_config_path,
            "dataset_source_path": str(dataset["source_path"]),
            "processing_order_cursor": order_manifest["cursor"],
            "processing_order_generation": order_manifest["order_generation"],
            "processing_order_assignments": assignments,
            "requested_documents": target,
            "selected_documents": documents,
            "selected_document_count": len(documents),
        }

    def next_track_doc_seq(self, track_name: str | None) -> int:
        normalized = normalize_track_name(track_name)
        return self._next_track_doc_seq(self._load_document_ledger(normalized))

    def advance(
        self,
        track_name: str | None = None,
        *,
        force_stage: str | None = None,
        allow_overwrite: bool = False,
        skip_review_gates: bool = False,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        if llm_execution_mode_override is not None and llm_execution_mode_override not in LLM_EXECUTION_MODES:
            raise ValueError(f"Unsupported LLM execution mode override: {llm_execution_mode_override}")
        track_state = self.load_track_state(normalized)
        if not track_state.current_batch_name:
            return {
                "track_name": normalized,
                "track_policy": track_policy_name(normalized),
                "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                "advanced": False,
                "message": "No current batch is set for this track. Run prepare first.",
            }

        batch_state = self.load_batch_state(track_state.current_batch_name)
        return self.advance_batch(
            batch_state.batch_name,
            force_stage=force_stage,
            allow_overwrite=allow_overwrite,
            skip_review_gates=skip_review_gates,
            llm_execution_mode_override=llm_execution_mode_override,
        )

    def advance_batch(
        self,
        batch_name: str,
        *,
        force_stage: str | None = None,
        allow_overwrite: bool = False,
        skip_review_gates: bool = False,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        if llm_execution_mode_override is not None and llm_execution_mode_override not in LLM_EXECUTION_MODES:
            raise ValueError(f"Unsupported LLM execution mode override: {llm_execution_mode_override}")
        batch_state = self.load_batch_state(batch_name)
        normalized = normalize_track_name(batch_state.track_name)
        current_stage = batch_state.current_stage

        if force_stage is not None:
            force_stage = normalize_stage_name(force_stage)
            if force_stage != current_stage:
                return {
                    "track_name": normalized,
                    "track_policy": track_policy_name(normalized),
                    "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                    "batch_name": batch_state.batch_name,
                    "advanced": False,
                    "current_stage": current_stage,
                    "requested_force_stage": force_stage,
                    "skipped_review_gates": batch_state.skipped_review_gates,
                    "yomi_policy": batch_state.yomi_policy,
                    "llm_policy": batch_state.llm_policy,
                    "llm_execution_policy": batch_state.llm_execution_policy,
                    "blocking_reason": (
                        "Forced rerun is currently limited to the batch's current stage "
                        f"({current_stage})."
                    ),
                }
            if force_stage not in RERUNNABLE_STAGES:
                return {
                    "track_name": normalized,
                    "track_policy": track_policy_name(normalized),
                    "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                    "batch_name": batch_state.batch_name,
                    "advanced": False,
                    "current_stage": current_stage,
                    "requested_force_stage": force_stage,
                    "skipped_review_gates": batch_state.skipped_review_gates,
                    "yomi_policy": batch_state.yomi_policy,
                    "llm_policy": batch_state.llm_policy,
                    "llm_execution_policy": batch_state.llm_execution_policy,
                    "blocking_reason": f"Stage {force_stage} is not rerunnable.",
                }
            overwrite_paths = self._existing_stage_artifact_paths(
                batch_state=batch_state,
                stage_name=force_stage,
            )
            if (
                overwrite_paths
                and requires_strict_human_review_gates(normalized)
                and not allow_overwrite
            ):
                return {
                    "track_name": normalized,
                    "track_policy": track_policy_name(normalized),
                    "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                    "batch_name": batch_state.batch_name,
                    "advanced": False,
                    "current_stage": current_stage,
                    "requested_force_stage": force_stage,
                    "requires_confirmation": True,
                    "overwrite_paths": overwrite_paths,
                    "skipped_review_gates": batch_state.skipped_review_gates,
                    "yomi_policy": batch_state.yomi_policy,
                    "llm_policy": batch_state.llm_policy,
                    "llm_execution_policy": batch_state.llm_execution_policy,
                    "blocking_reason": (
                        f"Rerunning stage {force_stage} will overwrite existing artifacts "
                        "on the working track."
                    ),
                }

            if llm_execution_mode_override is not None and llm_task_for_stage(force_stage) is None:
                return self._llm_override_rejected_summary(
                    normalized=normalized,
                    batch_state=batch_state,
                    stage_name=force_stage,
                    forced=True,
                )
            summary = self._run_stage(
                batch_state.batch_name,
                force_stage,
                skip_review_gates=skip_review_gates,
                llm_execution_mode_override=llm_execution_mode_override,
            )
            if not summary.get("stage_complete", True):
                return self._persist_incomplete_stage_summary(
                    normalized=normalized,
                    batch_state=batch_state,
                    summary=summary,
                    forced=True,
                )
            batch_state.current_stage = force_stage
            batch_state.blocking_reason = self._blocking_reason_for_stage(force_stage)
            batch_state.artifacts.update(summary["artifacts"])
            prune_deprecated_artifact_keys(batch_state.artifacts)
            batch_state.skipped_review_gates = merge_review_gates(
                batch_state.skipped_review_gates,
                summary.get("skipped_review_gates", []),
            )
            batch_state.updated_at = now_iso()
            self.save_batch_state(batch_state)
            return {
                "track_name": normalized,
                "track_policy": track_policy_name(normalized),
                "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                "batch_name": batch_state.batch_name,
                "advanced": True,
                "forced": True,
                "current_stage": batch_state.current_stage,
                "next_stage": self._next_stage_name(batch_state.current_stage),
                "blocking_reason": batch_state.blocking_reason,
                "skipped_review_gates": batch_state.skipped_review_gates,
                "yomi_policy": batch_state.yomi_policy,
                "llm_policy": batch_state.llm_policy,
                "llm_execution_policy": batch_state.llm_execution_policy,
                "artifacts": batch_state.artifacts,
            }

        next_stage = self._next_stage_name(current_stage)
        if next_stage is None:
            return {
                "track_name": normalized,
                "track_policy": track_policy_name(normalized),
                "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
                "batch_name": batch_state.batch_name,
                "advanced": False,
                "current_stage": batch_state.current_stage,
                "skipped_review_gates": batch_state.skipped_review_gates,
                "yomi_policy": batch_state.yomi_policy,
                "llm_policy": batch_state.llm_policy,
                "llm_execution_policy": batch_state.llm_execution_policy,
                "blocking_reason": batch_state.blocking_reason
                or "No automated next stage is implemented for this batch.",
            }

        if llm_execution_mode_override is not None and llm_task_for_stage(next_stage) is None:
            return self._llm_override_rejected_summary(
                normalized=normalized,
                batch_state=batch_state,
                stage_name=next_stage,
                forced=False,
            )
        summary = self._run_stage(
            batch_state.batch_name,
            next_stage,
            skip_review_gates=skip_review_gates,
            llm_execution_mode_override=llm_execution_mode_override,
        )
        if not summary.get("stage_complete", True):
            return self._persist_incomplete_stage_summary(
                normalized=normalized,
                batch_state=batch_state,
                summary=summary,
                forced=False,
            )
        batch_state.current_stage = next_stage
        batch_state.blocking_reason = self._blocking_reason_for_stage(next_stage)
        batch_state.artifacts.update(summary["artifacts"])
        prune_deprecated_artifact_keys(batch_state.artifacts)
        batch_state.skipped_review_gates = merge_review_gates(
            batch_state.skipped_review_gates,
            summary.get("skipped_review_gates", []),
        )
        batch_state.updated_at = now_iso()
        self.save_batch_state(batch_state)
        return {
            "track_name": normalized,
            "track_policy": track_policy_name(normalized),
            "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
            "batch_name": batch_state.batch_name,
            "advanced": True,
            "current_stage": batch_state.current_stage,
            "next_stage": self._next_stage_name(batch_state.current_stage),
            "blocking_reason": batch_state.blocking_reason,
            "skipped_review_gates": batch_state.skipped_review_gates,
            "yomi_policy": batch_state.yomi_policy,
            "llm_policy": batch_state.llm_policy,
            "llm_execution_policy": batch_state.llm_execution_policy,
            "artifacts": batch_state.artifacts,
        }

    def _persist_incomplete_stage_summary(
        self,
        *,
        normalized: str,
        batch_state: BatchState,
        summary: dict[str, object],
        forced: bool,
    ) -> dict[str, object]:
        batch_state.artifacts.update(summary.get("artifacts", {}))
        prune_deprecated_artifact_keys(batch_state.artifacts)
        batch_state.blocking_reason = str(
            summary.get("blocking_reason") or "Stage is still running."
        )
        batch_state.updated_at = now_iso()
        self.save_batch_state(batch_state)
        result = {
            "track_name": normalized,
            "track_policy": track_policy_name(normalized),
            "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
            "batch_name": batch_state.batch_name,
            "advanced": False,
            "current_stage": batch_state.current_stage,
            "blocking_reason": batch_state.blocking_reason,
            "skipped_review_gates": batch_state.skipped_review_gates,
            "yomi_policy": batch_state.yomi_policy,
            "llm_policy": batch_state.llm_policy,
            "llm_execution_policy": batch_state.llm_execution_policy,
            "artifacts": batch_state.artifacts,
        }
        if forced:
            result["forced"] = True
        return result

    def _llm_override_rejected_summary(
        self,
        *,
        normalized: str,
        batch_state: BatchState,
        stage_name: str | None,
        forced: bool,
    ) -> dict[str, object]:
        result = {
            "track_name": normalized,
            "track_policy": track_policy_name(normalized),
            "requires_strict_human_review_gates": requires_strict_human_review_gates(normalized),
            "batch_name": batch_state.batch_name,
            "advanced": False,
            "current_stage": batch_state.current_stage,
            "next_stage": self._next_stage_name(batch_state.current_stage),
            "skipped_review_gates": batch_state.skipped_review_gates,
            "yomi_policy": batch_state.yomi_policy,
            "llm_policy": batch_state.llm_policy,
            "llm_execution_policy": batch_state.llm_execution_policy,
            "artifacts": batch_state.artifacts,
            "blocking_reason": (
                f"LLM execution mode override is only valid for LLM stages; "
                f"stage {stage_name or '-'} does not call the LLM."
            ),
        }
        if forced:
            result["forced"] = True
        return result

    def _run_stage(
        self,
        batch_name: str,
        stage_name: str,
        *,
        skip_review_gates: bool = False,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        stage_name = normalize_stage_name(stage_name)
        if stage_name == STAGE_YOMI_GENERATED:
            return self._generate_mechanical_yomi(batch_name)
        if stage_name == STAGE_YOMI_AUTO_ACCEPTED:
            return self._auto_accept_mechanical_yomi(batch_name)
        if stage_name == STAGE_YOMI_READING_QUEUED:
            return self._queue_yomi_llm_reading(batch_name)
        if stage_name == STAGE_YOMI_READING_LLM_COMPLETED:
            return self._run_yomi_llm_reading(
                batch_name,
                llm_execution_mode_override=llm_execution_mode_override,
            )
        if stage_name == STAGE_FINAL_REVIEW_PREPARED:
            return self._prepare_final_review(batch_name)
        if stage_name == STAGE_FINAL_REVIEW_APPLIED:
            return self._apply_final_review(batch_name)
        if stage_name == STAGE_YOMI_STRONG_REPAIR_QUEUED:
            return self._queue_yomi_strong_repair(batch_name)
        if stage_name == STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED:
            return self._run_yomi_strong_repair(
                batch_name,
                llm_execution_mode_override=llm_execution_mode_override,
            )
        if stage_name == STAGE_YOMI_FINALIZED:
            return self._finalize_yomi(batch_name)
        raise ValueError(f"Unsupported pipeline stage: {stage_name}")

    def _stage_artifact_paths(self, *, batch_state: BatchState, stage_name: str) -> list[Path]:
        stage_name = normalize_stage_name(stage_name)
        batch_dir = self.batch_dir(batch_state.batch_name)
        if stage_name == STAGE_YOMI_GENERATED:
            return [
                batch_dir / "units.yomi.aligned_hybrid.jsonl",
            ]
        if stage_name == STAGE_YOMI_AUTO_ACCEPTED:
            return [
                batch_dir / "units.yomi.auto_accept.jsonl",
                batch_dir / "yomi_auto_accept_summary.json",
            ]
        if stage_name == STAGE_YOMI_READING_QUEUED:
            return [
                batch_dir / "units.yomi.safety_pre_llm.jsonl",
                batch_dir / "yomi_safety_pre_llm_summary.json",
                batch_dir / "yomi_reading_input.jsonl",
                batch_dir / "yomi_reading_queue_summary.json",
            ]
        if stage_name == STAGE_YOMI_READING_LLM_COMPLETED:
            return [
                batch_dir / "yomi_reading_results.jsonl",
                batch_dir / "yomi_reading_usage_summary.json",
                batch_dir / "yomi_reading_retry_input.jsonl",
                batch_dir / "yomi_reading_retry_queue_summary.json",
                batch_dir / "yomi_reading_retry_results.jsonl",
                batch_dir / "yomi_reading_retry_usage_summary.json",
                batch_dir / "yomi_reading_retry2_input.jsonl",
                batch_dir / "yomi_reading_retry2_queue_summary.json",
                batch_dir / "yomi_reading_retry2_results.jsonl",
                batch_dir / "yomi_reading_retry2_usage_summary.json",
                batch_dir / "yomi_reading_retry3_input.jsonl",
                batch_dir / "yomi_reading_retry3_queue_summary.json",
                batch_dir / "yomi_reading_retry3_results.jsonl",
                batch_dir / "yomi_reading_retry3_usage_summary.json",
                batch_dir / "units.yomi.llm_readings.jsonl",
                batch_dir / "yomi_reading_apply_summary.json",
            ]
        if stage_name == STAGE_FINAL_REVIEW_PREPARED:
            return [
                batch_dir / "final_review_pack.json",
                batch_dir / "final_review_pack_summary.json",
                self.document_review_state_path(batch_state.batch_name),
            ]
        if stage_name == STAGE_FINAL_REVIEW_APPLIED:
            return [
                batch_dir / "units.yomi.reviewed.jsonl",
                batch_dir / "final_review_apply_summary.json",
                self.document_review_state_path(batch_state.batch_name),
            ]
        if stage_name == STAGE_YOMI_STRONG_REPAIR_QUEUED:
            return [
                batch_dir / "yomi_strong_repair_queue.jsonl",
                batch_dir / "yomi_strong_repair_queue_summary.json",
                self.document_review_state_path(batch_state.batch_name),
            ]
        if stage_name == STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED:
            return [
                batch_dir / "yomi_strong_repair_results.jsonl",
                batch_dir / "yomi_strong_repair_usage_summary.json",
                batch_dir / "units.yomi.strong_repaired.jsonl",
                batch_dir / "yomi_strong_repair_apply_summary.json",
            ]
        if stage_name == STAGE_YOMI_FINALIZED:
            return [
                batch_dir / "units.yomi.final.jsonl",
                batch_dir / "yomi_finalize_summary.json",
            ]
        return []

    def _existing_stage_artifact_paths(self, *, batch_state: BatchState, stage_name: str) -> list[str]:
        return [
            str(path)
            for path in self._stage_artifact_paths(batch_state=batch_state, stage_name=stage_name)
            if path.exists()
        ]

    def _next_stage_name(self, current_stage: str) -> str | None:
        current_stage = normalize_stage_name(current_stage)
        try:
            index = STAGE_SEQUENCE.index(current_stage)
        except ValueError:
            return None
        next_index = index + 1
        if next_index >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[next_index]

    @staticmethod
    def _blocking_reason_for_stage(stage_name: str) -> str | None:
        return None

    def _allocate_next_batch_name(self, track_name: str) -> str:
        prefix = TRACKS[track_name]["batch_prefix"]
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_value = 0
        if self.units_root().exists():
            for path in self.units_root().iterdir():
                match = pattern.match(path.name)
                if match:
                    max_value = max(max_value, int(match.group(1)))
        return f"{prefix}{max_value + 1:04d}"

    def _infer_latest_batch_name_for_track(self, track_name: str) -> str | None:
        prefix = TRACKS[track_name]["batch_prefix"]
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        candidates: list[tuple[int, str]] = []
        if self.units_root().exists():
            for path in self.units_root().iterdir():
                match = pattern.match(path.name)
                if match:
                    candidates.append((int(match.group(1)), path.name))
        if not candidates:
            return None
        candidates.sort()
        return candidates[-1][1]

    def _infer_batch_state(self, batch_name: str) -> BatchState:
        manifest_path = self.manifest_path(batch_name)
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest found for batch {batch_name}")
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        track_name = str(
            manifest.get("track_name")
            or (DEV_TRACK if batch_name.startswith("dev_batch_") else WORKING_TRACK)
        )
        current_stage = self._infer_stage_from_artifacts(batch_name)
        blocking_reason = self._blocking_reason_for_stage(current_stage)
        artifacts = {
            "units_jsonl": str(self.batch_dir(batch_name) / "units.jsonl"),
            "manifest": str(manifest_path),
        }
        if current_stage in {
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts["units_yomi_jsonl"] = str(
                self.batch_dir(batch_name) / "units.yomi.aligned_hybrid.jsonl"
            )
        if current_stage in {
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts["units_yomi_auto_accept_jsonl"] = str(
                self.batch_dir(batch_name) / "units.yomi.auto_accept.jsonl"
            )
            artifacts["yomi_auto_accept_summary_json"] = str(
                self.batch_dir(batch_name) / "yomi_auto_accept_summary.json"
            )
        if current_stage in {STAGE_YOMI_READING_QUEUED, STAGE_YOMI_READING_LLM_COMPLETED}:
            artifacts["units_yomi_safety_pre_llm_jsonl"] = str(
                self.batch_dir(batch_name) / "units.yomi.safety_pre_llm.jsonl"
            )
            artifacts["yomi_safety_pre_llm_summary_json"] = str(
                self.batch_dir(batch_name) / "yomi_safety_pre_llm_summary.json"
            )
            artifacts["yomi_reading_input_jsonl"] = str(
                self.batch_dir(batch_name) / "yomi_reading_input.jsonl"
            )
            artifacts["yomi_reading_queue_summary_json"] = str(
                self.batch_dir(batch_name) / "yomi_reading_queue_summary.json"
            )
        if current_stage == STAGE_YOMI_READING_LLM_COMPLETED:
            artifacts["yomi_reading_results_jsonl"] = str(
                self.batch_dir(batch_name) / "yomi_reading_results.jsonl"
            )
            artifacts["yomi_reading_usage_summary_json"] = str(
                self.batch_dir(batch_name) / "yomi_reading_usage_summary.json"
            )
            for attempt in (2, 3):
                prefix = f"yomi_reading_retry{attempt}"
                artifacts[f"{prefix}_input_jsonl"] = str(
                    self.batch_dir(batch_name) / f"{prefix}_input.jsonl"
                )
                artifacts[f"{prefix}_queue_summary_json"] = str(
                    self.batch_dir(batch_name) / f"{prefix}_queue_summary.json"
                )
                artifacts[f"{prefix}_results_jsonl"] = str(
                    self.batch_dir(batch_name) / f"{prefix}_results.jsonl"
                )
                artifacts[f"{prefix}_usage_summary_json"] = str(
                    self.batch_dir(batch_name) / f"{prefix}_usage_summary.json"
                )
            artifacts["units_yomi_llm_readings_jsonl"] = str(
                self.batch_dir(batch_name) / "units.yomi.llm_readings.jsonl"
            )
            artifacts["yomi_reading_apply_summary_json"] = str(
                self.batch_dir(batch_name) / "yomi_reading_apply_summary.json"
            )
        if current_stage == STAGE_FINAL_REVIEW_PREPARED:
            artifacts["final_review_pack_json"] = str(
                self.batch_dir(batch_name) / "final_review_pack.json"
            )
            artifacts["final_review_pack_summary_json"] = str(
                self.batch_dir(batch_name) / "final_review_pack_summary.json"
            )
            artifacts["review_pack_json"] = str(
                self.root
                / "data"
                / "review_packs"
                / "yomi_final"
                / f"yomi_final_{batch_name}_v1.json"
            )
            artifacts["review_publish_required"] = "true"
        raw_yomi_policy = manifest.get("yomi_policy")
        state = BatchState(
            batch_name=batch_name,
            track_name=track_name,
            batch_kind=str(manifest.get("batch_kind") or TRACKS[track_name]["batch_kind"]),
            pipeline_profile=str(manifest.get("pipeline_profile") or TRACKS[track_name]["pipeline_profile"]),
            dataset_name=str(manifest["dataset_name"]),
            dataset_config_path=str(manifest.get("dataset_config_path", "config/datasets/ja_cc_level2.toml")),
            dataset_source_path=str(manifest["dataset_source_path"]),
            target_documents=int(manifest["target_documents"]),
            docs_written=int(manifest["docs_written"]),
            units_written=int(manifest["units_written"]),
            current_stage=current_stage,
            yomi_policy=normalize_yomi_policy(
                raw_yomi_policy,
                track_name=track_name,
            ),
            llm_policy=normalize_llm_policy(
                manifest.get("llm_policy"),
                track_name=track_name,
                legacy_yomi_policy=raw_yomi_policy,
            ),
            llm_execution_policy=normalize_llm_execution_policy(
                manifest.get("llm_execution_policy"),
                track_name=track_name,
            ),
            decoder_model_dir=manifest.get("decoder_model_dir"),
            blocking_reason=blocking_reason,
            skipped_review_gates=[],
            artifacts=artifacts,
            updated_at=now_iso(),
        )
        self.save_batch_state(state)
        return state

    def _infer_stage_from_artifacts(self, batch_name: str) -> str:
        batch_dir = self.batch_dir(batch_name)
        if (batch_dir / "final_review_pack.json").exists():
            return STAGE_FINAL_REVIEW_PREPARED
        if (batch_dir / "units.yomi.llm_readings.jsonl").exists():
            return STAGE_YOMI_READING_LLM_COMPLETED
        if (batch_dir / "yomi_reading_input.jsonl").exists():
            return STAGE_YOMI_READING_QUEUED
        if (batch_dir / "units.yomi.auto_accept.jsonl").exists():
            return STAGE_YOMI_AUTO_ACCEPTED
        if (batch_dir / "units.yomi.aligned_hybrid.jsonl").exists():
            return STAGE_YOMI_GENERATED
        return STAGE_PREPARED

    def _load_dataset_config(self, path_str: str) -> dict[str, object]:
        path = self.root / path_str
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
        source_path = Path(str(payload["source_path"]))
        if not source_path.is_absolute():
            source_path = (self.root / source_path).resolve()
        return {
            "name": str(payload["name"]),
            "source_path": source_path,
        }

    def _select_source_documents(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        excluded_doc_ids: set[str] | None,
        assignments: list[dict[str, int]],
    ) -> list[dict[str, object]]:
        excluded = excluded_doc_ids or set()
        payloads = self._load_source_payloads(
            source_path=source_path,
            source_line_nos=[int(row["source_line_no"]) for row in assignments],
        )
        documents: list[dict[str, object]] = []
        for assignment in assignments:
            source_line_no = int(assignment["source_line_no"])
            doc_id = f"{dataset_name}:{source_line_no:010d}"
            if doc_id in excluded:
                raise ValueError(
                    f"Processing order selected already assigned document {doc_id}."
                )
            payload = payloads[source_line_no]
            text = str(payload["text"])
            documents.append(
                {
                    "doc_id": doc_id,
                    "track_doc_seq": int(assignment["processing_slot"]),
                    "dataset_name": dataset_name,
                    "dataset_source_path": str(source_path),
                    "source_line_no": source_line_no,
                    "source_file": str(payload.get("source_file", "")),
                    "text_preview": text[:120],
                }
            )
        return documents

    def _extract_batch_documents(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        batch_name: str,
        track_name: str,
        assignments: list[dict[str, int]],
    ) -> tuple[int, int, int | None, int | None]:
        output_dir = self.batch_dir(batch_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        units_path = output_dir / "units.jsonl"

        units_written = 0
        docs_written = 0
        source_start_line_no: int | None = None
        source_end_line_no: int | None = None
        payloads = self._load_source_payloads(
            source_path=source_path,
            source_line_nos=[int(row["source_line_no"]) for row in assignments],
        )
        selected = [
            (
                int(row["processing_slot"]),
                int(row["source_line_no"]),
                payloads[int(row["source_line_no"])],
            )
            for row in assignments
        ]

        with units_path.open("w", encoding="utf-8") as out:
            for track_doc_seq, source_line_no, payload in selected:
                text = str(payload["text"])
                docs_written += 1
                if source_start_line_no is None:
                    source_start_line_no = source_line_no
                else:
                    source_start_line_no = min(source_start_line_no, source_line_no)
                source_end_line_no = max(source_end_line_no or source_line_no, source_line_no)
                doc_id = f"{dataset_name}:{source_line_no:010d}"
                track_doc_seq = self._assign_track_doc_seq(
                    track_name=track_name,
                    doc_id=doc_id,
                    dataset_name=dataset_name,
                    source_path=Path(source_path),
                    source_line_no=source_line_no,
                    batch_name=batch_name,
                    track_doc_seq=track_doc_seq,
                )
                source_file = str(payload.get("source_file", ""))
                spans = split_text_into_units(text)
                for unit_seq, span in enumerate(spans, start=1):
                    units_written += 1
                    unit = UnitRecord(
                        doc_id=doc_id,
                        unit_id=f"{doc_id}:u{unit_seq:04d}",
                        unit_seq=unit_seq,
                        track_doc_seq=track_doc_seq,
                        char_start=span.start,
                        char_end=span.end,
                        text=span.text,
                        source_file=source_file,
                        source_line_no=source_line_no,
                        analysis=empty_analysis(),
                    )
                    out.write(json.dumps(unit.to_dict(), ensure_ascii=False) + "\n")
        return docs_written, units_written, source_start_line_no, source_end_line_no

    @staticmethod
    def _load_source_payloads(
        *,
        source_path: Path,
        source_line_nos: list[int],
    ) -> dict[int, dict[str, object]]:
        wanted = set(source_line_nos)
        payloads: dict[int, dict[str, object]] = {}
        if not wanted:
            return payloads
        last_wanted = max(wanted)
        with gzip.open(source_path, "rt", encoding="utf-8") as handle:
            for source_line_no, line in enumerate(handle, start=1):
                if source_line_no not in wanted:
                    if source_line_no > last_wanted:
                        break
                    continue
                payload = json.loads(line)
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(
                        f"Processing order selected blank source document {source_line_no}."
                    )
                payloads[source_line_no] = payload
                if len(payloads) == len(wanted):
                    break
        missing = sorted(wanted - payloads.keys())
        if missing:
            raise EOFError(f"Source documents not found for lines: {missing}")
        return payloads

    def _assign_track_doc_seq(
        self,
        *,
        track_name: str,
        doc_id: str,
        dataset_name: str,
        source_path: Path,
        source_line_no: int,
        batch_name: str,
        track_doc_seq: int | None = None,
    ) -> int:
        ledger = self._load_document_ledger(track_name)
        by_doc = {
            str(row.get("doc_id") or ""): row
            for row in ledger.get("documents", [])
            if isinstance(row, dict) and str(row.get("doc_id") or "")
        }
        existing = by_doc.get(doc_id)
        if existing is not None:
            existing_seq = int(existing.get("track_doc_seq") or 0)
            if existing_seq > 0:
                if track_doc_seq is not None and existing_seq != track_doc_seq:
                    raise ValueError(
                        f"Document {doc_id} is already assigned to slot {existing_seq}, "
                        f"not {track_doc_seq}."
                    )
                return existing_seq

        assigned_seq = track_doc_seq or self._next_track_doc_seq(ledger)
        for row in ledger.get("documents", []):
            if int(row.get("track_doc_seq") or 0) == assigned_seq:
                raise ValueError(
                    f"Processing slot {assigned_seq} is already assigned to {row.get('doc_id')}."
                )
        row = {
            "doc_id": doc_id,
            "track_doc_seq": assigned_seq,
            "dataset_name": dataset_name,
            "dataset_source_path": str(source_path),
            "source_line_no": source_line_no,
            "first_batch_name": batch_name,
            "created_at": now_iso(),
        }
        ledger.setdefault("documents", []).append(row)
        ledger["updated_at"] = now_iso()
        self._write_document_ledger(track_name, ledger)
        return assigned_seq

    def _load_document_ledger(self, track_name: str) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        path = self.document_ledger_path(normalized)
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload.get("documents"), list):
                return payload
        created = now_iso()
        return {
            "schema_version": 1,
            "track_name": normalized,
            "created_at": created,
            "updated_at": created,
            "documents": [],
        }

    def _write_document_ledger(self, track_name: str, ledger: dict[str, object]) -> None:
        normalized = normalize_track_name(track_name)
        ledger["track_name"] = normalized
        ledger["schema_version"] = 1
        ledger.setdefault("created_at", now_iso())
        ledger.setdefault("documents", [])
        self.ensure_dirs()
        self.document_ledger_path(normalized).write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _next_track_doc_seq(ledger: dict[str, object]) -> int:
        max_seq = 0
        for row in ledger.get("documents", []):
            if not isinstance(row, dict):
                continue
            try:
                max_seq = max(max_seq, int(row.get("track_doc_seq") or 0))
            except (TypeError, ValueError):
                continue
        return max_seq + 1

    def _generate_mechanical_yomi(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        input_path = batch_dir / "units.jsonl"
        summary = export_named_variant(
            variant_name="aligned_hybrid",
            batch_dir=batch_dir,
            config_path="config/yomi/default.toml",
            formats=["jsonl"],
            show_progress=True,
            input_jsonl=input_path,
            decoder_model_dir=batch_state.decoder_model_dir,
        )
        artifacts = {
            "units_yomi_jsonl": str(batch_dir / "units.yomi.aligned_hybrid.jsonl"),
            "yomi_variant": str(summary["variant_name"]),
            "yomi_input_jsonl": str(input_path),
        }
        if batch_state.decoder_model_dir:
            artifacts["decoder_model_dir"] = batch_state.decoder_model_dir
        return {"artifacts": artifacts}

    def _auto_accept_mechanical_yomi(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        input_path = batch_dir / "units.yomi.aligned_hybrid.jsonl"
        output_path = batch_dir / "units.yomi.auto_accept.jsonl"
        summary_path = batch_dir / "yomi_auto_accept_summary.json"
        auto_accept_profile = batch_state.yomi_policy["auto_accept_profile"]
        summary = apply_yomi_auto_acceptance_file(
            input_jsonl=input_path,
            output_jsonl=output_path,
            summary_json=summary_path,
            auto_accept_profile=auto_accept_profile,
            decoder_model_dir=batch_state.decoder_model_dir,
        )
        return {
            "artifacts": {
                "units_yomi_auto_accept_jsonl": str(output_path),
                "yomi_auto_accept_summary_json": str(summary_path),
                "yomi_auto_accept_rule": summary.rule,
                "yomi_auto_accept_profile": summary.auto_accept_profile,
                "yomi_auto_accept_accepted": str(summary.accepted),
                "yomi_auto_accept_rejected": str(summary.rejected),
                "yomi_auto_accept_stable_two_kanji_enabled": str(
                    summary.stable_two_kanji_enabled
                ).lower(),
                "yomi_auto_accept_stable_surface_lexicon": str(
                    getattr(summary, "stable_surface_lexicon_artifact", "") or ""
                ),
            }
        }

    def _queue_yomi_llm_reading(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        input_path = batch_dir / "units.yomi.auto_accept.jsonl"
        safety_path = batch_dir / "units.yomi.safety_pre_llm.jsonl"
        safety_summary_path = batch_dir / "yomi_safety_pre_llm_summary.json"
        output_path = batch_dir / "yomi_reading_input.jsonl"
        summary_path = batch_dir / "yomi_reading_queue_summary.json"
        safety_summary = apply_yomi_safety_pre_llm_file(
            input_jsonl=input_path,
            output_jsonl=safety_path,
            summary_json=safety_summary_path,
            decoder_model_dir=batch_state.decoder_model_dir,
        )
        summary = build_yomi_llm_reading_queue_file(
            input_jsonl=safety_path,
            output_jsonl=output_path,
            summary_json=summary_path,
            skip_stable_two_kanji=False,
        )
        return {
            "artifacts": {
                "units_yomi_safety_pre_llm_jsonl": str(safety_path),
                "yomi_safety_pre_llm_summary_json": str(safety_summary_path),
                "yomi_safety_pre_llm_targets": str(safety_summary.target_count),
                "yomi_safety_pre_llm_safe": str(safety_summary.safe_targets),
                "yomi_safety_pre_llm_unresolved": str(safety_summary.unresolved_targets),
                "yomi_safety_pre_llm_stable_surface_safe": str(
                    getattr(safety_summary, "stable_surface_lexicon_safe", 0)
                ),
                "yomi_safety_pre_llm_unit_auto_accept": str(
                    getattr(safety_summary, "unit_auto_accept_safe", 0)
                ),
                "yomi_safety_pre_llm_corpus_frequency_stats": str(
                    getattr(safety_summary, "corpus_frequency_stats_artifact", "") or ""
                ),
                "yomi_safety_pre_llm_stable_surface_lexicon": str(
                    getattr(safety_summary, "stable_surface_lexicon_artifact", "") or ""
                ),
                "yomi_reading_input_jsonl": str(output_path),
                "yomi_reading_queue_summary_json": str(summary_path),
                "yomi_reading_task_config": "config/llm/yomi_reading.toml",
                "yomi_reading_queued": str(summary.queued_items),
                "yomi_reading_skipped": str(summary.skipped_items),
                "yomi_reading_stable_two_kanji_skipped": str(summary.stable_two_kanji_skipped),
                "yomi_reading_safety_skipped": str(summary.safety_skipped),
            }
        }

    def _run_yomi_llm_reading(
        self,
        batch_name: str,
        *,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        task_config_path = "config/llm/yomi_reading.toml"
        llm_profile = batch_state.llm_policy[LLM_TASK_YOMI_READING]
        execution_mode = (
            llm_execution_mode_override
            or batch_state.llm_execution_policy[LLM_TASK_YOMI_READING]
        )
        base_task_config = load_llm_task_config(task_config_path)
        task_config = apply_llm_profile(base_task_config, llm_profile)
        input_path = batch_dir / "yomi_reading_input.jsonl"
        results_path = batch_dir / "yomi_reading_results.jsonl"
        usage_summary_path = batch_dir / "yomi_reading_usage_summary.json"
        output_path = batch_dir / "units.yomi.llm_readings.jsonl"
        apply_summary_path = batch_dir / "yomi_reading_apply_summary.json"
        job_dir = self.root / "data" / "llm" / "jobs" / f"{batch_name}_yomi_reading"

        queued_count = count_nonempty_lines(input_path)
        job_summary = None
        if queued_count:
            job_summary = run_llm_task(
                task_config_path,
                str(input_path),
                str(results_path),
                execution_mode=execution_mode,
                task_config_override=task_config,
                job_dir=str(job_dir),
                show_progress=True,
            )
            if job_summary.status != "completed":
                return {
                    "stage_complete": False,
                    "blocking_reason": self._llm_incomplete_blocking_reason(
                        execution_mode=execution_mode,
                        job_summary=job_summary,
                    ),
                    "artifacts": self._llm_running_artifacts(
                        prefix="yomi_reading",
                        task_config_path=task_config_path,
                        task_config=task_config,
                        llm_profile=llm_profile,
                        execution_mode=execution_mode,
                        job_dir=job_dir,
                        job_summary=job_summary,
                        queued_count=queued_count,
                    ),
                }
        else:
            results_path.parent.mkdir(parents=True, exist_ok=True)
            results_path.write_text("", encoding="utf-8")

        usage_summary = summarize_results_jsonl(
            str(results_path),
            model=task_config.model,
            processing_tier="standard",
            pricing_config_path=str(DEFAULT_PRICING_CONFIG_PATH),
        )
        usage_summary_path.write_text(
            json.dumps(usage_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        retry_artifacts: dict[str, str] = {}
        retry_results_paths: list[Path] = []
        previous_results_path = results_path
        max_attempts = 3
        for attempt in range(2, max_attempts + 1):
            retry_prefix = f"yomi_reading_retry{attempt}"
            retry_input_path = batch_dir / f"{retry_prefix}_input.jsonl"
            retry_queue_summary_path = batch_dir / f"{retry_prefix}_queue_summary.json"
            retry_results_path = batch_dir / f"{retry_prefix}_results.jsonl"
            retry_usage_summary_path = batch_dir / f"{retry_prefix}_usage_summary.json"
            retry_job_dir = self.root / "data" / "llm" / "jobs" / f"{batch_name}_{retry_prefix}"
            retry_queue_summary = build_yomi_llm_reading_retry_queue_file(
                queue_jsonl=input_path,
                results_jsonl=previous_results_path,
                output_jsonl=retry_input_path,
                summary_json=retry_queue_summary_path,
                attempt=attempt,
            )
            retry_queued_count = retry_queue_summary.retry_items
            retry_job_summary = None
            if retry_queued_count:
                retry_job_summary = run_llm_task(
                    task_config_path,
                    str(retry_input_path),
                    str(retry_results_path),
                    execution_mode=execution_mode,
                    task_config_override=task_config,
                    job_dir=str(retry_job_dir),
                    show_progress=True,
                )
                if retry_job_summary.status != "completed":
                    return {
                        "stage_complete": False,
                        "blocking_reason": self._llm_incomplete_blocking_reason(
                            execution_mode=execution_mode,
                            job_summary=retry_job_summary,
                            label=f"retry attempt {attempt}",
                        ),
                        "artifacts": {
                            **self._llm_completed_artifacts(
                                prefix="yomi_reading",
                                results_path=results_path,
                                usage_summary_path=usage_summary_path,
                                apply_summary_path=retry_queue_summary_path,
                                task_config_path=task_config_path,
                                task_config=task_config,
                                llm_profile=llm_profile,
                                execution_mode=execution_mode,
                                job_dir=job_dir,
                                job_summary=job_summary,
                                queued_count=queued_count,
                            ),
                            **retry_artifacts,
                            **self._llm_running_artifacts(
                                prefix=retry_prefix,
                                task_config_path=task_config_path,
                                task_config=task_config,
                                llm_profile=llm_profile,
                                execution_mode=execution_mode,
                                job_dir=retry_job_dir,
                                job_summary=retry_job_summary,
                                queued_count=retry_queued_count,
                            ),
                            f"{retry_prefix}_input_jsonl": str(retry_input_path),
                            f"{retry_prefix}_queue_summary_json": str(retry_queue_summary_path),
                        },
                    }
            else:
                retry_results_path.parent.mkdir(parents=True, exist_ok=True)
                retry_results_path.write_text("", encoding="utf-8")

            retry_usage_summary = summarize_results_jsonl(
                str(retry_results_path),
                model=task_config.model,
                processing_tier="standard",
                pricing_config_path=str(DEFAULT_PRICING_CONFIG_PATH),
            )
            retry_usage_summary_path.write_text(
                json.dumps(retry_usage_summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            retry_results_paths.append(retry_results_path)
            retry_artifacts.update(
                {
                    **self._llm_completed_artifacts(
                        prefix=retry_prefix,
                        results_path=retry_results_path,
                        usage_summary_path=retry_usage_summary_path,
                        apply_summary_path=retry_queue_summary_path,
                        task_config_path=task_config_path,
                        task_config=task_config,
                        llm_profile=llm_profile,
                        execution_mode=execution_mode,
                        job_dir=retry_job_dir,
                        job_summary=retry_job_summary,
                        queued_count=retry_queued_count,
                    ),
                    f"{retry_prefix}_input_jsonl": str(retry_input_path),
                    f"{retry_prefix}_queue_summary_json": str(retry_queue_summary_path),
                    f"{retry_prefix}_queued": str(retry_queued_count),
                }
            )
            previous_results_path = retry_results_path
            if retry_queued_count == 0:
                break

        retry_results_path = retry_results_paths[-1] if retry_results_paths else batch_dir / "yomi_reading_retry2_results.jsonl"
        if not retry_results_path.exists():
            retry_results_path.parent.mkdir(parents=True, exist_ok=True)
            retry_results_path.write_text("", encoding="utf-8")
        apply_summary = apply_yomi_llm_reading_results_file(
            units_jsonl=batch_dir / "units.yomi.safety_pre_llm.jsonl",
            queue_jsonl=input_path,
            results_jsonl=results_path,
            retry_results_jsonls=retry_results_paths,
            output_jsonl=output_path,
            summary_json=apply_summary_path,
        )
        return {
            "artifacts": {
                **self._llm_completed_artifacts(
                    prefix="yomi_reading",
                    results_path=results_path,
                    usage_summary_path=usage_summary_path,
                    apply_summary_path=apply_summary_path,
                    task_config_path=task_config_path,
                    task_config=task_config,
                    llm_profile=llm_profile,
                    execution_mode=execution_mode,
                    job_dir=job_dir,
                    job_summary=job_summary,
                    queued_count=queued_count,
                ),
                **retry_artifacts,
                "units_yomi_llm_readings_jsonl": str(output_path),
                "yomi_reading_checked": str(apply_summary.checked_items),
                "yomi_reading_matched": str(apply_summary.matched_items),
                "yomi_reading_mismatched": str(apply_summary.mismatched_items),
                "yomi_reading_parse_error": str(apply_summary.parse_error_items),
                "yomi_reading_missing_result": str(apply_summary.missing_result_items),
            }
        }

    def _prepare_final_review(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        source_path, _ = self._materialize_yomi_review_units(batch_name)
        pack_id = f"yomi_final_{batch_name}_v1"
        document_state_path = self.document_review_state_path(batch_name)
        document_state = build_initial_document_review_state(
            units_jsonl=source_path,
            batch_name=batch_name,
            track_name=batch_state.track_name,
        )
        write_document_review_state(document_state_path, document_state)
        summary, pack_artifacts = self._refresh_final_review_pack(
            batch_name=batch_name,
            pack_id=pack_id,
        )
        return {
            "artifacts": {
                "document_review_state_json": str(document_state_path),
                "document_review_state_documents": str(
                    document_state["summary"]["document_count"]
                ),
                "document_review_state_final_pending": str(
                    document_state["summary"]["state_counts"].get("final_pending", 0)
                ),
                "units_yomi_review_input_jsonl": str(source_path),
                **pack_artifacts,
                "review_publish_required": "true",
                "review_site_url": "https://hiroshi-manabe.github.io/yomi-corpus/",
                "final_review_stage": summary.review_stage,
                "final_review_pack_id": summary.pack_id,
                "final_review_items": str(summary.item_count),
                "final_review_unresolved_items": str(summary.unresolved_item_count),
                "final_review_unresolved_targets": str(summary.unresolved_target_count),
            }
        }

    def _refresh_final_review_pack(
        self,
        *,
        batch_name: str,
        pack_id: str,
    ) -> tuple[object, dict[str, str]]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        source_path, _ = self._materialize_yomi_review_units(batch_name)
        batch_pack_path = batch_dir / "final_review_pack.json"
        summary_path = batch_dir / "final_review_pack_summary.json"
        review_pack_path = (
            self.root / "data" / "review_packs" / "yomi_final" / f"{pack_id}.json"
        )
        document_state_path = self.document_review_state_path(batch_name)
        summary = build_yomi_final_review_pack_file(
            units_jsonl=source_path,
            output_json=batch_pack_path,
            pack_id=pack_id,
            track_name=batch_state.track_name,
            batch_name=batch_name,
            document_state_json=document_state_path if document_state_path.exists() else None,
            latest_json=review_pack_path,
            created_at_epoch=existing_pack_created_at_epoch(review_pack_path),
        )
        write_yomi_final_review_summary(summary, summary_path)
        return summary, {
            "units_yomi_review_input_jsonl": str(source_path),
            "final_review_pack_json": str(batch_pack_path),
            "final_review_pack_summary_json": str(summary_path),
            "review_pack_json": str(review_pack_path),
        }

    def _materialize_yomi_review_units(
        self,
        batch_name: str,
    ) -> tuple[Path, dict[str, object]]:
        batch_dir = self.batch_dir(batch_name)
        output_path = batch_dir / "units.yomi.review_input.jsonl"
        summary = materialize_yomi_review_units_file(
            processed_units_jsonl=batch_dir / "units.yomi.llm_readings.jsonl",
            output_jsonl=output_path,
        )
        return output_path, summary

    def _apply_final_review(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        pack_id = str(
            batch_state.artifacts.get("final_review_pack_id") or f"yomi_final_{batch_name}_v1"
        )
        pack_path = batch_dir / "final_review_pack.json"
        output_path = batch_dir / "units.yomi.reviewed.jsonl"
        summary_path = batch_dir / "final_review_apply_summary.json"
        submission_store_dir = self.root / "data" / "review_submissions" / "yomi_final"
        import_summary = self._import_final_review_submissions(submission_store_dir)
        review_units_path, _ = self._materialize_yomi_review_units(batch_name)
        summary = apply_final_review_file(
            units_jsonl=review_units_path,
            pack_json=pack_path,
            submission_store_dir=submission_store_dir,
            output_jsonl=output_path,
            summary_json=summary_path,
        )
        document_state_path = self.document_review_state_path(batch_name)
        document_state_artifacts: dict[str, str] = {
            "document_review_state_json": str(document_state_path),
        }
        if output_path.exists():
            if document_state_path.exists():
                document_state = load_document_review_state(document_state_path)
            else:
                document_state = build_initial_document_review_state(
                    units_jsonl=review_units_path,
                    batch_name=batch_name,
                    track_name=batch_state.track_name,
                )
            document_state = update_document_review_state_after_final_review(
                state=document_state,
                reviewed_units_jsonl=output_path,
            )
            write_document_review_state(document_state_path, document_state)
            state_counts = document_state["summary"]["state_counts"]
            document_state_artifacts.update(
                {
                    "document_review_state_documents": str(
                        document_state["summary"]["document_count"]
                    ),
                    "document_review_state_final_pending": str(
                        state_counts.get("final_pending", 0)
                    ),
                    "document_review_state_final_in_review": str(
                        state_counts.get("final_in_review", 0)
                    ),
                    "document_review_state_final_reviewed": str(
                        state_counts.get("final_reviewed", 0)
                    ),
                    "document_review_state_skipped": str(state_counts.get("skipped", 0)),
                }
            )
            _, pack_artifacts = self._refresh_final_review_pack(
                batch_name=batch_name,
                pack_id=pack_id,
            )
            document_state_artifacts.update(pack_artifacts)
        artifacts = {
            "final_review_pack_id": pack_id,
            "final_review_submission_store": str(submission_store_dir),
            "final_review_issue_import_summary_json": str(
                self.root / "data" / "state" / "yomi_final" / "last_review_inbox_import_summary.json"
            ),
            "final_review_issue_import_status": str(import_summary.get("status", "")),
            "final_review_issue_imported_submissions": str(
                import_summary.get("imported_submission_count", "")
            ),
            "final_review_apply_summary_json": str(summary_path),
            **document_state_artifacts,
        }
        if not summary.get("stage_complete", True):
            return {
                "stage_complete": False,
                "blocking_reason": str(summary["blocking_reason"]),
                "artifacts": {
                    **artifacts,
                    "human_review_required": "true",
                    "human_review_gate": "yomi_final_review",
                    "human_review_item_count": str(batch_state.artifacts.get("final_review_items", "")),
                    "final_review_submissions": "0",
                },
            }
        return {
            "artifacts": {
                **artifacts,
                "units_yomi_reviewed_jsonl": str(output_path),
                "final_review_submissions": str(summary["submission_count"]),
                "final_review_reviewed_units": str(summary["reviewed_units"]),
                "final_review_unreviewed_units": str(summary["unreviewed_units"]),
                "final_review_skipped_units": str(summary["skipped_units"]),
                "final_review_target_overrides": str(summary["target_override_count"]),
                "final_review_no_ruby_targets": str(summary["no_ruby_target_count"]),
                "final_review_exact_rendered_updates": str(summary["exact_rendered_updates"]),
                "human_review_required": "false",
                "human_review_gate": "",
                "human_review_item_count": "",
            }
        }

    def _import_final_review_submissions(self, submission_store_dir: Path) -> dict[str, object]:
        return self._import_review_submissions(
            submission_store_dir,
            review_stage="yomi_final_review",
            summary_path=self.root
            / "data"
            / "state"
            / "yomi_final"
            / "last_review_inbox_import_summary.json",
        )

    def _import_strong_repair_review_submissions(self, submission_store_dir: Path) -> dict[str, object]:
        return self._import_review_submissions(
            submission_store_dir,
            review_stage=STRONG_REPAIR_REVIEW_STAGE,
            summary_path=self.root
            / "data"
            / "state"
            / "yomi_strong_repair"
            / "last_review_inbox_import_summary.json",
        )

    def _import_review_submissions(
        self,
        submission_store_dir: Path,
        *,
        review_stage: str,
        summary_path: Path,
    ) -> dict[str, object]:
        try:
            summary = import_open_issue_inbox(
                repo="hiroshi-manabe/yomi-corpus",
                review_pack_root=self.root / "data" / "review_packs",
                submission_store_dir=submission_store_dir,
                review_stage=review_stage,
            )
            summary = {"status": "ok", **summary}
        except SystemExit as exc:
            summary = {
                "status": "failed",
                "error": str(exc),
                "imported_submission_count": 0,
            }
        except Exception as exc:
            summary = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "imported_submission_count": 0,
            }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary

    def _queue_yomi_strong_repair(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        output_path = batch_dir / "yomi_strong_repair_queue.jsonl"
        summary_path = batch_dir / "yomi_strong_repair_queue_summary.json"
        summary = build_strong_repair_queue_file(
            units_jsonl=batch_dir / "units.yomi.reviewed.jsonl",
            output_jsonl=output_path,
            summary_json=summary_path,
        )
        document_state_path = self.document_review_state_path(batch_name)
        document_state_artifacts: dict[str, str] = {
            "document_review_state_json": str(document_state_path),
        }
        if document_state_path.exists():
            document_state = load_document_review_state(document_state_path)
            document_state = update_document_review_state_after_strong_queue(
                state=document_state,
                queue_jsonl=output_path,
            )
            write_document_review_state(document_state_path, document_state)
            state_counts = document_state["summary"]["state_counts"]
            document_state_artifacts.update(
                {
                    "document_review_state_strong_pending": str(
                        state_counts.get("strong_pending", 0)
                    ),
                    "document_review_state_complete": str(state_counts.get("complete", 0)),
                    "document_review_state_skipped": str(state_counts.get("skipped", 0)),
                }
            )
        return {
            "artifacts": {
                "yomi_strong_repair_queue_jsonl": str(output_path),
                "yomi_strong_repair_queue_summary_json": str(summary_path),
                **document_state_artifacts,
                "yomi_strong_repair_queued": str(summary["queued_items"]),
                "yomi_strong_repair_target_escalations": str(summary["target_escalations"]),
                "yomi_strong_repair_mock_only": "false",
                "human_review_required": "false",
                "human_review_gate": "",
                "human_review_item_count": "",
            }
        }

    def _run_yomi_strong_repair(
        self,
        batch_name: str,
        *,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        task_config_path = "config/llm/yomi_repair.toml"
        llm_profile = batch_state.llm_policy[LLM_TASK_YOMI_REPAIR]
        execution_mode = (
            llm_execution_mode_override
            or batch_state.llm_execution_policy[LLM_TASK_YOMI_REPAIR]
        )
        base_task_config = load_llm_task_config(task_config_path)
        task_config = apply_llm_profile(base_task_config, llm_profile)
        input_path = batch_dir / "yomi_strong_repair_queue.jsonl"
        results_path = batch_dir / "yomi_strong_repair_results.jsonl"
        usage_summary_path = batch_dir / "yomi_strong_repair_usage_summary.json"
        output_path = batch_dir / "units.yomi.strong_repaired.jsonl"
        apply_summary_path = batch_dir / "yomi_strong_repair_apply_summary.json"
        job_dir = self.root / "data" / "llm" / "jobs" / f"{batch_name}_yomi_strong_repair"

        queued_count = count_nonempty_lines(input_path)
        if apply_summary_path.exists():
            existing_apply_summary = json.loads(apply_summary_path.read_text(encoding="utf-8"))
            existing_queued_count = int(existing_apply_summary.get("queued_items") or 0)
            if existing_apply_summary.get("confirmed") and existing_queued_count == queued_count:
                return {
                    "artifacts": {
                        "units_yomi_strong_repaired_jsonl": str(output_path),
                        "yomi_strong_repair_apply_summary_json": str(apply_summary_path),
                        "yomi_strong_repair_queued": str(queued_count),
                        "yomi_strong_repair_applied": str(
                            existing_apply_summary.get("applied_items", "")
                        ),
                        "yomi_strong_repair_unapplied": str(
                            existing_apply_summary.get("unapplied_items", "")
                        ),
                        "yomi_strong_repair_noop": str(
                            existing_apply_summary.get("noop_items", "")
                        ),
                        "yomi_strong_repair_unresolved": str(
                            existing_apply_summary.get("unresolved_items", "")
                        ),
                        "yomi_strong_repair_confirmed": "true",
                        "human_review_required": "false",
                        "human_review_gate": "",
                        "human_review_item_count": "",
                    }
                }
        job_summary = None
        if queued_count:
            job_summary = run_llm_task(
                task_config_path,
                str(input_path),
                str(results_path),
                execution_mode=execution_mode,
                task_config_override=task_config,
                job_dir=str(job_dir),
                show_progress=True,
            )
            if job_summary.status != "completed":
                return {
                    "stage_complete": False,
                    "blocking_reason": self._llm_incomplete_blocking_reason(
                        execution_mode=execution_mode,
                        job_summary=job_summary,
                    ),
                    "artifacts": self._llm_running_artifacts(
                        prefix="yomi_strong_repair",
                        task_config_path=task_config_path,
                        task_config=task_config,
                        llm_profile=llm_profile,
                        execution_mode=execution_mode,
                        job_dir=job_dir,
                        job_summary=job_summary,
                        queued_count=queued_count,
                    ),
                }
        else:
            results_path.parent.mkdir(parents=True, exist_ok=True)
            results_path.write_text("", encoding="utf-8")

        effective_results_path = batch_dir / "yomi_strong_repair_effective_results.jsonl"
        result_paths = [results_path]
        retry_artifacts: dict[str, str] = {}
        for attempt in range(1, YOMI_STRONG_REPAIR_RESPONSE_RETRIES + 1):
            retry_rows = write_effective_yomi_strong_repair_results(
                queue_jsonl=input_path,
                result_jsonls=result_paths,
                output_jsonl=effective_results_path,
            )
            if not retry_rows:
                break
            retry_prefix = f"yomi_strong_repair_retry{attempt}"
            retry_input_path = batch_dir / f"{retry_prefix}_input.jsonl"
            retry_results_path = batch_dir / f"{retry_prefix}_results.jsonl"
            retry_job_dir = self.root / "data" / "llm" / "jobs" / f"{batch_name}_{retry_prefix}"
            write_jsonl_rows(retry_input_path, retry_rows)
            retry_summary = run_llm_task(
                task_config_path,
                str(retry_input_path),
                str(retry_results_path),
                execution_mode=execution_mode,
                task_config_override=task_config,
                job_dir=str(retry_job_dir),
                show_progress=True,
            )
            retry_artifacts[f"{retry_prefix}_input_jsonl"] = str(retry_input_path)
            retry_artifacts[f"{retry_prefix}_results_jsonl"] = str(retry_results_path)
            if retry_summary.status != "completed":
                return {
                    "stage_complete": False,
                    "blocking_reason": self._llm_incomplete_blocking_reason(
                        execution_mode=execution_mode,
                        job_summary=retry_summary,
                    ),
                    "artifacts": {
                        **retry_artifacts,
                        **self._llm_running_artifacts(
                            prefix=retry_prefix,
                            task_config_path=task_config_path,
                            task_config=task_config,
                            llm_profile=llm_profile,
                            execution_mode=execution_mode,
                            job_dir=retry_job_dir,
                            job_summary=retry_summary,
                            queued_count=len(retry_rows),
                        ),
                    },
                }
            result_paths.append(retry_results_path)

        write_effective_yomi_strong_repair_results(
            queue_jsonl=input_path,
            result_jsonls=result_paths,
            output_jsonl=effective_results_path,
        )
        usage_summary = summarize_results_jsonl(
            str(effective_results_path),
            model=task_config.model,
            processing_tier="standard",
            pricing_config_path=str(DEFAULT_PRICING_CONFIG_PATH),
        )
        usage_summary_path.write_text(
            json.dumps(usage_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        apply_summary = apply_yomi_strong_repair_results_file(
            units_jsonl=batch_dir / "units.yomi.reviewed.jsonl",
            queue_jsonl=input_path,
            results_jsonl=effective_results_path,
            output_jsonl=output_path,
            summary_json=apply_summary_path,
        )
        review_pack_artifacts: dict[str, str] = {}
        if queued_count:
            review_pack_artifacts = self._prepare_strong_repair_review_pack(
                batch_name,
                queue_jsonl=input_path,
                results_jsonl=effective_results_path,
                units_jsonl=output_path,
            )
        completed_artifacts = self._llm_completed_artifacts(
            prefix="yomi_strong_repair",
            results_path=effective_results_path,
            usage_summary_path=usage_summary_path,
            apply_summary_path=apply_summary_path,
            task_config_path=task_config_path,
            task_config=task_config,
            llm_profile=llm_profile,
            execution_mode=execution_mode,
            job_dir=job_dir,
            job_summary=job_summary,
            queued_count=queued_count,
        )
        artifacts = {
            **completed_artifacts,
            **retry_artifacts,
            **review_pack_artifacts,
            "units_yomi_strong_repaired_jsonl": str(output_path),
            "yomi_strong_repair_applied": str(apply_summary["applied_items"]),
            "yomi_strong_repair_unapplied": str(apply_summary["unapplied_items"]),
            "yomi_strong_repair_noop": str(apply_summary["noop_items"]),
            "yomi_strong_repair_unresolved": str(apply_summary["unresolved_items"]),
            "yomi_strong_repair_parse_error": str(apply_summary["parse_error_items"]),
            "yomi_strong_repair_missing_result": str(apply_summary["missing_results"]),
            "human_review_required": "true" if queued_count else "false",
            "human_review_gate": "yomi_strong_repair_review" if queued_count else "",
            "human_review_item_count": str(queued_count) if queued_count else "",
        }
        if not apply_summary.get("stage_complete", True):
            return {
                "stage_complete": False,
                "blocking_reason": str(apply_summary["blocking_reason"]),
                "artifacts": artifacts,
            }
        return {"artifacts": artifacts}

    def _prepare_strong_repair_review_pack(
        self,
        batch_name: str,
        *,
        queue_jsonl: Path,
        results_jsonl: Path,
        units_jsonl: Path,
    ) -> dict[str, str]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        pack_id = f"yomi_strong_repair_{batch_name}_v1"
        batch_pack_path = batch_dir / "yomi_strong_repair_review_pack.json"
        summary_path = batch_dir / "yomi_strong_repair_review_pack_summary.json"
        review_pack_path = (
            self.root / "data" / "review_packs" / "yomi_strong_repair" / f"{pack_id}.json"
        )
        summary = build_yomi_strong_repair_review_pack_file(
            queue_jsonl=queue_jsonl,
            results_jsonl=results_jsonl,
            units_jsonl=units_jsonl,
            output_json=batch_pack_path,
            pack_id=pack_id,
            track_name=batch_state.track_name,
            batch_name=batch_name,
            document_state_json=self.document_review_state_path(batch_name),
            latest_json=review_pack_path,
            created_at_epoch=existing_pack_created_at_epoch(review_pack_path),
        )
        write_yomi_final_review_summary(summary, summary_path)
        return {
            "yomi_strong_repair_review_pack_json": str(batch_pack_path),
            "yomi_strong_repair_review_pack_summary_json": str(summary_path),
            "yomi_strong_repair_review_pack_id": summary.pack_id,
            "yomi_strong_repair_review_items": str(summary.item_count),
            "review_publish_required": "true",
            "yomi_strong_repair_review_site_url": "https://hiroshi-manabe.github.io/yomi-corpus/",
        }

    def _apply_strong_repair_review(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        strong_repaired_path = batch_dir / "units.yomi.strong_repaired.jsonl"
        strong_review_pack = batch_dir / "yomi_strong_repair_review_pack.json"
        strong_review_summary_path = batch_dir / "yomi_strong_repair_review_apply_summary.json"
        strong_submission_store_dir = self.root / "data" / "review_submissions" / "yomi_strong_repair"
        if not strong_review_pack.exists():
            return {
                "artifacts": {
                    "yomi_strong_repair_review_pack_json": str(strong_review_pack),
                    "yomi_strong_repair_review_pack_exists": "false",
                }
            }

        import_summary = self._import_strong_repair_review_submissions(
            strong_submission_store_dir
        )
        artifacts: dict[str, str] = {
            "yomi_strong_repair_review_issue_import_summary_json": str(
                self.root
                / "data"
                / "state"
                / "yomi_strong_repair"
                / "last_review_inbox_import_summary.json"
            ),
            "yomi_strong_repair_review_issue_import_status": str(
                import_summary.get("status", "")
            ),
            "yomi_strong_repair_review_imported_submissions": str(
                import_summary.get("imported_submission_count", "")
            ),
            "yomi_strong_repair_review_pack_json": str(strong_review_pack),
            "yomi_strong_repair_review_apply_summary_json": str(
                strong_review_summary_path
            ),
            "yomi_strong_repair_review_submission_store": str(
                strong_submission_store_dir
            ),
        }

        strong_review_summary = apply_strong_repair_review_file(
            pack_json=strong_review_pack,
            submission_store_dir=strong_submission_store_dir,
            strong_apply_summary_json=batch_dir / "yomi_strong_repair_apply_summary.json",
            output_summary_json=strong_review_summary_path,
            units_jsonl=strong_repaired_path if strong_repaired_path.exists() else None,
        )
        document_state_path = self.document_review_state_path(batch_name)
        if document_state_path.exists():
            document_state = update_document_review_state_after_strong_review(
                state=load_document_review_state(document_state_path),
                pack_json=strong_review_pack,
                review_summary=strong_review_summary,
            )
            write_document_review_state(document_state_path, document_state)
            state_counts = document_state["summary"]["state_counts"]
            final_pack_id = str(
                self.load_batch_state(batch_name).artifacts.get("final_review_pack_id")
                or f"yomi_final_{batch_name}_v1"
            )
            _, final_pack_artifacts = self._refresh_final_review_pack(
                batch_name=batch_name,
                pack_id=final_pack_id,
            )
            artifacts.update(
                {
                    "document_review_state_json": str(document_state_path),
                    "document_review_state_strong_pending": str(
                        state_counts.get("strong_pending", 0)
                    ),
                    "document_review_state_strong_reviewed": str(
                        state_counts.get("strong_reviewed", 0)
                    ),
                    "document_review_state_strong_apply_failed": str(
                        state_counts.get("strong_apply_failed", 0)
                    ),
                    "document_review_state_complete": str(
                        state_counts.get("complete", 0)
                    ),
                    "document_review_state_skipped": str(
                        state_counts.get("skipped", 0)
                    ),
                    **final_pack_artifacts,
                }
            )

        queue_jsonl = batch_dir / "yomi_strong_repair_queue.jsonl"
        results_jsonl = strong_repair_review_results_path(batch_dir)
        if queue_jsonl.exists() and results_jsonl.exists():
            artifacts.update(
                self._prepare_strong_repair_review_pack(
                    batch_name,
                    queue_jsonl=queue_jsonl,
                    results_jsonl=results_jsonl,
                    units_jsonl=strong_repaired_path,
                )
            )

        if not strong_review_summary.get("stage_complete", True):
            return {
                "stage_complete": False,
                "blocking_reason": str(strong_review_summary["blocking_reason"]),
                "artifacts": {
                    **artifacts,
                    "human_review_required": "true",
                    "human_review_gate": "yomi_strong_repair_review",
                    "human_review_item_count": str(strong_review_summary.get("item_count", "")),
                },
            }
        return {
            "artifacts": {
                **artifacts,
                "human_review_required": "false",
                "human_review_gate": "",
                "human_review_item_count": "",
            }
        }

    def _finalize_yomi(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        output_path = batch_dir / "units.yomi.final.jsonl"
        skipped_output_path = batch_dir / "units.yomi.skipped.jsonl"
        excluded_output_path = batch_dir / "units.yomi.excluded.jsonl"
        summary_path = batch_dir / "yomi_finalize_summary.json"
        strong_repaired_path = batch_dir / "units.yomi.strong_repaired.jsonl"
        strong_review_pack = batch_dir / "yomi_strong_repair_review_pack.json"
        strong_review_summary_path = batch_dir / "yomi_strong_repair_review_apply_summary.json"
        strong_import_artifacts: dict[str, str] = {}
        if strong_review_pack.exists():
            strong_review_result = self._apply_strong_repair_review(batch_name)
            strong_import_artifacts = {
                key: str(value)
                for key, value in strong_review_result.get("artifacts", {}).items()
                if str(key).startswith("yomi_strong_repair_review_")
            }
            strong_review_summary = (
                json.loads(strong_review_summary_path.read_text(encoding="utf-8"))
                if strong_review_summary_path.exists()
                else {}
            )
            if not strong_review_summary.get("stage_complete", True):
                return {
                    "stage_complete": False,
                    "blocking_reason": str(strong_review_summary["blocking_reason"]),
                    "artifacts": {
                        "units_yomi_final_jsonl": str(output_path),
                        "yomi_finalize_summary_json": str(summary_path),
                        "yomi_strong_repair_review_pack_json": str(strong_review_pack),
                        "yomi_strong_repair_review_apply_summary_json": str(
                            strong_review_summary_path
                        ),
                        **strong_import_artifacts,
                        "human_review_required": "true",
                        "human_review_gate": "yomi_strong_repair_review",
                        "human_review_item_count": str(strong_review_summary.get("item_count", "")),
                    },
                }
        summary = finalize_reviewed_yomi_file(
            units_jsonl=strong_repaired_path if strong_repaired_path.exists() else batch_dir / "units.yomi.reviewed.jsonl",
            reviewed_units_jsonl=batch_dir / "units.yomi.reviewed.jsonl"
            if strong_repaired_path.exists()
            else None,
            strong_queue_summary_json=batch_dir / "yomi_strong_repair_queue_summary.json",
            strong_apply_summary_json=batch_dir / "yomi_strong_repair_apply_summary.json",
            output_jsonl=output_path,
            summary_json=summary_path,
            skipped_output_jsonl=skipped_output_path,
            excluded_output_jsonl=excluded_output_path,
        )
        artifacts = {
            "units_yomi_final_jsonl": str(output_path),
            "units_yomi_skipped_jsonl": str(skipped_output_path),
            "units_yomi_excluded_jsonl": str(excluded_output_path),
            "yomi_finalize_summary_json": str(summary_path),
            **strong_import_artifacts,
        }
        if not summary.get("stage_complete", True):
            return {
                "stage_complete": False,
                "blocking_reason": str(summary["blocking_reason"]),
                "artifacts": {
                    **artifacts,
                    "yomi_strong_repair_queued": str(summary["queued_items"]),
                    "human_review_required": "true",
                    "human_review_gate": "yomi_strong_repair_review",
                    "human_review_item_count": str(summary["queued_items"]),
                },
            }
        document_state_path = self.document_review_state_path(batch_name)
        document_state_artifacts: dict[str, str] = {
            "document_review_state_json": str(document_state_path),
        }
        if document_state_path.exists():
            document_state = mark_document_review_state_finalized(
                load_document_review_state(document_state_path)
            )
            write_document_review_state(document_state_path, document_state)
            state_counts = document_state["summary"]["state_counts"]
            document_state_artifacts.update(
                {
                    "document_review_state_complete": str(state_counts.get("complete", 0)),
                    "document_review_state_skipped": str(state_counts.get("skipped", 0)),
                }
            )
        if batch_state.batch_kind == "recovery":
            application_ledger_path = batch_dir / "recovery_application_ledger.jsonl"
            application_rows = build_application_ledger(
                row
                for source_path in (
                    output_path,
                    skipped_output_path,
                    excluded_output_path,
                )
                for row in iter_jsonl(source_path)
            )
            application_rows.sort(
                key=lambda row: (
                    int(row["destination_track_doc_seq"]),
                    int(row["new_char_start"]),
                    str(row["recovery_unit_id"]),
                )
            )
            write_jsonl(application_ledger_path, application_rows)
            ready = sum(row["state"] == "ready_to_apply" for row in application_rows)
            skipped = sum(row["state"] == "skipped" for row in application_rows)
            excluded = sum(row["state"] == "excluded" for row in application_rows)
            recovery_summary_path = batch_dir / "recovery_finalization_summary.json"
            recovery_summary_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "batch_name": batch_name,
                        "campaign_id": batch_state.dataset_name.removeprefix("recovery:"),
                        "ready_to_apply": ready,
                        "skipped": skipped,
                        "excluded": excluded,
                        "canonical_export": False,
                        "global_lexicon_harvest": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return {
                "artifacts": {
                    **artifacts,
                    **document_state_artifacts,
                    "recovery_application_ledger_jsonl": str(application_ledger_path),
                    "recovery_finalization_summary_json": str(recovery_summary_path),
                    "recovery_ready_to_apply": str(ready),
                    "recovery_skipped": str(skipped),
                    "recovery_excluded": str(excluded),
                    "canonical_export": "false",
                    "global_lexicon_harvest": "false",
                    "human_review_required": "false",
                    "human_review_gate": "",
                    "human_review_item_count": "",
                }
            }
        harvest_summary_path = batch_dir / "yomi_finalization_harvest_summary.json"
        harvest_summary = harvest_yomi_finalization_artifacts_file(
            final_units_jsonl=output_path,
            batch_manual_rewrites_jsonl=batch_dir / "manual_yomi_rewrites.jsonl",
            batch_supplemental_furigana_tsv=batch_dir / "supplemental_furigana.tsv",
            global_manual_rewrites_jsonl=self.root / "data" / "lexicon" / "manual_yomi_rewrites.jsonl",
            global_supplemental_furigana_tsv=self.root / "data" / "lexicon" / "supplemental_furigana.tsv",
            strong_queue_jsonl=batch_dir / "yomi_strong_repair_queue.jsonl",
            batch_learned_readings_tsv=batch_dir / "learned_yomi_readings.tsv",
            global_learned_readings_tsv=self.root / "data" / "lexicon" / "learned_yomi_readings.tsv",
            summary_json=harvest_summary_path,
            batch_name=batch_name,
            track_name=batch_state.track_name,
        )
        return {
            "artifacts": {
                **artifacts,
                **document_state_artifacts,
                "yomi_final_written_units": str(summary["written_units"]),
                "yomi_final_skipped_units": str(summary["skipped_units"]),
                "yomi_final_unreviewed_units": str(summary["unreviewed_units"]),
                "yomi_finalization_harvest_summary_json": str(harvest_summary_path),
                "manual_yomi_rewrites_jsonl": str(batch_dir / "manual_yomi_rewrites.jsonl"),
                "manual_yomi_rewrites_appended": str(
                    harvest_summary["manual_rewrite_appended_count"]
                ),
                "supplemental_furigana_tsv": str(batch_dir / "supplemental_furigana.tsv"),
                "supplemental_furigana_appended": str(
                    harvest_summary["supplemental_furigana_appended_count"]
                ),
                "global_manual_yomi_rewrites_jsonl": str(
                    self.root / "data" / "lexicon" / "manual_yomi_rewrites.jsonl"
                ),
                "global_supplemental_furigana_tsv": str(
                    self.root / "data" / "lexicon" / "supplemental_furigana.tsv"
                ),
                "learned_yomi_readings_tsv": str(batch_dir / "learned_yomi_readings.tsv"),
                "learned_yomi_readings_appended": str(
                    harvest_summary["learned_reading_appended_count"]
                ),
                "global_learned_yomi_readings_tsv": str(
                    self.root / "data" / "lexicon" / "learned_yomi_readings.tsv"
                ),
                "human_review_required": "false",
                "human_review_gate": "",
                "human_review_item_count": "",
            }
        }

    @staticmethod
    def _llm_running_artifacts(
        *,
        prefix: str,
        task_config_path: str,
        task_config: object,
        llm_profile: str,
        execution_mode: str,
        job_dir: Path,
        job_summary: object,
        queued_count: int,
    ) -> dict[str, str]:
        return {
            f"{prefix}_task_config": task_config_path,
            f"{prefix}_model": str(task_config.model),
            f"{prefix}_llm_profile": llm_profile,
            f"{prefix}_execution_mode": execution_mode,
            f"{prefix}_reasoning_effort": task_config.reasoning_effort or "",
            f"{prefix}_llm_job_dir": str(job_dir),
            f"{prefix}_llm_job_status": str(job_summary.status),
            f"{prefix}_llm_job_status_reason": str(getattr(job_summary, "status_reason", "") or ""),
            f"{prefix}_llm_remote_status": job_summary.remote_status or "",
            f"{prefix}_llm_remote_batch_id": job_summary.remote_batch_id or "",
            f"{prefix}_llm_job_completed": str(job_summary.completed_items),
            f"{prefix}_llm_job_failed": str(job_summary.failed_items),
            f"{prefix}_llm_job_total": str(job_summary.total_items),
            f"{prefix}_prompt_template": str(task_config.prompt_template),
            f"{prefix}_queued": str(queued_count),
        }

    @staticmethod
    def _llm_incomplete_blocking_reason(
        *,
        execution_mode: str,
        job_summary: object,
        label: str = "job",
    ) -> str:
        status = str(getattr(job_summary, "status", "unknown"))
        reason = str(getattr(job_summary, "status_reason", "") or "")
        detail = f" ({reason})" if reason else ""
        return f"LLM {execution_mode} {label} is {status}{detail}; rerun ./next to poll or resume."

    def _llm_completed_artifacts(
        self,
        *,
        prefix: str,
        results_path: Path,
        usage_summary_path: Path,
        apply_summary_path: Path,
        task_config_path: str,
        task_config: object,
        llm_profile: str,
        execution_mode: str,
        job_dir: Path,
        job_summary: object | None,
        queued_count: int,
    ) -> dict[str, str]:
        return {
            f"{prefix}_results_jsonl": str(results_path),
            f"{prefix}_usage_summary_json": str(usage_summary_path),
            f"{prefix}_apply_summary_json": str(apply_summary_path),
            **self._llm_running_artifacts(
                prefix=prefix,
                task_config_path=task_config_path,
                task_config=task_config,
                llm_profile=llm_profile,
                execution_mode=execution_mode,
                job_dir=job_dir,
                job_summary=EmptyJobSummary() if job_summary is None else job_summary,
                queued_count=queued_count,
            ),
            f"{prefix}_llm_job_status": "completed",
        }

def write_effective_yomi_strong_repair_results(
    *,
    queue_jsonl: Path,
    result_jsonls: list[Path],
    output_jsonl: Path,
) -> list[dict[str, object]]:
    queue_rows = [
        json.loads(line)
        for line in queue_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    queue_by_id = {str(row.get("item_id") or ""): row for row in queue_rows}
    effective_rows: list[dict[str, object]] = []
    latest_by_id: dict[str, dict[str, object]] = {}
    for path in result_jsonls:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = str(row.get("item_id") or "")
            normalized = dict(row)
            if item_id and not normalized.get("parse_error"):
                queue_row = queue_by_id.get(item_id)
                try:
                    validate_yomi_repair_surface(
                        normalized.get("parsed"),
                        metadata={"source_row": queue_row} if queue_row else None,
                    )
                except ValueError as exc:
                    normalized["parse_error"] = str(exc)
                    normalized["parsed"] = None
            effective_rows.append(normalized)
            if item_id:
                latest_by_id[item_id] = normalized
    write_jsonl_rows(output_jsonl, effective_rows)
    return [
        row
        for row in queue_rows
        if (
            str(row.get("item_id") or "") not in latest_by_id
            or latest_by_id[str(row.get("item_id") or "")].get("parse_error")
        )
    ]


def write_jsonl_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def count_nonempty_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def merge_review_gates(existing: list[str], incoming: object) -> list[str]:
    merged = list(existing)
    if not isinstance(incoming, list):
        return merged
    seen = set(merged)
    for gate in incoming:
        gate_text = str(gate)
        if gate_text in seen:
            continue
        seen.add(gate_text)
        merged.append(gate_text)
    return merged
