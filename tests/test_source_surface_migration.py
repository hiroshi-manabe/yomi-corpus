from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.source_surface_migration import migrate_source_surfaces


class SourceSurfaceMigrationTests(unittest.TestCase):
    def test_migration_restores_spaces_collapses_empty_tokens_and_clears_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            path.parent.mkdir(parents=True)
            row = {
                "unit_id": "u1",
                "text": "A ⑴B",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "tokens": [["A", "エー"], [" ", "\u00a0"], ["⑴", "イチ"], ["B", "ビー"]],
                            "sudachi": {
                                "tokens": [
                                    sudachi_token("A", "エー"),
                                    sudachi_token("\u00a0", "キゴウ", pos="空白"),
                                    sudachi_token("⑴", "キゴウ", pos="補助記号,括弧開"),
                                    sudachi_token("", "イチ", pos="名詞,数詞"),
                                    sudachi_token("", "キゴウ", pos="補助記号,括弧閉"),
                                    sudachi_token("B", "ビー"),
                                ]
                            },
                            "ngram_decoder": {
                                "candidates": [
                                    {
                                        "rank": 1,
                                        "entries": [
                                            {"surface": "A"},
                                            {"surface": "\u00a0"},
                                            {"surface": "⑴"},
                                            {"surface": "B"},
                                        ],
                                    }
                                ]
                            },
                        }
                    }
                },
            }
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

            report = migrate_source_surfaces(
                root=root,
                apply=True,
                report_json=root / "report.json",
                backup_root=root / "backup",
            )

            self.assertTrue(report["applied"])
            self.assertEqual(report["empty_sudachi_token_count"], 2)
            migrated = json.loads(path.read_text(encoding="utf-8"))
            yomi = migrated["analysis"]["mechanical"]["yomi"]
            self.assertEqual(yomi["tokens"][1], [" ", ""])
            self.assertEqual(
                [token["surface"] for token in yomi["sudachi"]["tokens"]],
                ["A", " ", "⑴", "B"],
            )
            self.assertEqual(
                [entry["surface"] for entry in yomi["ngram_decoder"]["candidates"][0]["entries"]],
                ["A", " ", "⑴", "B"],
            )

    def test_anomaly_prevents_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            path.parent.mkdir(parents=True)
            original = {
                "unit_id": "u1",
                "text": "AB",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "tokens": [["A", "エー"], ["B", "ビー"]],
                            "sudachi": {"tokens": [sudachi_token("AC", "エーシー")]},
                        }
                    }
                },
            }
            serialized = json.dumps(original, ensure_ascii=False) + "\n"
            path.write_text(serialized, encoding="utf-8")

            report = migrate_source_surfaces(
                root=root,
                apply=True,
                report_json=root / "report.json",
                backup_root=root / "backup",
            )

            self.assertFalse(report["applied"])
            self.assertEqual(report["anomaly_count"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), serialized)


def sudachi_token(surface: str, reading: str, *, pos: str = "名詞") -> dict[str, str]:
    return {
        "surface": surface,
        "pos": pos,
        "dictionary_form": surface,
        "normalized_form": surface,
        "reading": reading,
    }


if __name__ == "__main__":
    unittest.main()
