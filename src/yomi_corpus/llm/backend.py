from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yomi_corpus.llm.credentials import resolve_openai_api_key
from yomi_corpus.llm.parsers import parse_output
from yomi_corpus.llm.schemas import LLMResult, LLMTaskConfig, PromptItem
from yomi_corpus.llm.usage import usage_from_batch_item, usage_from_response


class OpenAIResponsesBackend:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_file: str | Path | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The openai package is required for sync execution. "
                "Install it in the environment before running sync mode."
            ) from exc
        resolved_api_key, api_key_source = resolve_openai_api_key(
            api_key=api_key,
            api_key_file=api_key_file,
        )
        self.api_key_source = api_key_source
        self._client = OpenAI(api_key=resolved_api_key) if resolved_api_key else OpenAI()

    def run_sync(self, task_config: LLMTaskConfig, items: list[PromptItem]) -> list[LLMResult]:
        results: list[LLMResult] = []
        for item in items:
            response = self._client.responses.create(**build_response_create_kwargs(task_config, item.prompt))
            raw_text = _extract_output_text(response)
            parsed = None
            parse_error = None
            try:
                parsed = parse_output(raw_text, task_config.parser)
            except Exception as exc:  # noqa: BLE001
                parse_error = str(exc)
            results.append(
                LLMResult(
                    item_id=item.item_id,
                    raw_text=raw_text,
                    parsed=parsed,
                    parse_error=parse_error,
                    usage=usage_from_response(response),
                    metadata=item.metadata,
                )
            )
        return results

    def submit_batch(
        self,
        requests_jsonl_path: str | Path,
        *,
        endpoint: str,
        completion_window: str,
    ) -> dict[str, Any]:
        path = Path(requests_jsonl_path)
        with path.open("rb") as handle:
            uploaded_file = self._client.files.create(file=handle, purpose="batch")
        submission = self._client.batches.create(
            input_file_id=uploaded_file.id,
            endpoint=endpoint,
            completion_window=completion_window,
        )
        return {
            "input_file_id": uploaded_file.id,
            "batch_id": submission.id,
            "status": getattr(submission, "status", "submitted"),
            "created_at": getattr(submission, "created_at", None),
            "output_file_id": getattr(submission, "output_file_id", None),
            "error_file_id": getattr(submission, "error_file_id", None),
        }

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self._client.batches.retrieve(batch_id)
        request_counts = getattr(batch, "request_counts", None)
        return {
            "batch_id": batch.id,
            "status": getattr(batch, "status", None),
            "created_at": getattr(batch, "created_at", None),
            "in_progress_at": getattr(batch, "in_progress_at", None),
            "completed_at": getattr(batch, "completed_at", None),
            "expires_at": getattr(batch, "expires_at", None),
            "expired_at": getattr(batch, "expired_at", None),
            "failed_at": getattr(batch, "failed_at", None),
            "finalizing_at": getattr(batch, "finalizing_at", None),
            "output_file_id": getattr(batch, "output_file_id", None),
            "error_file_id": getattr(batch, "error_file_id", None),
            "request_counts": _object_to_dict(request_counts),
            "usage": _object_to_dict(getattr(batch, "usage", None)),
        }

    def download_file(self, file_id: str, output_path: str | Path) -> None:
        self._client.files.content(file_id).write_to_file(Path(output_path))


def write_batch_requests(
    task_config: LLMTaskConfig,
    items: list[PromptItem],
    output_path: str | Path,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            request = {
                "custom_id": item.item_id,
                "method": "POST",
                "url": task_config.batch_endpoint,
                "body": build_response_create_kwargs(task_config, item.prompt),
            }
            handle.write(json.dumps(request, ensure_ascii=False) + "\n")


def build_response_create_kwargs(task_config: LLMTaskConfig, prompt: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": task_config.model,
        "input": [{"role": "user", "content": prompt}],
        "max_output_tokens": task_config.max_output_tokens,
    }
    if task_config.model.startswith("gpt-5"):
        if task_config.verbosity:
            kwargs["text"] = {"verbosity": task_config.verbosity}
        if task_config.reasoning_effort:
            kwargs["reasoning"] = {"effort": task_config.reasoning_effort}
    return kwargs


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        for block in reversed(output):
            if getattr(block, "type", None) != "message":
                continue
            for content in getattr(block, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text:
                    return text
    raise ValueError("Could not extract output text from Responses API result.")


def extract_output_text_from_batch_item(item: dict[str, Any]) -> str | None:
    response = item.get("response") or {}
    body = response.get("body") or {}

    for container in (response, body):
        output_text = container.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

    for container in (body, response):
        output = container.get("output")
        if not isinstance(output, list):
            continue
        for block in reversed(output):
            if block.get("type") != "message":
                continue
            for content in block.get("content") or []:
                text = content.get("text")
                if isinstance(text, str) and text:
                    return text
    return None


def extract_usage_from_batch_item(item: dict[str, Any]) -> dict[str, int] | None:
    return usage_from_batch_item(item)


def _object_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value
