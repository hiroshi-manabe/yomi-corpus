from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMTaskConfig:
    task_name: str
    input_builder: str
    parser: str
    mode: str
    model: str
    prompt_template: str
    reasoning_effort: str | None
    verbosity: str | None
    max_output_tokens: int
    batch_endpoint: str
    batch_completion_window: str


@dataclass(frozen=True)
class PromptItem:
    item_id: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    item_id: str
    raw_text: str
    parsed: Any
    parse_error: str | None = None
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
