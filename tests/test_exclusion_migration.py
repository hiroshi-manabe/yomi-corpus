from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.exclusion_migration import migrate_terminal_exclusion


class TerminalExclusionMigrationTests(unittest.TestCase):
    def test_dry_run_then_idempotent_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "data" / "units" / "dev_batch_0002"
            packs = root / "data" / "review_packs" / "yomi_final"
            evals = root / "data" / "evals" / "scope_triage"
            batch.mkdir(parents=True)
            packs.mkdir(parents=True)
            evals.mkdir(parents=True)
            final_path = batch / "units.yomi.final.jsonl"
            skipped_path = batch / "units.yomi.skipped.jsonl"
            final_path.write_text(
                encode_rows(
                    [
                        unit("sensitive:u0001", 13, "sensitive text 1"),
                        unit("sensitive:u0002", 13, "sensitive text 2"),
                        unit("sensitive:u0004", 13, "retained text"),
                        unit("safe:u0001", 14, "safe text"),
                    ]
                ),
                encoding="utf-8",
            )
            skipped_path.write_text(
                encode_rows([unit("sensitive:u0003", 13, "sensitive skipped text")]),
                encoding="utf-8",
            )
            pack_path = packs / "pack.json"
            pack_path.write_text(
                json.dumps(
                    {
                        "item_count": 2,
                        "items": [
                            {"item_id": "sensitive:u0001", "text": "sensitive text 1"},
                            {"item_id": "safe:u0001", "text": "safe text"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            eval_path = evals / "gold.jsonl"
            eval_path.write_text(
                encode_rows(
                    [
                        {"unit_id": "sensitive:u0002", "text": "sensitive text 2"},
                        {"unit_id": "safe:u0001", "text": "safe text"},
                    ]
                ),
                encoding="utf-8",
            )
            before = final_path.read_text(encoding="utf-8")

            dry_run = migrate_terminal_exclusion(
                root=root,
                track_name="dev",
                track_doc_seq=13,
                unit_ids={"sensitive:u0001", "sensitive:u0002", "sensitive:u0003"},
                reason_category="sensitive_content",
                confirmation_submission_id="admin-doc-13",
                apply=False,
                report_json=root / "dry-run.json",
            )

            self.assertFalse(dry_run["applied"])
            self.assertEqual(dry_run["source_unit_count"], 3)
            self.assertEqual(final_path.read_text(encoding="utf-8"), before)

            applied = migrate_terminal_exclusion(
                root=root,
                track_name="dev",
                track_doc_seq=13,
                unit_ids={"sensitive:u0001", "sensitive:u0002", "sensitive:u0003"},
                reason_category="sensitive_content",
                confirmation_submission_id="admin-doc-13",
                apply=True,
                report_json=root / "apply.json",
                backup_root=root / "backups",
            )

            self.assertTrue(applied["applied"])
            self.assertEqual(
                [row["unit_id"] for row in load_rows(final_path)],
                ["sensitive:u0004", "safe:u0001"],
            )
            self.assertEqual(load_rows(skipped_path), [])
            tombstones = load_rows(batch / "units.yomi.excluded.jsonl")
            self.assertEqual(len(tombstones), 3)
            self.assertEqual({row["unit_id"] for row in tombstones}, {
                "sensitive:u0001", "sensitive:u0002", "sensitive:u0003"
            })
            self.assertTrue(all(row["excluded"] for row in tombstones))
            self.assertTrue(all("text" not in row and "analysis" not in row for row in tombstones))
            self.assertEqual(json.loads(pack_path.read_text())["item_count"], 1)
            self.assertEqual([row["unit_id"] for row in load_rows(eval_path)], ["safe:u0001"])

            rerun = migrate_terminal_exclusion(
                root=root,
                track_name="dev",
                track_doc_seq=13,
                unit_ids={"sensitive:u0001", "sensitive:u0002", "sensitive:u0003"},
                reason_category="sensitive_content",
                confirmation_submission_id="admin-doc-13",
                apply=True,
                report_json=root / "rerun.json",
                backup_root=root / "backups-rerun",
            )

            self.assertTrue(rerun["applied"])
            self.assertEqual(rerun["source_unit_count"], 0)
            self.assertEqual(rerun["existing_tombstone_count"], 3)
            self.assertEqual(rerun["changed_paths"], [])
            self.assertEqual(len(load_rows(batch / "units.yomi.excluded.jsonl")), 3)


def unit(unit_id: str, track_doc_seq: int, text: str) -> dict:
    doc_id = unit_id.split(":u", 1)[0]
    return {
        "doc_id": doc_id,
        "track_doc_seq": track_doc_seq,
        "unit_id": unit_id,
        "unit_seq": int(unit_id.rsplit("u", 1)[1]),
        "text": text,
        "analysis": {"mechanical": {"yomi": {"rendered": "機密/キミツ"}}},
    }


def encode_rows(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


if __name__ == "__main__":
    unittest.main()
