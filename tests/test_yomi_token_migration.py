from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.token_migration import migrate_finalized_yomi_tokens


class YomiTokenMigrationTests(unittest.TestCase):
    def test_migration_preserves_fullwidth_space_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "A　B",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "A/エー 　/　 B/ビー"},
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            report = migrate_finalized_yomi_tokens(
                root=root,
                apply=True,
                report_json=root / "report.json",
                backup_root=root / "backup",
            )

            self.assertEqual(report["anomaly_count"], 0)
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["analysis"]["mechanical"]["yomi"]["tokens"],
                [["A", "エー"], ["　", "　"], ["B", "ビー"]],
            )

    def test_migration_normalizes_symbol_readings_to_literal_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "1～2",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "1/ ～/カラ 2/"},
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            report = migrate_finalized_yomi_tokens(
                root=root,
                apply=True,
                report_json=root / "report.json",
                backup_root=root / "backup",
            )

            self.assertEqual(report["anomaly_count"], 0)
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["analysis"]["mechanical"]["yomi"]["tokens"],
                [["1", ""], ["～", "～"], ["2", ""]],
            )

    def test_migration_recovers_invalid_latin_reading_from_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "II",
                        "analysis": {
                            "mechanical": {"yomi": {"rendered": "II/II"}},
                            "human_review": {
                                "yomi_final": {
                                    "target_overrides": [
                                        {
                                            "surface": "II",
                                            "selected_reading": "つー",
                                        }
                                    ]
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            report = migrate_finalized_yomi_tokens(
                root=root,
                apply=True,
                report_json=root / "report.json",
                backup_root=root / "backup",
            )

            self.assertEqual(report["anomaly_count"], 0)
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["analysis"]["mechanical"]["yomi"]["tokens"],
                [["II", "ツー"]],
            )

    def test_apply_backs_up_and_migrates_finalized_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_path = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            final_path.parent.mkdir(parents=True)
            original = {
                "unit_id": "u1",
                "text": "3/22",
                "analysis": {
                    "mechanical": {
                        "yomi": {"rendered": "3/ /// 22/"},
                    }
                },
            }
            final_path.write_text(json.dumps(original, ensure_ascii=False) + "\n", encoding="utf-8")
            report_path = root / "report.json"
            backup_root = root / "backups"

            report = migrate_finalized_yomi_tokens(
                root=root,
                apply=True,
                report_json=report_path,
                backup_root=backup_root,
            )

            self.assertTrue(report["applied"])
            self.assertEqual(report["anomaly_count"], 0)
            migrated = json.loads(final_path.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["analysis"]["mechanical"]["yomi"],
                {
                    "token_schema_version": 1,
                    "tokens": [["3", ""], ["/", "/"], ["22", ""]],
                },
            )
            backup = backup_root / final_path.relative_to(root)
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)

            second = migrate_finalized_yomi_tokens(
                root=root,
                apply=False,
                report_json=root / "second-report.json",
            )
            self.assertEqual(second["changed_unit_count"], 0)

    def test_anomaly_prevents_all_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "data" / "units" / "dev_batch_0001" / "units.yomi.final.jsonl"
            bad = root / "data" / "units" / "dev_batch_0002" / "units.yomi.final.jsonl"
            good.parent.mkdir(parents=True)
            bad.parent.mkdir(parents=True)
            good_text = json.dumps(
                {
                    "unit_id": "good",
                    "text": "今日",
                    "analysis": {"mechanical": {"yomi": {"rendered": "今日/キョウ"}}},
                },
                ensure_ascii=False,
            ) + "\n"
            good.write_text(good_text, encoding="utf-8")
            bad.write_text(
                json.dumps(
                    {
                        "unit_id": "bad",
                        "text": "違う",
                        "analysis": {"mechanical": {"yomi": {"rendered": "別/ベツ"}}},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            report = migrate_finalized_yomi_tokens(
                root=root,
                apply=True,
                report_json=root / "report.json",
                backup_root=root / "backups",
            )

            self.assertFalse(report["applied"])
            self.assertEqual(report["anomaly_count"], 1)
            self.assertEqual(good.read_text(encoding="utf-8"), good_text)


if __name__ == "__main__":
    unittest.main()
