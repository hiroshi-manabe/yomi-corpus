from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.recovery_application import apply_recovery_campaign


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _unit(unit_id: str, text: str, tokens: list[list[str]], seq: int) -> dict:
    return {
        "unit_id": unit_id,
        "doc_id": "doc-1",
        "track_doc_seq": 1,
        "unit_seq": seq,
        "char_start": 0,
        "char_end": len(text),
        "text": text,
        "source_file": "source.jsonl.gz",
        "source_line_no": 7,
        "analysis": {
            "mechanical": {"yomi": {"tokens": tokens}},
            "human_review": {"yomi_final": {"item_id": unit_id}},
        },
    }


def test_scatter_back_splits_legacy_unit_and_is_idempotent(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    (campaign_dir / "campaign.json").write_text(
        json.dumps({"campaign_id": "cleaner_v1"}), encoding="utf-8"
    )
    state_dir = tmp_path / "data/pipeline/batches"
    state_dir.mkdir(parents=True)
    (state_dir / "dev_batch_0001.json").write_text(
        json.dumps(
            {
                "batch_name": "dev_batch_0001",
                "track_name": "dev",
                "current_stage": "yomi_finalized",
            }
        ),
        encoding="utf-8",
    )
    batch_dir = tmp_path / "data/units/dev_batch_0001"
    original = _unit(
        "doc-1:u0001",
        "前です。後です。",
        [["前", "マエ"], ["です", "デス"], ["。", "。"], ["後", "アト"], ["です", "デス"], ["。", "。"]],
        1,
    )
    _write_jsonl(batch_dir / "units.jsonl", [original])
    _write_jsonl(batch_dir / "units.yomi.final.jsonl", [original])
    _write_jsonl(batch_dir / "units.yomi.skipped.jsonl", [])

    recovery_batch = tmp_path / "data/units/dev_recovery_cleaner_v1"
    recovery = _unit(
        "recovery:cleaner_v1:source:4:hash",
        "復元です。",
        [["復元", "フクゲン"], ["です", "デス"], ["。", "。"]],
        1,
    )
    recovery["doc_id"] = "recovery-doc"
    _write_jsonl(recovery_batch / "units.yomi.final.jsonl", [recovery])
    ledger_row = {
        "schema_version": 1,
        "campaign_id": "cleaner_v1",
        "recovery_unit_id": recovery["unit_id"],
        "destination_doc_id": "doc-1",
        "destination_track_doc_seq": 1,
        "destination_source_line_no": 7,
        "new_char_start": 4,
        "new_char_end": 9,
        "text": "復元です。",
        "text_sha256": __import__("hashlib").sha256("復元です。".encode()).hexdigest(),
        "preceding_anchor": {"text": "前です。"},
        "following_anchor": {"text": "後です。"},
        "state": "ready_to_apply",
        "final_unit_id": recovery["unit_id"],
        "final_yomi_tokens": [["復元", "フクゲン"], ["です", "デス"], ["。", "。"]],
    }
    _write_jsonl(recovery_batch / "recovery_application_ledger.jsonl", [ledger_row])

    dry_run = apply_recovery_campaign(
        root=tmp_path,
        campaign_dir=campaign_dir,
        recovery_batch_name="dev_recovery_cleaner_v1",
        apply=False,
    )
    assert dry_run["split_legacy_units"] == 1
    assert dry_run["changed"] is False

    applied = apply_recovery_campaign(
        root=tmp_path,
        campaign_dir=campaign_dir,
        recovery_batch_name="dev_recovery_cleaner_v1",
        apply=True,
    )
    assert applied["applied_units"] == 1
    rows = [json.loads(line) for line in (batch_dir / "units.yomi.final.jsonl").read_text().splitlines()]
    assert [row["text"] for row in rows] == ["前です。", "復元です。", "後です。"]
    assert [row["unit_seq"] for row in rows] == [1, 2, 3]

    repeated = apply_recovery_campaign(
        root=tmp_path,
        campaign_dir=campaign_dir,
        recovery_batch_name="dev_recovery_cleaner_v1",
        apply=True,
    )
    assert repeated["status"] == "already_applied"
    assert repeated["changed"] is False
