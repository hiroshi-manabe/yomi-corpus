from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tomllib

from yomi_corpus.llm.schemas import LLMTaskConfig
from yomi_corpus.paths import resolve_repo_path

DEFAULT_LLM_PROFILES_CONFIG_PATH = "config/llm/profiles.toml"


def load_llm_task_config(path: str | Path) -> LLMTaskConfig:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    return LLMTaskConfig(
        task_name=str(payload["task_name"]),
        input_builder=str(payload["input_builder"]),
        parser=str(payload["parser"]),
        mode=str(payload.get("mode", "sync")),
        model=str(payload["model"]),
        prompt_template=str(payload["prompt_template"]),
        reasoning_effort=_optional_str(payload.get("reasoning_effort")),
        verbosity=_optional_str(payload.get("verbosity")),
        max_output_tokens=int(payload.get("max_output_tokens", 512)),
        batch_endpoint=str(payload.get("batch_endpoint", "/v1/responses")),
        batch_completion_window=str(payload.get("batch_completion_window", "24h")),
    )


def load_llm_profile(
    profile_name: str,
    *,
    profiles_config_path: str | Path = DEFAULT_LLM_PROFILES_CONFIG_PATH,
) -> dict[str, str]:
    config_path = resolve_repo_path(str(profiles_config_path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError(f"No LLM profiles found in {profiles_config_path}")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise ValueError(f"Unknown LLM profile: {profile_name}")

    normalized: dict[str, str] = {}
    for key in ["model", "reasoning_effort", "verbosity"]:
        value = _optional_str(profile.get(key))
        if value is not None:
            normalized[key] = value
    if "model" not in normalized:
        raise ValueError(f"LLM profile {profile_name} must define model")
    return normalized


def apply_llm_profile(
    task_config: LLMTaskConfig,
    profile_name: str,
    *,
    profiles_config_path: str | Path = DEFAULT_LLM_PROFILES_CONFIG_PATH,
) -> LLMTaskConfig:
    profile = load_llm_profile(profile_name, profiles_config_path=profiles_config_path)
    return replace(
        task_config,
        model=profile["model"],
        reasoning_effort=profile.get("reasoning_effort", task_config.reasoning_effort),
        verbosity=profile.get("verbosity", task_config.verbosity),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
