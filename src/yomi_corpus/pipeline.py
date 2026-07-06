from __future__ import annotations

import gzip
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import tomllib

from yomi_corpus.alphabetic import (
    apply_global_decisions,
    aggregate_occurrences,
    attach_examples_to_types,
    build_occurrences_for_unit,
    load_alphabetic_config,
    project_alphabetic_scope,
    project_minor_alphabetic_judgment,
)
from yomi_corpus.alphabetic_reports import build_unresolved_entity_rows
from yomi_corpus.alphabetic_review import (
    append_alphabetic_llm_judgments,
    build_llm_judgments_from_results,
    load_jsonl as load_alphabetic_review_jsonl,
)
from yomi_corpus.alphabetic_state import (
    AlphabeticDecision,
    AlphabeticEvidence,
    append_alphabetic_evidence,
    decision_status_to_resolved_status,
    load_alphabetic_decisions,
    upsert_alphabetic_decision,
)
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
from yomi_corpus.paths import resolve_repo_path
from yomi_corpus.llm.pricing import DEFAULT_PRICING_CONFIG_PATH
from yomi_corpus.llm.runner import run_llm_task
from yomi_corpus.llm.usage_report import summarize_results_jsonl
from yomi_corpus.models import UnitRecord, empty_analysis
from yomi_corpus.splitter import split_text_into_units
from yomi_corpus.yomi.acceptance import apply_yomi_auto_acceptance_file
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
    write_summary as write_yomi_final_review_summary,
)
from yomi_corpus.yomi.final_review_issue_import import import_open_issue_inbox
from yomi_corpus.yomi.llm_readings import (
    apply_yomi_llm_reading_results_file,
    build_yomi_llm_reading_queue_file,
    build_yomi_llm_reading_retry_queue_file,
)
from yomi_corpus.yomi.safety import apply_yomi_safety_pre_llm_file
from yomi_corpus.yomi.scope import apply_scope_triage_results_file, build_scope_triage_queue_file


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
LLM_TASK_ALPHABETIC_ENTITY_JUDGE = "alphabetic_entity_judge"
LLM_TASK_SCOPE_TRIAGE = "scope_triage"
LLM_TASK_YOMI_READING = "yomi_reading"
LLM_TASK_YOMI_REPAIR = "yomi_repair"
LLM_TASK_YOMI_RESCUE = "yomi_rescue"
LLM_POLICY_TASKS = (
    LLM_TASK_ALPHABETIC_ENTITY_JUDGE,
    LLM_TASK_SCOPE_TRIAGE,
    LLM_TASK_YOMI_READING,
    LLM_TASK_YOMI_REPAIR,
    LLM_TASK_YOMI_RESCUE,
)
LLM_POLICY_TASK_SET = frozenset(LLM_POLICY_TASKS)
LEGACY_LLM_TASK_MAP = {
    "non_target_judge": LLM_TASK_SCOPE_TRIAGE,
    "yomi_triage": LLM_TASK_YOMI_READING,
}
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
STAGE_ALPHABETIC_ANALYZED = "alphabetic_analyzed"
STAGE_ALPHABETIC_REPORTED = "alphabetic_reported"
STAGE_ALPHABETIC_LLM_JUDGED = "alphabetic_llm_judged"
STAGE_ALPHABETIC_PROMOTION_CANDIDATES = "alphabetic_promotion_candidates"
STAGE_SCOPE_TRIAGE_QUEUED = "scope_triage_queued"
STAGE_SCOPE_TRIAGE_LLM_COMPLETED = "scope_triage_llm_completed"
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
    "alphabetic_judged": STAGE_ALPHABETIC_LLM_JUDGED,
    "scope_triage_completed": STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
    "yomi_reading_completed": STAGE_YOMI_READING_LLM_COMPLETED,
}
STAGE_LLM_TASKS = {
    STAGE_ALPHABETIC_LLM_JUDGED: LLM_TASK_ALPHABETIC_ENTITY_JUDGE,
    STAGE_SCOPE_TRIAGE_LLM_COMPLETED: LLM_TASK_SCOPE_TRIAGE,
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
    STAGE_ALPHABETIC_ANALYZED,
    STAGE_ALPHABETIC_REPORTED,
    STAGE_ALPHABETIC_LLM_JUDGED,
    STAGE_ALPHABETIC_PROMOTION_CANDIDATES,
    STAGE_SCOPE_TRIAGE_QUEUED,
    STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
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
        STAGE_ALPHABETIC_ANALYZED,
        STAGE_ALPHABETIC_REPORTED,
        STAGE_ALPHABETIC_LLM_JUDGED,
        STAGE_ALPHABETIC_PROMOTION_CANDIDATES,
        STAGE_YOMI_GENERATED,
        STAGE_YOMI_AUTO_ACCEPTED,
        STAGE_SCOPE_TRIAGE_QUEUED,
        STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
        STAGE_YOMI_READING_QUEUED,
        STAGE_YOMI_READING_LLM_COMPLETED,
        STAGE_FINAL_REVIEW_PREPARED,
        STAGE_FINAL_REVIEW_APPLIED,
        STAGE_YOMI_STRONG_REPAIR_QUEUED,
        STAGE_YOMI_STRONG_REPAIR_LLM_COMPLETED,
        STAGE_YOMI_FINALIZED,
    }
)


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
            task_name = LEGACY_LLM_TASK_MAP.get(str(task), str(task))
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
            task_name = LEGACY_LLM_TASK_MAP.get(str(task), str(task))
            if task_name not in LLM_POLICY_TASK_SET:
                raise ValueError(f"Unsupported LLM task in execution policy: {task_name}")
            normalized[task_name] = str(mode)
    for task, mode in normalized.items():
        if mode not in LLM_EXECUTION_MODES:
            raise ValueError(f"Unsupported LLM execution mode for {task}: {mode}")
    return normalized


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

    def track_state_path(self, track_name: str) -> Path:
        return self.tracks_root() / f"{track_name}.json"

    def batch_state_path(self, batch_name: str) -> Path:
        return self.batches_root() / f"{batch_name}.json"

    def document_review_state_path(self, batch_name: str) -> Path:
        return self.document_states_root() / f"{batch_name}.json"

    def batch_dir(self, batch_name: str) -> Path:
        return self.units_root() / batch_name

    def manifest_path(self, batch_name: str) -> Path:
        return self.batch_dir(batch_name) / "manifest.json"

    def ensure_dirs(self) -> None:
        self.tracks_root().mkdir(parents=True, exist_ok=True)
        self.batches_root().mkdir(parents=True, exist_ok=True)
        self.document_states_root().mkdir(parents=True, exist_ok=True)

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
        batch_name = self._allocate_next_batch_name(normalized)
        dataset = self._load_dataset_config(dataset_config_path)
        track_state = self.load_track_state(normalized)
        decoder_model_dir = track_state.decoder_model_dir

        skip_source_line_no = self._latest_source_line_no_for_track(
            track_name=normalized,
            dataset_name=str(dataset["name"]),
            dataset_source_path=Path(dataset["source_path"]),
        )

        (
            docs_written,
            units_written,
            source_start_line_no,
            source_end_line_no,
        ) = self._extract_batch_documents(
            source_path=dataset["source_path"],
            dataset_name=dataset["name"],
            target_documents=target_documents,
            batch_name=batch_name,
            skip_source_line_no=skip_source_line_no,
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
        if stage_name == STAGE_ALPHABETIC_ANALYZED:
            return self._run_alphabetic_analysis(batch_name)
        if stage_name == STAGE_ALPHABETIC_REPORTED:
            return self._build_unresolved_alphabetic_report(batch_name)
        if stage_name == STAGE_ALPHABETIC_LLM_JUDGED:
            return self._run_alphabetic_entity_judgment(
                batch_name,
                llm_execution_mode_override=llm_execution_mode_override,
            )
        if stage_name == STAGE_ALPHABETIC_PROMOTION_CANDIDATES:
            return self._build_alphabetic_promotion_candidates(
                batch_name,
                skip_review_gates=skip_review_gates,
            )
        if stage_name == STAGE_YOMI_GENERATED:
            return self._generate_mechanical_yomi(batch_name)
        if stage_name == STAGE_YOMI_AUTO_ACCEPTED:
            return self._auto_accept_mechanical_yomi(batch_name)
        if stage_name == STAGE_SCOPE_TRIAGE_QUEUED:
            return self._queue_scope_triage(batch_name)
        if stage_name == STAGE_SCOPE_TRIAGE_LLM_COMPLETED:
            return self._run_scope_triage(
                batch_name,
                llm_execution_mode_override=llm_execution_mode_override,
            )
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
        if stage_name == STAGE_ALPHABETIC_ANALYZED:
            return [
                batch_dir / "units.alphabetic.jsonl",
                batch_dir / "alphabetic_occurrences.jsonl",
                batch_dir / "alphabetic_types.jsonl",
            ]
        if stage_name == STAGE_ALPHABETIC_REPORTED:
            return [
                batch_dir / "alphabetic_unresolved_entities.jsonl",
                batch_dir / "alphabetic_unresolved_entities.tsv",
            ]
        if stage_name == STAGE_ALPHABETIC_LLM_JUDGED:
            return [
                batch_dir / "alphabetic_judgment_input.jsonl",
                batch_dir / "alphabetic_judgment_results.jsonl",
                batch_dir / "alphabetic_judgment_usage_summary.json",
                batch_dir / "alphabetic_judgment_ingest_summary.json",
            ]
        if stage_name == STAGE_ALPHABETIC_PROMOTION_CANDIDATES:
            return [
                batch_dir / "alphabetic_promotion_candidates_summary.json",
            ]
        if stage_name == STAGE_YOMI_GENERATED:
            return [
                batch_dir / "units.yomi.aligned_hybrid.jsonl",
            ]
        if stage_name == STAGE_YOMI_AUTO_ACCEPTED:
            return [
                batch_dir / "units.yomi.auto_accept.jsonl",
                batch_dir / "yomi_auto_accept_summary.json",
            ]
        if stage_name == STAGE_SCOPE_TRIAGE_QUEUED:
            return [
                batch_dir / "scope_triage_input.jsonl",
                batch_dir / "scope_triage_queue_summary.json",
            ]
        if stage_name == STAGE_SCOPE_TRIAGE_LLM_COMPLETED:
            return [
                batch_dir / "scope_triage_results.jsonl",
                batch_dir / "scope_triage_usage_summary.json",
                batch_dir / "units.scope_triaged.jsonl",
                batch_dir / "scope_triage_apply_summary.json",
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
            STAGE_ALPHABETIC_ANALYZED,
            STAGE_ALPHABETIC_REPORTED,
            STAGE_ALPHABETIC_LLM_JUDGED,
            STAGE_ALPHABETIC_PROMOTION_CANDIDATES,
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_SCOPE_TRIAGE_QUEUED,
            STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts.update(
                {
                    "units_alphabetic_jsonl": str(self.batch_dir(batch_name) / "units.alphabetic.jsonl"),
                    "alphabetic_occurrences_jsonl": str(self.batch_dir(batch_name) / "alphabetic_occurrences.jsonl"),
                    "alphabetic_types_jsonl": str(self.batch_dir(batch_name) / "alphabetic_types.jsonl"),
                }
            )
        if current_stage in {
            STAGE_ALPHABETIC_REPORTED,
            STAGE_ALPHABETIC_LLM_JUDGED,
            STAGE_ALPHABETIC_PROMOTION_CANDIDATES,
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_SCOPE_TRIAGE_QUEUED,
            STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts.update(
                {
                    "alphabetic_unresolved_jsonl": str(
                        self.batch_dir(batch_name) / "alphabetic_unresolved_entities.jsonl"
                    ),
                    "alphabetic_unresolved_tsv": str(
                        self.batch_dir(batch_name) / "alphabetic_unresolved_entities.tsv"
                    ),
                }
            )
        if current_stage in {
            STAGE_ALPHABETIC_LLM_JUDGED,
            STAGE_ALPHABETIC_PROMOTION_CANDIDATES,
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_SCOPE_TRIAGE_QUEUED,
            STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts.update(
                {
                    "alphabetic_judgment_results_jsonl": str(
                        self.batch_dir(batch_name) / "alphabetic_judgment_results.jsonl"
                    ),
                    "alphabetic_judgment_input_jsonl": str(
                        self.batch_dir(batch_name) / "alphabetic_judgment_input.jsonl"
                    ),
                    "alphabetic_judgment_usage_summary_json": str(
                        self.batch_dir(batch_name) / "alphabetic_judgment_usage_summary.json"
                    ),
                    "alphabetic_judgment_ingest_summary_json": str(
                        self.batch_dir(batch_name) / "alphabetic_judgment_ingest_summary.json"
                    ),
                }
            )
        if current_stage in {
            STAGE_ALPHABETIC_PROMOTION_CANDIDATES,
            STAGE_SCOPE_TRIAGE_QUEUED,
            STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts.update(
                {
                    "alphabetic_promotion_candidates_summary_json": str(
                        self.batch_dir(batch_name) / "alphabetic_promotion_candidates_summary.json"
                    ),
                    "alphabetic_projection_summary_json": str(
                        self.batch_dir(batch_name) / "alphabetic_promotion_candidates_summary.json"
                    ),
                    "alphabetic_token_decisions_jsonl": str(
                        self.root / "data" / "state" / "alphabetic" / "token_decisions.jsonl"
                    ),
                }
            )
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
        if current_stage in {
            STAGE_SCOPE_TRIAGE_QUEUED,
            STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts["scope_triage_input_jsonl"] = str(
                self.batch_dir(batch_name) / "scope_triage_input.jsonl"
            )
            artifacts["scope_triage_queue_summary_json"] = str(
                self.batch_dir(batch_name) / "scope_triage_queue_summary.json"
            )
        if current_stage in {
            STAGE_SCOPE_TRIAGE_LLM_COMPLETED,
            STAGE_YOMI_GENERATED,
            STAGE_YOMI_AUTO_ACCEPTED,
            STAGE_YOMI_READING_QUEUED,
            STAGE_YOMI_READING_LLM_COMPLETED,
        }:
            artifacts["scope_triage_results_jsonl"] = str(
                self.batch_dir(batch_name) / "scope_triage_results.jsonl"
            )
            artifacts["scope_triage_usage_summary_json"] = str(
                self.batch_dir(batch_name) / "scope_triage_usage_summary.json"
            )
            artifacts["units_scope_triaged_jsonl"] = str(
                self.batch_dir(batch_name) / "units.scope_triaged.jsonl"
            )
            artifacts["scope_triage_apply_summary_json"] = str(
                self.batch_dir(batch_name) / "scope_triage_apply_summary.json"
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
            artifacts["review_site_manifest_json"] = str(
                self.root / "docs" / "review" / "manifest.json"
            )
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
        if (batch_dir / "units.scope_triaged.jsonl").exists():
            return STAGE_SCOPE_TRIAGE_LLM_COMPLETED
        if (batch_dir / "scope_triage_input.jsonl").exists():
            return STAGE_SCOPE_TRIAGE_QUEUED
        if (batch_dir / "units.yomi.triaged.jsonl").exists():
            return STAGE_YOMI_READING_LLM_COMPLETED
        if (batch_dir / "yomi_triage_input.jsonl").exists():
            return STAGE_SCOPE_TRIAGE_QUEUED
        if (batch_dir / "alphabetic_promotion_candidates_summary.json").exists():
            return STAGE_ALPHABETIC_PROMOTION_CANDIDATES
        if (batch_dir / "alphabetic_judgment_ingest_summary.json").exists():
            return STAGE_ALPHABETIC_LLM_JUDGED
        if (batch_dir / "alphabetic_unresolved_entities.jsonl").exists():
            return STAGE_ALPHABETIC_REPORTED
        if (batch_dir / "units.alphabetic.jsonl").exists():
            return STAGE_ALPHABETIC_ANALYZED
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

    def _extract_batch_documents(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        target_documents: int,
        batch_name: str,
        skip_source_line_no: int = 0,
    ) -> tuple[int, int, int | None, int | None]:
        output_dir = self.batch_dir(batch_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        units_path = output_dir / "units.jsonl"

        units_written = 0
        docs_written = 0
        source_start_line_no: int | None = None
        source_end_line_no: int | None = None
        with gzip.open(source_path, "rt", encoding="utf-8") as handle, units_path.open(
            "w", encoding="utf-8"
        ) as out:
            for source_line_no, line in enumerate(handle, start=1):
                if source_line_no <= skip_source_line_no:
                    continue
                payload = json.loads(line)
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                docs_written += 1
                if source_start_line_no is None:
                    source_start_line_no = source_line_no
                source_end_line_no = source_line_no
                doc_id = f"{dataset_name}:{source_line_no:010d}"
                source_file = str(payload.get("source_file", ""))
                spans = split_text_into_units(text)
                for unit_seq, span in enumerate(spans, start=1):
                    units_written += 1
                    unit = UnitRecord(
                        doc_id=doc_id,
                        unit_id=f"{doc_id}:u{unit_seq:04d}",
                        unit_seq=unit_seq,
                        char_start=span.start,
                        char_end=span.end,
                        text=span.text,
                        source_file=source_file,
                        source_line_no=source_line_no,
                        analysis=empty_analysis(),
                    )
                    out.write(json.dumps(unit.to_dict(), ensure_ascii=False) + "\n")
                if docs_written >= target_documents:
                    break
        return docs_written, units_written, source_start_line_no, source_end_line_no

    def _latest_source_line_no_for_track(
        self,
        *,
        track_name: str,
        dataset_name: str,
        dataset_source_path: Path,
    ) -> int:
        latest = 0
        expected_source = str(dataset_source_path)
        for manifest_path in self.units_root().glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("track_name") != track_name:
                continue
            if manifest.get("dataset_name") != dataset_name:
                continue
            if str(manifest.get("dataset_source_path", "")) != expected_source:
                continue
            line_no = manifest.get("source_end_line_no")
            if isinstance(line_no, int):
                latest = max(latest, line_no)
                continue
            units_path = manifest_path.parent / "units.jsonl"
            latest = max(latest, self._max_source_line_no_from_units(units_path))
        return latest

    @staticmethod
    def _max_source_line_no_from_units(units_path: Path) -> int:
        if not units_path.exists():
            return 0
        latest = 0
        with units_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                source_line_no = row.get("source_line_no")
                if isinstance(source_line_no, int):
                    latest = max(latest, source_line_no)
        return latest

    def _run_alphabetic_analysis(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        config = load_alphabetic_config("config/alphabetic/default.toml")
        input_path = batch_dir / "units.jsonl"
        output_units_path = batch_dir / "units.alphabetic.jsonl"
        output_occurrences_path = batch_dir / "alphabetic_occurrences.jsonl"
        output_types_path = batch_dir / "alphabetic_types.jsonl"
        global_decisions_path = self.root / "data" / "state" / "alphabetic" / "token_decisions.jsonl"
        global_evidence_path = self.root / "data" / "state" / "alphabetic" / "token_evidence.jsonl"
        output_units_path.parent.mkdir(parents=True, exist_ok=True)

        units: list[dict] = []
        occurrences_by_unit: dict[str, list] = {}
        all_occurrences = []
        unit_text_by_id: dict[str, str] = {}
        decision_status_by_key = {
            entity_key: decision_status_to_resolved_status(decision.status)
            for entity_key, decision in load_alphabetic_decisions(global_decisions_path).items()
        }
        decisions_by_key = load_alphabetic_decisions(global_decisions_path)

        with input_path.open(encoding="utf-8") as src:
            for line in src:
                unit = json.loads(line)
                units.append(unit)
                unit_text_by_id[str(unit["unit_id"])] = str(unit["text"])
                occurrences = apply_global_decisions(
                    build_occurrences_for_unit(unit, config),
                    decision_status_by_key,
                )
                occurrences_by_unit[str(unit["unit_id"])] = occurrences
                all_occurrences.extend(occurrences)

        types = attach_examples_to_types(
            aggregate_occurrences(all_occurrences),
            unit_text_by_id,
        )

        with output_occurrences_path.open("w", encoding="utf-8") as dst:
            for occurrence in all_occurrences:
                dst.write(json.dumps(asdict(occurrence), ensure_ascii=False) + "\n")

        with output_types_path.open("w", encoding="utf-8") as dst:
            for entity_type in types:
                dst.write(json.dumps(asdict(entity_type), ensure_ascii=False) + "\n")

        append_alphabetic_evidence(
            global_evidence_path,
            [
                AlphabeticEvidence(
                    batch_name=batch_name,
                    entity_key=entity_type.entity_key,
                    strict_case=entity_type.strict_case,
                    resolved_status=entity_type.resolved_status,
                    base_list_status=entity_type.base_list_status,
                    occurrence_count=entity_type.occurrence_count,
                    unit_count=entity_type.unit_count,
                    surface_forms=entity_type.surface_forms,
                    example_unit_ids=entity_type.example_unit_ids,
                )
                for entity_type in types
            ],
        )

        with output_units_path.open("w", encoding="utf-8") as dst:
            for unit in units:
                judgment = project_minor_alphabetic_judgment(occurrences_by_unit[str(unit["unit_id"])])
                unit["analysis"]["mechanical"]["minor_alphabetic_sequence"] = {
                    "value": judgment.value,
                    "certain": judgment.certain,
                    "signals": judgment.signals,
                    "matches": judgment.matches,
                    "decision_granularity": "entity_type",
                }
                unit["analysis"]["mechanical"]["alphabetic_scope"] = project_alphabetic_scope(
                    occurrences_by_unit[str(unit["unit_id"])],
                    decisions_by_key,
                )
                dst.write(json.dumps(unit, ensure_ascii=False) + "\n")

        return {
            "artifacts": {
                "units_alphabetic_jsonl": str(output_units_path),
                "alphabetic_occurrences_jsonl": str(output_occurrences_path),
                "alphabetic_types_jsonl": str(output_types_path),
            }
        }

    def _build_unresolved_alphabetic_report(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        input_types_path = batch_dir / "alphabetic_types.jsonl"
        output_jsonl_path = batch_dir / "alphabetic_unresolved_entities.jsonl"
        output_tsv_path = batch_dir / "alphabetic_unresolved_entities.tsv"

        rows: list[dict] = []
        with input_types_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))

        unresolved = build_unresolved_entity_rows(
            rows,
            min_occurrences=1,
            max_examples=3,
            max_example_chars=160,
        )

        with output_jsonl_path.open("w", encoding="utf-8") as handle:
            for row in unresolved:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        with output_tsv_path.open("w", encoding="utf-8") as handle:
            header = [
                "entity_key",
                "strict_case",
                "resolved_status",
                "base_list_status",
                "occurrence_count",
                "unit_count",
                "surface_forms",
                "example_unit_ids",
                "example_texts",
            ]
            handle.write("\t".join(header) + "\n")
            for row in unresolved:
                handle.write(
                    "\t".join(
                        [
                            row["entity_key"],
                            str(row["strict_case"]),
                            row["resolved_status"],
                            row["base_list_status"],
                            str(row["occurrence_count"]),
                            str(row["unit_count"]),
                            " | ".join(row["surface_forms"]),
                            " | ".join(row["example_unit_ids"]),
                            " || ".join(
                                text.replace("\t", " ").replace("\n", " ") for text in row["example_texts"]
                            ),
                        ]
                    )
                    + "\n"
                )
        return {
            "artifacts": {
                "alphabetic_unresolved_jsonl": str(output_jsonl_path),
                "alphabetic_unresolved_tsv": str(output_tsv_path),
            }
        }

    def _run_alphabetic_entity_judgment(
        self,
        batch_name: str,
        *,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        task_config_path = "config/llm/alphabetic_entity_judge.toml"
        llm_profile = batch_state.llm_policy[LLM_TASK_ALPHABETIC_ENTITY_JUDGE]
        execution_mode = (
            llm_execution_mode_override
            or batch_state.llm_execution_policy[LLM_TASK_ALPHABETIC_ENTITY_JUDGE]
        )
        base_task_config = load_llm_task_config(task_config_path)
        task_config = apply_llm_profile(base_task_config, llm_profile)
        input_path = batch_dir / "alphabetic_unresolved_entities.jsonl"
        llm_input_path = batch_dir / "alphabetic_judgment_input.jsonl"
        results_path = batch_dir / "alphabetic_judgment_results.jsonl"
        usage_summary_path = batch_dir / "alphabetic_judgment_usage_summary.json"
        ingest_summary_path = batch_dir / "alphabetic_judgment_ingest_summary.json"
        ledger_path = self.root / "data" / "state" / "alphabetic" / "llm_judgments.jsonl"
        decisions_path = self.root / "data" / "state" / "alphabetic" / "token_decisions.jsonl"
        job_dir = self.root / "data" / "llm" / "jobs" / f"{batch_name}_alphabetic_judgment"

        existing_decisions = load_alphabetic_decisions(decisions_path)
        llm_input_path.parent.mkdir(parents=True, exist_ok=True)
        input_entities = 0
        cached_entity_skips = 0
        with input_path.open(encoding="utf-8") as src, llm_input_path.open("w", encoding="utf-8") as dst:
            for line in src:
                if not line.strip():
                    continue
                input_entities += 1
                row = json.loads(line)
                if str(row.get("entity_key", "")) in existing_decisions:
                    cached_entity_skips += 1
                    continue
                dst.write(json.dumps(row, ensure_ascii=False) + "\n")

        queued_count = count_nonempty_lines(llm_input_path)
        job_summary = None
        if queued_count:
            job_summary = run_llm_task(
                task_config_path,
                str(llm_input_path),
                str(results_path),
                execution_mode=execution_mode,
                task_config_override=task_config,
                job_dir=str(job_dir),
                show_progress=True,
            )
            if job_summary.status != "completed":
                return {
                    "stage_complete": False,
                    "blocking_reason": (
                        f"LLM {execution_mode} job is {job_summary.status}; "
                        "rerun ./next to poll or resume."
                    ),
                    "artifacts": self._llm_running_artifacts(
                        prefix="alphabetic_judgment",
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

        result_rows = load_alphabetic_review_jsonl(results_path)
        judgments = build_llm_judgments_from_results(
            result_rows,
            batch_name=batch_name,
            source_path=str(results_path),
        )
        append_alphabetic_llm_judgments(ledger_path, judgments)
        cached_decisions = 0
        for judgment in judgments:
            existing = existing_decisions.get(judgment.entity_key)
            if existing is not None and existing.source not in {"llm", "unknown"}:
                continue
            upsert_alphabetic_decision(
                decisions_path,
                AlphabeticDecision(
                    entity_key=judgment.entity_key,
                    strict_case=judgment.strict_case,
                    status=judgment.llm_status,
                    source="llm",
                    note=judgment.note,
                ),
            )
            cached_decisions += 1
        ingest_summary = {
            "batch_name": batch_name,
            "input_entities": input_entities,
            "queued_entities": queued_count,
            "cached_entity_skips": cached_entity_skips,
            "result_rows": len(result_rows),
            "ingested_judgments": len(judgments),
            "cached_decisions": cached_decisions,
            "ledger_jsonl": str(ledger_path),
            "decisions_jsonl": str(decisions_path),
        }
        ingest_summary_path.write_text(
            json.dumps(ingest_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return {
            "artifacts": {
                **self._llm_completed_artifacts(
                    prefix="alphabetic_judgment",
                    results_path=results_path,
                    usage_summary_path=usage_summary_path,
                    apply_summary_path=ingest_summary_path,
                    task_config_path=task_config_path,
                    task_config=task_config,
                    llm_profile=llm_profile,
                    execution_mode=execution_mode,
                    job_dir=job_dir,
                    job_summary=job_summary,
                    queued_count=queued_count,
                ),
                "alphabetic_judgment_input_jsonl": str(llm_input_path),
                "alphabetic_judgment_ingested": str(len(judgments)),
                "alphabetic_judgment_cached_decisions": str(cached_decisions),
                "alphabetic_judgment_cached_entity_skips": str(cached_entity_skips),
                "alphabetic_llm_judgments_jsonl": str(ledger_path),
                "alphabetic_token_decisions_jsonl": str(decisions_path),
            }
        }

    def _build_alphabetic_promotion_candidates(
        self,
        batch_name: str,
        *,
        skip_review_gates: bool = False,
    ) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        decisions_path = self.root / "data" / "state" / "alphabetic" / "token_decisions.jsonl"
        summary_path = batch_dir / "alphabetic_promotion_candidates_summary.json"
        decisions = load_alphabetic_decisions(decisions_path)
        input_path = batch_dir / "units.alphabetic.jsonl"
        config = load_alphabetic_config("config/alphabetic/default.toml")
        decision_status_by_key = {
            entity_key: decision_status_to_resolved_status(decision.status)
            for entity_key, decision in decisions.items()
        }

        read_units = 0
        provisional_skip_units = 0
        unresolved_units = 0
        in_scope_units = 0
        updated_units: list[dict] = []
        if input_path.exists():
            with input_path.open(encoding="utf-8") as src:
                for line in src:
                    if not line.strip():
                        continue
                    read_units += 1
                    unit = json.loads(line)
                    occurrences = apply_global_decisions(
                        build_occurrences_for_unit(unit, config),
                        decision_status_by_key,
                    )
                    judgment = project_minor_alphabetic_judgment(occurrences)
                    alphabetic_scope = project_alphabetic_scope(occurrences, decisions)
                    mechanical = unit.setdefault("analysis", {}).setdefault("mechanical", {})
                    mechanical["minor_alphabetic_sequence"] = {
                        "value": judgment.value,
                        "certain": judgment.certain,
                        "signals": judgment.signals,
                        "matches": judgment.matches,
                        "decision_granularity": "entity_type",
                    }
                    mechanical["alphabetic_scope"] = alphabetic_scope
                    if alphabetic_scope["status"] == "provisional_skip":
                        provisional_skip_units += 1
                    elif alphabetic_scope["status"] == "unresolved":
                        unresolved_units += 1
                    else:
                        in_scope_units += 1
                    updated_units.append(unit)

            with input_path.open("w", encoding="utf-8") as dst:
                for unit in updated_units:
                    dst.write(json.dumps(unit, ensure_ascii=False) + "\n")

        summary = {
            "batch_name": batch_name,
            "read_units": read_units,
            "existing_decisions": len(decisions),
            "provisional_skip_units": provisional_skip_units,
            "unresolved_units": unresolved_units,
            "in_scope_units": in_scope_units,
            "units_alphabetic_jsonl": str(input_path),
            "decisions_jsonl": str(decisions_path),
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if skip_review_gates:
            summary["skip_review_gates_ignored"] = True
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        return {
            "artifacts": {
                "alphabetic_promotion_candidates_summary_json": str(summary_path),
                "alphabetic_projection_summary_json": str(summary_path),
                "alphabetic_token_decisions_jsonl": str(decisions_path),
                "alphabetic_provisional_skip_units": str(provisional_skip_units),
                "alphabetic_unresolved_units": str(unresolved_units),
                "alphabetic_in_scope_units": str(in_scope_units),
                "alphabetic_projection_units_jsonl": str(input_path),
            }
        }

    def _generate_mechanical_yomi(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        input_path = batch_dir / "units.scope_triaged.jsonl"
        summary = export_named_variant(
            variant_name="aligned_hybrid",
            batch_dir=batch_dir,
            config_path="config/yomi/default.toml",
            formats=["jsonl"],
            show_progress=True,
            input_jsonl=input_path,
            skip_scope_skipped=True,
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
            }
        }

    def _queue_scope_triage(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        input_path = batch_dir / "units.alphabetic.jsonl"
        output_path = batch_dir / "scope_triage_input.jsonl"
        summary_path = batch_dir / "scope_triage_queue_summary.json"
        summary = build_scope_triage_queue_file(
            input_jsonl=input_path,
            output_jsonl=output_path,
            summary_json=summary_path,
        )
        return {
            "artifacts": {
                "scope_triage_input_jsonl": str(output_path),
                "scope_triage_source_jsonl": str(input_path),
                "scope_triage_queue_summary_json": str(summary_path),
                "scope_triage_task_config": "config/llm/scope_triage.toml",
                "scope_triage_queued": str(summary.queued),
                "scope_triage_provisional_alphabetic_skip": str(
                    summary.provisional_alphabetic_skip
                ),
            }
        }

    def _run_scope_triage(
        self,
        batch_name: str,
        *,
        llm_execution_mode_override: str | None = None,
    ) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        task_config_path = "config/llm/scope_triage.toml"
        llm_profile = batch_state.llm_policy[LLM_TASK_SCOPE_TRIAGE]
        execution_mode = (
            llm_execution_mode_override
            or batch_state.llm_execution_policy[LLM_TASK_SCOPE_TRIAGE]
        )
        base_task_config = load_llm_task_config(task_config_path)
        task_config = apply_llm_profile(base_task_config, llm_profile)
        input_path = batch_dir / "scope_triage_input.jsonl"
        results_path = batch_dir / "scope_triage_results.jsonl"
        usage_summary_path = batch_dir / "scope_triage_usage_summary.json"
        output_path = batch_dir / "units.scope_triaged.jsonl"
        apply_summary_path = batch_dir / "scope_triage_apply_summary.json"
        job_dir = self.root / "data" / "llm" / "jobs" / f"{batch_name}_scope_triage"

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
                    "blocking_reason": (
                        f"LLM {execution_mode} job is {job_summary.status}; "
                        "rerun ./next to poll or resume."
                    ),
                    "artifacts": self._llm_running_artifacts(
                        prefix="scope_triage",
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
        apply_summary = apply_scope_triage_results_file(
            units_jsonl=batch_dir / "units.alphabetic.jsonl",
            results_jsonl=results_path,
            output_jsonl=output_path,
            summary_json=apply_summary_path,
        )
        return {
            "artifacts": {
                **self._llm_completed_artifacts(
                    prefix="scope_triage",
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
                "units_scope_triaged_jsonl": str(output_path),
                "scope_triage_keep": str(apply_summary.keep),
                "scope_triage_skip": str(apply_summary.skip),
                "scope_triage_provisional_alphabetic_skip": str(
                    apply_summary.provisional_alphabetic_skip
                ),
                "scope_triage_parse_error_keep": str(apply_summary.parse_error_keep),
                "scope_triage_missing_result_keep": str(apply_summary.missing_result_keep),
            }
        }

    def _queue_yomi_llm_reading(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        input_path = batch_dir / "units.yomi.auto_accept.jsonl"
        safety_path = batch_dir / "units.yomi.safety_pre_llm.jsonl"
        safety_summary_path = batch_dir / "yomi_safety_pre_llm_summary.json"
        output_path = batch_dir / "yomi_reading_input.jsonl"
        summary_path = batch_dir / "yomi_reading_queue_summary.json"
        safety_summary = apply_yomi_safety_pre_llm_file(
            input_jsonl=input_path,
            output_jsonl=safety_path,
            summary_json=safety_summary_path,
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
                "yomi_safety_pre_llm_unit_auto_accept": str(
                    getattr(safety_summary, "unit_auto_accept_safe", 0)
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
                    "blocking_reason": (
                        f"LLM {execution_mode} job is {job_summary.status}; "
                        "rerun ./next to poll or resume."
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
                        "blocking_reason": (
                            f"LLM {execution_mode} retry attempt {attempt} is {retry_job_summary.status}; "
                            "rerun ./next to poll or resume."
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
        from yomi_corpus.review_site import publish_review_site

        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        source_path = batch_dir / "units.yomi.llm_readings.jsonl"
        pack_id = f"yomi_final_{batch_name}_v1"
        batch_pack_path = batch_dir / "final_review_pack.json"
        summary_path = batch_dir / "final_review_pack_summary.json"
        review_pack_path = self.root / "data" / "review_packs" / "yomi_final" / f"{pack_id}.json"
        document_state_path = self.document_review_state_path(batch_name)
        document_state = build_initial_document_review_state(
            units_jsonl=source_path,
            batch_name=batch_name,
            track_name=batch_state.track_name,
        )
        write_document_review_state(document_state_path, document_state)
        summary = build_yomi_final_review_pack_file(
            units_jsonl=source_path,
            output_json=batch_pack_path,
            pack_id=pack_id,
            track_name=batch_state.track_name,
            batch_name=batch_name,
            document_state_json=document_state_path,
        )
        review_pack_path.parent.mkdir(parents=True, exist_ok=True)
        review_pack_path.write_text(
            batch_pack_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        summary = replace(summary, latest_json=str(review_pack_path))
        write_yomi_final_review_summary(summary, summary_path)
        manifest = publish_review_site(
            web_review_dir=self.root / "web" / "review",
            docs_dir=self.root / "docs",
            review_pack_root=self.root / "data" / "review_packs",
        )
        manifest_path = self.root / "docs" / "review" / "manifest.json"
        return {
            "artifacts": {
                "final_review_pack_json": str(batch_pack_path),
                "final_review_pack_summary_json": str(summary_path),
                "document_review_state_json": str(document_state_path),
                "document_review_state_documents": str(
                    document_state["summary"]["document_count"]
                ),
                "document_review_state_final_pending": str(
                    document_state["summary"]["state_counts"].get("final_pending", 0)
                ),
                "review_pack_json": str(review_pack_path),
                "review_site_manifest_json": str(manifest_path),
                "review_site_url": "https://hiroshi-manabe.github.io/yomi-corpus/",
                "final_review_stage": summary.review_stage,
                "final_review_pack_id": summary.pack_id,
                "final_review_items": str(summary.item_count),
                "final_review_unresolved_items": str(summary.unresolved_item_count),
                "final_review_unresolved_targets": str(summary.unresolved_target_count),
                "final_review_provisional_skip_items": str(
                    summary.provisional_skip_item_count
                ),
                "review_site_default_stage": str(manifest.get("default_stage") or ""),
            }
        }

    def _apply_final_review(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        pack_id = str(batch_state.artifacts.get("final_review_pack_id") or f"yomi_final_{batch_name}_v1")
        pack_path = batch_dir / "final_review_pack.json"
        output_path = batch_dir / "units.yomi.reviewed.jsonl"
        summary_path = batch_dir / "final_review_apply_summary.json"
        submission_store_dir = self.root / "data" / "review_submissions" / "yomi_final"
        import_summary = self._import_final_review_submissions(submission_store_dir)
        summary = apply_final_review_file(
            units_jsonl=batch_dir / "units.yomi.llm_readings.jsonl",
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
                    units_jsonl=batch_dir / "units.yomi.llm_readings.jsonl",
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
            if existing_apply_summary.get("confirmed"):
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
                    "blocking_reason": (
                        f"LLM {execution_mode} job is {job_summary.status}; "
                        "rerun ./next to poll or resume."
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
        apply_summary = apply_yomi_strong_repair_results_file(
            units_jsonl=batch_dir / "units.yomi.reviewed.jsonl",
            queue_jsonl=input_path,
            results_jsonl=results_path,
            output_jsonl=output_path,
            summary_json=apply_summary_path,
        )
        review_pack_artifacts: dict[str, str] = {}
        if queued_count:
            review_pack_artifacts = self._prepare_strong_repair_review_pack(
                batch_name,
                queue_jsonl=input_path,
                results_jsonl=results_path,
                units_jsonl=output_path,
            )
        completed_artifacts = self._llm_completed_artifacts(
            prefix="yomi_strong_repair",
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
        )
        artifacts = {
            **completed_artifacts,
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
        from yomi_corpus.review_site import publish_review_site

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
        )
        write_yomi_final_review_summary(summary, summary_path)
        manifest = publish_review_site(
            web_review_dir=self.root / "web" / "review",
            docs_dir=self.root / "docs",
            review_pack_root=self.root / "data" / "review_packs",
        )
        return {
            "yomi_strong_repair_review_pack_json": str(batch_pack_path),
            "yomi_strong_repair_review_pack_summary_json": str(summary_path),
            "yomi_strong_repair_review_pack_id": summary.pack_id,
            "yomi_strong_repair_review_items": str(summary.item_count),
            "yomi_strong_repair_review_site_manifest_json": str(
                self.root / "docs" / "review" / "manifest.json"
            ),
            "yomi_strong_repair_review_site_url": "https://hiroshi-manabe.github.io/yomi-corpus/",
            "review_site_default_stage": str(manifest.get("default_stage") or ""),
        }

    def _finalize_yomi(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        batch_state = self.load_batch_state(batch_name)
        output_path = batch_dir / "units.yomi.final.jsonl"
        summary_path = batch_dir / "yomi_finalize_summary.json"
        strong_repaired_path = batch_dir / "units.yomi.strong_repaired.jsonl"
        strong_review_pack = batch_dir / "yomi_strong_repair_review_pack.json"
        strong_review_summary_path = batch_dir / "yomi_strong_repair_review_apply_summary.json"
        strong_submission_store_dir = self.root / "data" / "review_submissions" / "yomi_strong_repair"
        strong_import_artifacts: dict[str, str] = {}
        if strong_review_pack.exists():
            import_summary = self._import_strong_repair_review_submissions(
                strong_submission_store_dir
            )
            strong_import_artifacts = {
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
                        "yomi_strong_repair_review_submission_store": str(
                            strong_submission_store_dir
                        ),
                        **strong_import_artifacts,
                        "human_review_required": "true",
                        "human_review_gate": "yomi_strong_repair_review",
                        "human_review_item_count": str(strong_review_summary.get("item_count", "")),
                    },
                }
        summary = finalize_reviewed_yomi_file(
            units_jsonl=strong_repaired_path if strong_repaired_path.exists() else batch_dir / "units.yomi.reviewed.jsonl",
            strong_queue_summary_json=batch_dir / "yomi_strong_repair_queue_summary.json",
            strong_apply_summary_json=batch_dir / "yomi_strong_repair_apply_summary.json",
            output_jsonl=output_path,
            summary_json=summary_path,
        )
        artifacts = {
            "units_yomi_final_jsonl": str(output_path),
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
        harvest_summary_path = batch_dir / "yomi_finalization_harvest_summary.json"
        harvest_summary = harvest_yomi_finalization_artifacts_file(
            final_units_jsonl=output_path,
            batch_manual_rewrites_jsonl=batch_dir / "manual_yomi_rewrites.jsonl",
            batch_supplemental_furigana_tsv=batch_dir / "supplemental_furigana.tsv",
            global_manual_rewrites_jsonl=self.root / "data" / "lexicon" / "manual_yomi_rewrites.jsonl",
            global_supplemental_furigana_tsv=self.root / "data" / "lexicon" / "supplemental_furigana.tsv",
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
            f"{prefix}_llm_remote_status": job_summary.remote_status or "",
            f"{prefix}_llm_remote_batch_id": job_summary.remote_batch_id or "",
            f"{prefix}_llm_job_completed": str(job_summary.completed_items),
            f"{prefix}_llm_job_failed": str(job_summary.failed_items),
            f"{prefix}_llm_job_total": str(job_summary.total_items),
            f"{prefix}_prompt_template": str(task_config.prompt_template),
            f"{prefix}_queued": str(queued_count),
        }

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
