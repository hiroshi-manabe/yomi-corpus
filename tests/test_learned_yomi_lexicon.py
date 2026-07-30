from __future__ import annotations

import json
from pathlib import Path

import pytest

from yomi_corpus.yomi.final_review import (
    append_nonconflicting_exact_rewrites,
    harvest_learned_yomi_readings,
    harvest_manual_yomi_rewrites,
)
from yomi_corpus.yomi.learned_lexicon import (
    apply_exact_yomi_rewrites,
    load_exact_yomi_rewrites,
)
from yomi_corpus.yomi.learned_lexicon_migration import consolidate_exact_rewrites


def test_exact_rewrite_replaces_matching_token_span(tmp_path: Path) -> None:
    path = tmp_path / "rewrites.jsonl"
    path.write_text(
        json.dumps(
            {
                "original_surface": "池尻中学校",
                "replacement": [
                    {"surface": "池尻", "reading": "イケジリ"},
                    {"surface": "中学校", "reading": "チュウガッコウ"},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = apply_exact_yomi_rewrites(
        "旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ で/デ",
        rewrites_path=path,
    )

    assert result.rendered == "旧/キュウ 池尻/イケジリ 中学校/チュウガッコウ で/デ"
    assert len(result.applications) == 1


def test_segmentation_only_rewrite_preserves_current_reading(tmp_path: Path) -> None:
    path = tmp_path / "rewrites.jsonl"
    path.write_text(
        json.dumps(
            {
                "original_surface": "貢船",
                "replacement": [{"surface": "貢船", "reading": None}],
                "reading_mode": "preserve_current",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = apply_exact_yomi_rewrites(
        "貢/ミツギ 船/ブネ が/ガ",
        rewrites_path=path,
    )

    assert result.rendered == "貢船/ミツギブネ が/ガ"


def test_exact_rewrite_loader_rejects_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "rewrites.jsonl"
    rows = [
        {"original_surface": "一日", "replacement": [{"surface": "一日", "reading": "イチニチ"}]},
        {"original_surface": "一日", "replacement": [{"surface": "一日", "reading": "ツイタチ"}]},
    ]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="Conflicting exact yomi rewrites"):
        load_exact_yomi_rewrites(path)


def test_reading_only_repair_becomes_candidate_not_exact_default() -> None:
    units = [strong_repaired_unit(replacement=[{"surface": "一日", "reading": "イチニチ"}])]
    queue = {
        "u1::target_group:1": {
            "item_id": "u1::target_group:1",
            "rendered_yomi": "一日/ツイタチ です/デス 。/。",
        }
    }

    rewrites = harvest_manual_yomi_rewrites(
        units,
        batch_name="dev_batch_0001",
        track_name="dev",
        queue_by_item_id=queue,
    )
    readings = harvest_learned_yomi_readings(
        units,
        batch_name="dev_batch_0001",
        track_name="dev",
    )

    assert rewrites == []
    assert [(row["surface"], row["reading"]) for row in readings] == [("一日", "イチニチ")]


def test_boundary_change_becomes_exact_default() -> None:
    units = [
        strong_repaired_unit(
            replacement=[
                {"surface": "池尻", "reading": "イケジリ"},
                {"surface": "中学校", "reading": "チュウガッコウ"},
            ],
            rejected_span="池尻中学校",
        )
    ]
    queue = {
        "u1::target_group:1": {
            "item_id": "u1::target_group:1",
            "rendered_yomi": "池尻中/イケジリナカ 学校/ガッコウ 。/。",
        }
    }

    rewrites = harvest_manual_yomi_rewrites(
        units,
        batch_name="dev_batch_0001",
        track_name="dev",
        queue_by_item_id=queue,
    )

    assert [row["replacement_rendered"] for row in rewrites] == [
        "池尻/イケジリ 中学校/チュウガッコウ"
    ]


def test_whitespace_spanning_repair_is_not_learned() -> None:
    units = [
        strong_repaired_unit(
            rejected_span="The last",
            replacement=[
                {"surface": "The", "reading": "ザ"},
                {"surface": " ", "reading": " "},
                {"surface": "last", "reading": "ラスト"},
            ],
        )
    ]
    queue = {
        "u1::target_group:1": {
            "item_id": "u1::target_group:1",
            "rendered_yomi": "The/ザ  / last/ラスト",
        }
    }

    assert harvest_manual_yomi_rewrites(
        units,
        batch_name="dev_batch_0001",
        track_name="dev",
        queue_by_item_id=queue,
    ) == []
    assert [
        (row["surface"], row["reading"])
        for row in harvest_learned_yomi_readings(
            units,
            batch_name="dev_batch_0001",
            track_name="dev",
        )
    ] == [("The", "ザ"), ("last", "ラスト")]


def test_reading_conflict_preserves_shared_segmentation() -> None:
    rows = [
        rewrite_evidence("櫻丘", "櫻丘/オウキュウ", "u1"),
        rewrite_evidence("櫻丘", "櫻丘/サクラオカ", "u2"),
    ]

    consolidated, conflicts = consolidate_exact_rewrites(rows)

    assert conflicts == []
    assert consolidated[0]["reading_mode"] == "preserve_current"
    assert consolidated[0]["replacement"] == [{"surface": "櫻丘", "reading": None}]
    assert consolidated[0]["reading_variants"] == [["オウキュウ"], ["サクラオカ"]]


def test_boundary_conflict_is_reported_and_omitted() -> None:
    rows = [
        rewrite_evidence("３日", "３日/ミッカ", "u1"),
        {
            **rewrite_evidence("３日", "３/ 日/ニチ", "u2"),
            "replacement": [
                {"surface": "３", "reading": ""},
                {"surface": "日", "reading": "ニチ"},
            ],
        },
    ]

    consolidated, conflicts = consolidate_exact_rewrites(rows)

    assert consolidated == []
    assert conflicts[0]["original_surface"] == "３日"
    assert {tuple(row["replacement_surfaces"]) for row in conflicts[0]["variants"]} == {
        ("３日",),
        ("３", "日"),
    }


def test_incremental_reading_conflict_converts_default_to_segmentation_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rewrites.jsonl"
    first = rewrite_evidence("貢船", "貢船/コウセン", "u1")
    second = rewrite_evidence("貢船", "貢船/ミツギブネ", "u2")
    path.write_text(json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8")

    appended, conflicts = append_nonconflicting_exact_rewrites(path, [second])
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert appended == 0
    assert conflicts == []
    assert saved["reading_mode"] == "preserve_current"
    assert saved["replacement"] == [{"surface": "貢船", "reading": None}]


def strong_repaired_unit(
    *,
    replacement: list[dict[str, str]],
    rejected_span: str = "一日",
) -> dict:
    return {
        "unit_id": "u1",
        "analysis": {
            "llm": {
                "yomi_strong_repair": {
                    "repairs": [
                        {
                            "item_id": "u1::target_group:1",
                            "status": "applied",
                            "rejected_span": rejected_span,
                            "replacement": replacement,
                        }
                    ]
                }
            }
        },
    }


def rewrite_evidence(surface: str, rendered: str, unit_id: str) -> dict:
    reading = rendered.rsplit("/", 1)[1]
    return {
        "original_surface": surface,
        "replacement_rendered": rendered,
        "replacement": [{"surface": surface, "reading": reading}],
        "source": "llm_strong_repair",
        "source_batch": "dev_batch_0001",
        "source_track": "dev",
        "source_unit_id": unit_id,
        "source_item_id": f"{unit_id}::target_group:1",
    }
