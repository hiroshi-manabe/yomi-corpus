from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.yomi.canonical_compound_migration import (
    migrate_canonical_compound_tokens,
    prepare_decoder_base_corpus,
)
from yomi_corpus.yomi.final_review import canonicalize_finalized_unit_yomi


def test_migration_joins_minasama_and_minasan_variants_and_is_idempotent(tmp_path: Path) -> None:
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
        "text": "皆様、皆さま、みな様、みなさま、皆さん、みなさんです。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "tokens": [
                        ["皆", "ミナ"],
                        ["様", "サマ"],
                        ["、", "、"],
                        ["皆", "ミナ"],
                        ["さま", "サマ"],
                        ["、", "、"],
                        ["みな", "ミナ"],
                        ["様", "サマ"],
                        ["、", "、"],
                        ["みな", "ミナ"],
                        ["さま", "サマ"],
                        ["、", "、"],
                        ["皆", "ミナ"],
                        ["さん", "サン"],
                        ["、", "、"],
                        ["みな", "ミナ"],
                        ["さん", "サン"],
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
    assert first["merged_occurrence_count"] == 6
    assert second["changed_unit_count"] == 0
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["analysis"]["mechanical"]["yomi"]["tokens"] == [
        ["皆様", "ミナサマ"],
        ["、", "、"],
        ["皆さま", "ミナサマ"],
        ["、", "、"],
        ["みな様", "ミナサマ"],
        ["、", "、"],
        ["みなさま", "ミナサマ"],
        ["、", "、"],
        ["皆さん", "ミナサン"],
        ["、", "、"],
        ["みなさん", "ミナサン"],
        ["です", "デス"],
        ["。", "。"],
    ]
    assert "rendered" not in migrated["analysis"]["mechanical"]["yomi"]


def test_finalization_joins_all_minasa_variants_in_an_active_legacy_row() -> None:
    row = {
        "unit_id": "u1",
        "text": "皆様、皆さま、みな様、みなさま、皆さん、みなさんです。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "tokens": [
                        ["皆", "ミナ"],
                        ["様", "サマ"],
                        ["、", "、"],
                        ["皆", "ミナ"],
                        ["さま", "サマ"],
                        ["、", "、"],
                        ["みな", "ミナ"],
                        ["様", "サマ"],
                        ["、", "、"],
                        ["みな", "ミナ"],
                        ["さま", "サマ"],
                        ["、", "、"],
                        ["皆", "ミナ"],
                        ["さん", "サン"],
                        ["、", "、"],
                        ["みな", "ミナ"],
                        ["さん", "サン"],
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
        ["、", "、"],
        ["皆さま", "ミナサマ"],
        ["、", "、"],
        ["みな様", "ミナサマ"],
        ["、", "、"],
        ["みなさま", "ミナサマ"],
        ["、", "、"],
        ["皆さん", "ミナサン"],
        ["、", "、"],
        ["みなさん", "ミナサン"],
        ["です", "デス"],
        ["。", "。"],
    ]


def test_decoder_base_corpus_migration_joins_exact_sequences(tmp_path: Path) -> None:
    path = tmp_path / "core.txt"
    path.write_text(
        "皆\t名詞-普通名詞-副詞可能\t\t\tミナ\t皆\n"
        "さん\t接尾辞-名詞的-一般\t\t\tサン\tさん\n"
        "。\t補助記号-句点\t\t\t\t。\n"
        "EOS\n"
        "みな\t名詞-普通名詞-副詞可能\t\t\tミナ\t皆\n"
        "様\t接尾辞-名詞的-一般\t\t\tサマ\t様\n"
        "EOS\n",
        encoding="utf-8",
    )

    report, staged = prepare_decoder_base_corpus(path, apply=True)

    assert report["merged_occurrence_count"] == 2
    assert report["anomalies"] == []
    assert staged is not None
    staged.replace(path)
    assert path.read_text(encoding="utf-8") == (
        "皆さん\t名詞-普通名詞-一般\t\t\tミナサン\t皆さん\n"
        "。\t補助記号-句点\t\t\t\t。\n"
        "EOS\n"
        "みな様\t名詞-普通名詞-一般\t\t\tミナサマ\tみな様\n"
        "EOS\n"
    )

    second, staged = prepare_decoder_base_corpus(path, apply=False)
    assert second["merged_occurrence_count"] == 0
    assert staged is None
