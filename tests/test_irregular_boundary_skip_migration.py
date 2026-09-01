from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.yomi.irregular_boundary_skip_migration import (
    migrate_irregular_sentence_boundary_skips,
)


def test_migration_moves_only_irregular_finalized_units_to_skipped(tmp_path: Path) -> None:
    batch_dir = tmp_path / "data" / "units" / "dev_batch_0001"
    batch_dir.mkdir(parents=True)
    rows = [
        finalized_unit("u1", "前の文です. 次の文です。"),
        finalized_unit("u2", "１．項目です。"),
    ]
    (batch_dir / "units.yomi.final.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    backup_root = tmp_path / "backups"

    dry_run = migrate_irregular_sentence_boundary_skips(
        root=tmp_path,
        track_name="dev",
        apply=False,
        report_json=report,
    )
    assert dry_run["candidate_count"] == 1
    assert dry_run["applied_count"] == 0

    applied = migrate_irregular_sentence_boundary_skips(
        root=tmp_path,
        track_name="dev",
        apply=True,
        report_json=report,
        backup_root=backup_root,
    )
    assert applied["applied_count"] == 1
    assert applied["anomaly_count"] == 0
    final_rows = load_jsonl(batch_dir / "units.yomi.final.jsonl")
    skipped_rows = load_jsonl(batch_dir / "units.yomi.skipped.jsonl")
    assert [row["unit_id"] for row in final_rows] == ["u2"]
    assert [row["unit_id"] for row in skipped_rows] == ["u1"]
    assert skipped_rows[0]["analysis"]["human_review"]["yomi_final"]["disposition"] == "Skip"
    assert (backup_root / "dev_batch_0001" / "units.yomi.final.jsonl").exists()


def finalized_unit(unit_id: str, text: str) -> dict:
    return {
        "unit_id": unit_id,
        "unit_seq": 1,
        "doc_id": f"doc-{unit_id}",
        "text": text,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "token_schema_version": 1,
                    "tokens": [[text, "テスト"]],
                    "sudachi": {"tokens": []},
                }
            }
        },
    }


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
