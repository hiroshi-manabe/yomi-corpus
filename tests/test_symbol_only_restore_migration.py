from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.symbol_only_restore_migration import (
    MIGRATION_ID,
    migrate_symbol_only_skips,
)


class SymbolOnlyRestoreMigrationTests(unittest.TestCase):
    def test_dry_run_apply_and_rerun_are_safe_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "data" / "units" / "dev_batch_0001"
            batch.mkdir(parents=True)
            final_path = batch / "units.yomi.final.jsonl"
            skipped_path = batch / "units.yomi.skipped.jsonl"
            final_path.write_text(
                encode_rows([unit("doc:u0001", 1, "本文", "本文/ホンブン", skip=False)]),
                encoding="utf-8",
            )
            skipped_path.write_text(
                encode_rows(
                    [
                        unit("doc:u0002", 2, "？", "？/？", skip=True),
                        unit("doc:u0003", 3, "ABC", "ABC/エービーシー", skip=True),
                    ]
                ),
                encoding="utf-8",
            )
            before_final = final_path.read_text(encoding="utf-8")
            before_skipped = skipped_path.read_text(encoding="utf-8")

            dry_run = migrate_symbol_only_skips(
                root=root,
                track_name="dev",
                apply=False,
                report_json=root / "dry-run.json",
            )

            self.assertEqual(dry_run["restorable_count"], 1)
            self.assertEqual(dry_run["anomaly_count"], 0)
            self.assertFalse(dry_run["applied"])
            self.assertEqual(final_path.read_text(encoding="utf-8"), before_final)
            self.assertEqual(skipped_path.read_text(encoding="utf-8"), before_skipped)

            applied = migrate_symbol_only_skips(
                root=root,
                track_name="dev",
                apply=True,
                report_json=root / "apply.json",
                backup_root=root / "backups",
            )

            self.assertTrue(applied["applied"])
            self.assertEqual([row["unit_id"] for row in load_rows(final_path)], ["doc:u0001", "doc:u0002"])
            self.assertEqual([row["unit_id"] for row in load_rows(skipped_path)], ["doc:u0003"])
            restored = load_rows(final_path)[1]
            review = restored["analysis"]["human_review"]["yomi_final"]
            self.assertFalse(review["skip"])
            self.assertEqual(review["disposition"], "Keep")
            self.assertEqual(review["restoration_submission_id"], MIGRATION_ID)
            self.assertNotIn("finalized_corrections", restored["analysis"]["human_review"])
            self.assertEqual(
                restored["analysis"]["human_review"]["skip_history"][0]["event"],
                "restored",
            )

            rerun = migrate_symbol_only_skips(
                root=root,
                track_name="dev",
                apply=True,
                report_json=root / "rerun.json",
                backup_root=root / "rerun-backups",
            )

            self.assertTrue(rerun["applied"])
            self.assertEqual(rerun["restorable_count"], 0)
            self.assertEqual(rerun["changed_paths"], [])


def unit(unit_id: str, unit_seq: int, text: str, rendered: str, *, skip: bool) -> dict:
    return {
        "doc_id": "doc",
        "track_doc_seq": 1,
        "unit_id": unit_id,
        "unit_seq": unit_seq,
        "text": text,
        "analysis": {
            "mechanical": {"yomi": {"rendered": rendered}},
            "human_review": {
                "yomi_final": {
                    "reviewed": True,
                    "skip": skip,
                    "submission_id": "original-review",
                }
            },
        },
    }


def encode_rows(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


if __name__ == "__main__":
    unittest.main()
