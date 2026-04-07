from __future__ import annotations

from pathlib import Path
import tomllib

from yomi_corpus.llm.schemas import LLMTaskConfig
from yomi_corpus.paths import resolve_repo_path


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


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
