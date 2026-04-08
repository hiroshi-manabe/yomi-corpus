from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.export import (
    available_export_variant_names,
    export_jsonl_yomi,
    export_plaintext_yomi,
    export_named_variant,
    resolve_export_variant,
)


class YomiExportTests(unittest.TestCase):
    def test_available_export_variant_names(self) -> None:
        self.assertEqual(available_export_variant_names(), ["aligned_hybrid", "sudachi_only"])

    def test_resolve_export_variant(self) -> None:
        variant = resolve_export_variant("aligned_hybrid")
        self.assertEqual(variant.output_txt_filename, "units.yomi.aligned_hybrid.txt")
        self.assertEqual(variant.output_jsonl_filename, "units.yomi.aligned_hybrid.jsonl")
        self.assertEqual(variant.strategy_name, "aligned_hybrid_v1")

    def test_export_jsonl_yomi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "units.jsonl"
            output_path = root / "units.yomi.jsonl"
            rows = [
                {"unit_id": "u1", "text": "A", "analysis": {"mechanical": {"yomi": {}}}},
                {"unit_id": "u2", "text": "B", "analysis": {"mechanical": {"yomi": {}}}},
            ]
            with input_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

            config = YomiGenerationConfig(
                sudachi_command="sudachi",
                sudachi_args=(),
                decoder_python="python",
                decoder_script="decode.py",
                decoder_config="config.toml",
                decoder_beam=10,
                decoder_nbest=5,
                decoder_original_segments=True,
                default_strategy="aligned_hybrid_v1",
            )

            with patch("yomi_corpus.yomi.export.generate_mechanical_yomi") as mocked:
                mocked.side_effect = [
                    MechanicalYomi(rendered="A/エー", certain=True, signals=["x"]),
                    MechanicalYomi(rendered="B/ビー", certain=False, signals=["y"]),
                ]
                summary = export_jsonl_yomi(
                    input_jsonl=input_path,
                    output_jsonl=output_path,
                    config=config,
                    strategy_name="aligned_hybrid_v1",
                )

            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(summary["written"], 2)
            self.assertEqual(summary["last_unit_id"], "u2")
            self.assertEqual(rows[0]["analysis"]["mechanical"]["yomi"]["rendered"], "A/エー")
            self.assertEqual(rows[1]["analysis"]["mechanical"]["yomi"]["certain"], False)

    def test_export_plaintext_yomi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "units.jsonl"
            output_path = root / "units.yomi.txt"
            rows = [
                {"unit_id": "u1", "text": "A"},
                {"unit_id": "u2", "text": "B"},
            ]
            with input_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

            config = YomiGenerationConfig(
                sudachi_command="sudachi",
                sudachi_args=(),
                decoder_python="python",
                decoder_script="decode.py",
                decoder_config="config.toml",
                decoder_beam=10,
                decoder_nbest=5,
                decoder_original_segments=True,
                default_strategy="aligned_hybrid_v1",
            )

            with patch("yomi_corpus.yomi.export.generate_mechanical_yomi") as mocked:
                mocked.side_effect = [
                    MechanicalYomi(rendered="A/エー", certain=True),
                    MechanicalYomi(rendered="B/ビー", certain=True),
                ]
                summary = export_plaintext_yomi(
                    input_jsonl=input_path,
                    output_txt=output_path,
                    config=config,
                    strategy_name="aligned_hybrid_v1",
                )

            self.assertEqual(summary["written"], 2)
            self.assertEqual(summary["last_unit_id"], "u2")
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "u1\tA/エー\nu2\tB/ビー\n",
            )

    def test_export_named_variant_respects_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "batch"
            batch_dir.mkdir()
            (batch_dir / "units.jsonl").write_text(
                json.dumps({"unit_id": "u1", "text": "A", "analysis": {"mechanical": {"yomi": {}}}}) + "\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[sudachi]",
                        'command = "sudachi"',
                        "args = []",
                        "",
                        "[decoder]",
                        'python = "python"',
                        'script = "decode.py"',
                        'config = "decoder.toml"',
                        "beam = 10",
                        "nbest = 5",
                        "original_segments = true",
                        "",
                        "[strategy]",
                        'default = "aligned_hybrid_v1"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("yomi_corpus.yomi.export.generate_mechanical_yomi") as mocked:
                mocked.return_value = MechanicalYomi(rendered="A/エー", certain=True)
                summary = export_named_variant(
                    variant_name="aligned_hybrid",
                    batch_dir=batch_dir,
                    config_path=config_path,
                    formats=["jsonl", "txt"],
                )

            self.assertIn("jsonl", summary)
            self.assertIn("txt", summary)
            self.assertTrue((batch_dir / "units.yomi.aligned_hybrid.jsonl").exists())
            self.assertTrue((batch_dir / "units.yomi.aligned_hybrid.txt").exists())


if __name__ == "__main__":
    unittest.main()
