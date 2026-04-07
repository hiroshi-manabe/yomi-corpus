from __future__ import annotations

from typing import Any

from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows

DEFAULT_ENCODING_NAME = "o200k_base"


def load_token_encoding(encoding_name: str = DEFAULT_ENCODING_NAME) -> Any:
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError(
            "The tiktoken package is required for local token counting. "
            "Install it with `pip install .[llm]` or `pip install tiktoken`."
        ) from exc
    return tiktoken.get_encoding(encoding_name)


def count_text_tokens(text: str, encoding_name: str = DEFAULT_ENCODING_NAME) -> int:
    encoding = load_token_encoding(encoding_name)
    return len(encoding.encode(text))


def count_task_prompt_tokens(
    task_config_path: str,
    input_jsonl_path: str,
    *,
    encoding_name: str = DEFAULT_ENCODING_NAME,
) -> list[dict[str, Any]]:
    task_config = load_llm_task_config(task_config_path)
    rows = load_jsonl_rows(input_jsonl_path)
    items = build_prompt_items(task_config, rows)
    encoding = load_token_encoding(encoding_name)

    results: list[dict[str, Any]] = []
    for item in items:
        token_count = len(encoding.encode(item.prompt))
        results.append(
            {
                "item_id": item.item_id,
                "token_count": token_count,
                "character_count": len(item.prompt),
                "metadata": item.metadata,
            }
        )
    return results
