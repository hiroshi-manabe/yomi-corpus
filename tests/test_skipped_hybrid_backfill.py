from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.skipped_backfill import backfill_skipped_hybrid_yomi


def test_backfill_generates_and_archives_finalized_scope_skip_idempotently() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_dir = root / "data" / "pipeline" / "batches"
        batch_dir = root / "data" / "units" / "dev_batch_0001"
        state_dir.mkdir(parents=True)
        batch_dir.mkdir(parents=True)
        (state_dir / "dev_batch_0001.json").write_text(
            json.dumps(
                {
                    "batch_name": "dev_batch_0001",
                    "current_stage": "yomi_finalized",
                    "artifacts": {"decoder_model_dir": "/models/old"},
                }
            ),
            encoding="utf-8",
        )
        scope_rows = [
            scope_unit("u1", "学校です。", "Keep"),
            scope_unit("u2", "DVです。", "Skip"),
        ]
        write_jsonl(batch_dir / "units.scope_triaged.jsonl", scope_rows)
        write_jsonl(
            batch_dir / "units.yomi.aligned_hybrid.jsonl",
            [with_yomi(scope_rows[0], "学校/ガッコウ です/デス 。/。")],
        )
        (batch_dir / "units.yomi.final.jsonl").write_text("", encoding="utf-8")
        config = YomiGenerationConfig(
            sudachi_command="sudachi",
            sudachi_args=(),
            decoder_python="python",
            decoder_script="decode.py",
            decoder_config="decoder.toml",
            decoder_beam=1,
            decoder_nbest=1,
            default_strategy="aligned_hybrid_v1",
        )

        with patch(
            "yomi_corpus.yomi.skipped_backfill.load_yomi_generation_config",
            return_value=config,
        ):
            first = backfill_skipped_hybrid_yomi(
                root=root,
                apply=True,
                generator=fake_generator,
            )
            second = backfill_skipped_hybrid_yomi(
                root=root,
                apply=True,
                generator=fake_generator,
            )

        assert first["generated_hybrid_count"] == 1
        assert first["archived_skip_count"] == 1
        assert first["failure_count"] == 0
        assert second["generated_hybrid_count"] == 0
        assert second["archived_skip_count"] == 0
        aligned = load_jsonl(batch_dir / "units.yomi.aligned_hybrid.jsonl")
        skipped = load_jsonl(batch_dir / "units.yomi.skipped.jsonl")
        assert [row["unit_id"] for row in aligned] == ["u1", "u2"]
        assert [row["unit_id"] for row in skipped] == ["u2"]
        assert skipped[0]["analysis"]["mechanical"]["yomi"]["rendered"] == "DV/ディーブイ です/デス 。/。"
        assert skipped[0]["analysis"]["human_review"]["yomi_final"]["skip"]
        assert (root / "data" / "state" / "migrations" / "skipped_hybrid_yomi_v1" / "manifest.json").exists()


def test_backfill_recovers_effective_human_skip_from_reviewed_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_dir = root / "data" / "pipeline" / "batches"
        batch_dir = root / "data" / "units" / "dev_batch_0001"
        pack_dir = root / "data" / "review_packs" / "yomi_final"
        submission_dir = root / "data" / "review_submissions" / "yomi_final"
        state_dir.mkdir(parents=True)
        batch_dir.mkdir(parents=True)
        pack_dir.mkdir(parents=True)
        submission_dir.mkdir(parents=True)
        (state_dir / "dev_batch_0001.json").write_text(
            json.dumps(
                {
                    "batch_name": "dev_batch_0001",
                    "current_stage": "yomi_finalized",
                }
            ),
            encoding="utf-8",
        )
        source = scope_unit("u1", "事件です。", "Keep")
        aligned = with_yomi(source, "事件/ジケン です/デス 。/。")
        reviewed = with_yomi(source, "事件/ジケン です/デス 。/。")
        reviewed["analysis"]["human_review"]["yomi_final"] = {
            "reviewed": True,
            "skip": True,
            "submission_id": "submission-2",
            "generated_at_epoch": 2,
        }
        write_jsonl(batch_dir / "units.scope_triaged.jsonl", [source])
        write_jsonl(batch_dir / "units.yomi.aligned_hybrid.jsonl", [aligned])
        write_jsonl(batch_dir / "units.yomi.reviewed.jsonl", [reviewed])
        (batch_dir / "units.yomi.final.jsonl").write_text("", encoding="utf-8")
        pack = {
            "pack_id": "yomi_final_dev_batch_0001_v1",
            "items": [
                {
                    "item_id": "u1",
                    "seq": 1,
                    "skip_default": False,
                    "targets": [],
                }
            ],
        }
        (pack_dir / "yomi_final_dev_batch_0001_v1.json").write_text(
            json.dumps(pack), encoding="utf-8"
        )
        for epoch, skip in [(1, True), (2, True)]:
            submission = {
                "review_stage": "yomi_final_review",
                "pack_id": pack["pack_id"],
                "submission_id": f"submission-{epoch}",
                "generated_at_epoch": epoch,
                "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                "overrides": [{"item_id": "u1", "skip": skip}],
            }
            (submission_dir / f"submission-{epoch}.json").write_text(
                json.dumps(submission), encoding="utf-8"
            )
        config = YomiGenerationConfig(
            sudachi_command="sudachi",
            sudachi_args=(),
            decoder_python="python",
            decoder_script="decode.py",
            decoder_config="decoder.toml",
            decoder_beam=1,
            decoder_nbest=1,
            default_strategy="aligned_hybrid_v1",
        )

        with patch(
            "yomi_corpus.yomi.skipped_backfill.load_yomi_generation_config",
            return_value=config,
        ):
            summary = backfill_skipped_hybrid_yomi(root=root, apply=True)

        assert summary["human_review_skip_count"] == 1
        assert summary["archived_human_skip_count"] == 1
        skipped = load_jsonl(batch_dir / "units.yomi.skipped.jsonl")
        assert [row["unit_id"] for row in skipped] == ["u1"]
        review = skipped[0]["analysis"]["human_review"]["yomi_final"]
        assert review["submission_id"] == "submission-2"
        assert review["skip"] is True


def scope_unit(unit_id: str, text: str, status: str) -> dict:
    return {
        "unit_id": unit_id,
        "doc_id": "doc1",
        "unit_seq": 1 if unit_id == "u1" else 2,
        "track_doc_seq": 1,
        "text": text,
        "analysis": {
            "mechanical": {},
            "llm": {"scope_triage": {"status": status}},
            "human_review": {},
        },
    }


def with_yomi(row: dict, rendered: str) -> dict:
    value = json.loads(json.dumps(row))
    value["analysis"]["mechanical"]["yomi"] = {
        "rendered": rendered,
        "certain": False,
        "sudachi": {},
        "ngram_decoder": {},
        "post_hybrid_repairs": {},
        "signals": [],
    }
    return value


def fake_generator(text: str, **_: object) -> MechanicalYomi:
    assert text == "DVです。"
    return MechanicalYomi(
        rendered="DV/ディーブイ です/デス 。/。",
        certain=False,
        sudachi={},
        ngram_decoder={},
        post_hybrid_repairs={},
        signals=[],
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
