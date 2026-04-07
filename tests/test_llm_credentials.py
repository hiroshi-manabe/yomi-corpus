from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.credentials import resolve_openai_api_key


class LLMCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = PROJECT_ROOT / "tests" / "tmp_credentials"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.api_key_file = self.tmp_root / "openai.txt"
        self.original_openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.original_openai_api_key_file = os.environ.get("OPENAI_API_KEY_FILE")
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY_FILE", None)

    def tearDown(self) -> None:
        if self.original_openai_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.original_openai_api_key

        if self.original_openai_api_key_file is None:
            os.environ.pop("OPENAI_API_KEY_FILE", None)
        else:
            os.environ["OPENAI_API_KEY_FILE"] = self.original_openai_api_key_file

        if self.tmp_root.exists():
            for path in sorted(self.tmp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()

    def test_explicit_key_wins(self) -> None:
        self.api_key_file.write_text("file-key\n", encoding="utf-8")
        os.environ["OPENAI_API_KEY"] = "env-key"
        resolved, source = resolve_openai_api_key(api_key="explicit-key", api_key_file=self.api_key_file)
        self.assertEqual(resolved, "explicit-key")
        self.assertEqual(source, "explicit")

    def test_environment_variable_wins_over_file(self) -> None:
        self.api_key_file.write_text("file-key\n", encoding="utf-8")
        os.environ["OPENAI_API_KEY"] = "env-key"
        resolved, source = resolve_openai_api_key(api_key_file=self.api_key_file)
        self.assertEqual(resolved, "env-key")
        self.assertEqual(source, "env:OPENAI_API_KEY")

    def test_api_key_file_flag_is_used(self) -> None:
        self.api_key_file.write_text("file-key\n", encoding="utf-8")
        resolved, source = resolve_openai_api_key(api_key_file=self.api_key_file)
        self.assertEqual(resolved, "file-key")
        self.assertEqual(source, "flag:api_key_file")

    def test_openai_api_key_file_environment_variable_is_used(self) -> None:
        self.api_key_file.write_text("file-key\n", encoding="utf-8")
        os.environ["OPENAI_API_KEY_FILE"] = str(self.api_key_file)
        resolved, source = resolve_openai_api_key()
        self.assertEqual(resolved, "file-key")
        self.assertEqual(source, "env:OPENAI_API_KEY_FILE")
