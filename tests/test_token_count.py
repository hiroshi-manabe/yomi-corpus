from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.token_count import count_task_prompt_tokens, count_text_tokens


class FakeEncoding:
    def encode(self, text: str) -> list[str]:
        return list(text)


class TokenCountTests(unittest.TestCase):
    @patch("yomi_corpus.llm.token_count.load_token_encoding", return_value=FakeEncoding())
    def test_count_text_tokens_uses_encoding(self, mock_load_encoding) -> None:
        self.assertEqual(count_text_tokens("abc"), 3)
        mock_load_encoding.assert_called_once()

    @patch("yomi_corpus.llm.token_count.load_token_encoding", return_value=FakeEncoding())
    def test_count_task_prompt_tokens_counts_rendered_prompt(self, mock_load_encoding) -> None:
        rows = count_task_prompt_tokens(
            "config/llm/alphabetic_entity_judge.toml",
            "data/units/batch_0001/alphabetic_unresolved_entities.jsonl",
        )
        self.assertTrue(rows)
        self.assertIn("item_id", rows[0])
        self.assertIn("token_count", rows[0])
        self.assertGreater(rows[0]["token_count"], 0)
        mock_load_encoding.assert_called_once()
