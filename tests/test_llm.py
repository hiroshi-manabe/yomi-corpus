from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.backend import (
    build_response_create_kwargs,
    extract_usage_from_batch_item,
    write_batch_requests,
)
from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.parsers import parse_output
from yomi_corpus.llm.prompts import render_prompt
from yomi_corpus.llm.tasks import build_prompt_items
from yomi_corpus.llm.usage import normalize_usage


class LLMScaffoldingTests(unittest.TestCase):
    def test_load_llm_task_config(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        self.assertEqual(config.task_name, "alphabetic_entity_judge")
        self.assertEqual(config.model, "gpt-5.4")
        self.assertEqual(config.parser, "json_object")

    def test_render_prompt_requires_variables(self) -> None:
        prompt = render_prompt("Hello {name}", {"name": "world"})
        self.assertEqual(prompt, "Hello world")

    def test_build_prompt_items_for_alphabetic_entity(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        rows = [
            {
                "entity_key": "led zeppelin",
                "surface_forms": ["Led Zeppelin"],
                "occurrence_count": 1,
                "unit_count": 1,
                "example_texts": ["Led Zeppelinが好きです。"],
            }
        ]
        items = build_prompt_items(config, rows)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, "led zeppelin")
        self.assertIn("Led Zeppelin", items[0].prompt)

    def test_parse_json_output(self) -> None:
        parsed = parse_output('{"status":"whitelist","confidence":"high","note":"ok"}', "json_object")
        self.assertEqual(parsed["status"], "whitelist")

    def test_build_response_kwargs_for_gpt5(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        kwargs = build_response_create_kwargs(config, "prompt")
        self.assertEqual(kwargs["model"], "gpt-5.4")
        self.assertIn("text", kwargs)
        self.assertIn("reasoning", kwargs)

    def test_write_batch_requests_jsonl(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        rows = [
            {
                "entity_key": "run boys",
                "surface_forms": ["Run Boys"],
                "occurrence_count": 2,
                "unit_count": 2,
                "example_texts": ["Run Boysが出店しました。"],
            }
        ]
        items = build_prompt_items(config, rows)
        output_path = PROJECT_ROOT / "tests" / "tmp_batch_requests.jsonl"
        if output_path.exists():
            output_path.unlink()
        write_batch_requests(config, items, output_path)
        lines = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["custom_id"], "run boys")
        self.assertEqual(lines[0]["url"], "/v1/responses")
        output_path.unlink()

    def test_normalize_usage_supports_responses_shape(self) -> None:
        usage = normalize_usage(
            {
                "input_tokens": 1200,
                "input_tokens_details": {"cached_tokens": 1024},
                "output_tokens": 8,
                "output_tokens_details": {"reasoning_tokens": 2},
                "total_tokens": 1208,
            }
        )
        self.assertEqual(usage["input_tokens"], 1200)
        self.assertEqual(usage["cached_input_tokens"], 1024)
        self.assertEqual(usage["output_tokens"], 8)
        self.assertEqual(usage["reasoning_tokens"], 2)
        self.assertEqual(usage["total_tokens"], 1208)

    def test_extract_usage_from_batch_item(self) -> None:
        usage = extract_usage_from_batch_item(
            {
                "custom_id": "run boys",
                "response": {
                    "body": {
                        "usage": {
                            "input_tokens": 600,
                            "input_tokens_details": {"cached_tokens": 512},
                            "output_tokens": 5,
                            "output_tokens_details": {"reasoning_tokens": 1},
                            "total_tokens": 605,
                        }
                    }
                },
            }
        )
        self.assertEqual(usage["cached_input_tokens"], 512)


if __name__ == "__main__":
    unittest.main()
