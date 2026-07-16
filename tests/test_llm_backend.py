from __future__ import annotations

import unittest
from types import SimpleNamespace

from yomi_corpus.llm.backend import tool_calls_from_batch_item, tool_calls_from_response


class LLMBackendToolCallTests(unittest.TestCase):
    def test_counts_response_output_tool_calls_by_type(self) -> None:
        response = SimpleNamespace(
            output=[
                SimpleNamespace(type="web_search_call"),
                SimpleNamespace(type="message"),
                SimpleNamespace(type="web_search_call"),
                SimpleNamespace(type="function_call"),
            ]
        )
        self.assertEqual(
            tool_calls_from_response(response),
            {"web_search_call": 2, "function_call": 1},
        )

    def test_counts_batch_response_body_tool_calls(self) -> None:
        item = {
            "response": {
                "body": {
                    "output": [
                        {"type": "web_search_call"},
                        {"type": "message"},
                    ]
                }
            }
        }
        self.assertEqual(tool_calls_from_batch_item(item), {"web_search_call": 1})

    def test_returns_empty_counts_when_no_tool_was_called(self) -> None:
        self.assertEqual(tool_calls_from_response(SimpleNamespace(output=[])), {})


if __name__ == "__main__":
    unittest.main()
