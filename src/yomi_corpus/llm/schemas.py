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
    batch_max_requests_per_batch: int = 50000
    rendered_yomi_display: str = "full"
    include_source_text: bool = True
    text_format: str | None = None
    enable_web_search: bool = False
    web_search_context_size: str | None = None


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
