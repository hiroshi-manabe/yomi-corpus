from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.yomi.canonical_compound_migration import (
    migrate_canonical_compound_tokens,
)
from yomi_corpus.yomi.final_review import canonicalize_finalized_unit_yomi


def test_migration_joins_minasama_and_is_idempotent(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "data"
        / "units"
        / "dev_batch_0001"
        / "units.yomi.final.jsonl"
    )
    path.parent.mkdir(parents=True)
    row = {
        "unit_id": "u1",
        "text": "皆様です。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "tokens": [
                        ["皆", "ミナ"],
                        ["様", "サマ"],
                        ["です", "デス"],
                        ["。", "。"],
                    ]
                }
            }
        },
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    first = migrate_canonical_compound_tokens(
        root=tmp_path,
        apply=True,
        report_json=report_path,
        backup_root=tmp_path / "backup",
    )
    second = migrate_canonical_compound_tokens(
        root=tmp_path,
        apply=False,
        report_json=tmp_path / "post.json",
    )

    assert first["applied"] is True
    assert first["changed_unit_count"] == 1
    assert first["merged_occurrence_count"] == 1
    assert second["changed_unit_count"] == 0
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["analysis"]["mechanical"]["yomi"]["tokens"] == [
        ["皆様", "ミナサマ"],
        ["です", "デス"],
        ["。", "。"],
    ]
    assert "rendered" not in migrated["analysis"]["mechanical"]["yomi"]


def test_finalization_joins_minasama_in_an_active_legacy_row() -> None:
    row = {
        "unit_id": "u1",
        "text": "皆様です。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "tokens": [
                        ["皆", "ミナ"],
                        ["様", "サマ"],
                        ["です", "デス"],
                        ["。", "。"],
                    ]
                }
            }
        },
    }

    canonicalize_finalized_unit_yomi(row)

    assert row["analysis"]["mechanical"]["yomi"]["tokens"] == [
        ["皆様", "ミナサマ"],
        ["です", "デス"],
        ["。", "。"],
    ]
