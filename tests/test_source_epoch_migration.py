from __future__ import annotations

from pathlib import Path

from yomi_corpus.source_epoch_migration import (
    build_prefix_mapping,
    replace_string_prefix,
    rewrite_identity,
)


def _row(stable_id: str, line: int) -> dict[str, object]:
    return {"source_record_id": stable_id, "source_line_no": line, "text": stable_id}


def test_prefix_mapping_carries_by_stable_identity_and_keeps_corrected_order() -> None:
    result = build_prefix_mapping(
        [_row("a", 1), _row("drop", 2), _row("b", 3)],
        [_row("a", 1), _row("new", 2), _row("b", 3)],
        old_dataset_name="old",
        new_dataset_name="new",
    )

    assert result["counts"] == {"old": 3, "new": 3, "carried": 2, "removed": 1, "incoming": 1}
    assert [row["disposition"] for row in result["mapping"]] == ["carried", "incoming", "carried"]
    assert result["mapping"][2]["old_doc_id"] == "old:0000000003"
    assert result["mapping"][2]["new_doc_id"] == "new:0000000003"
    assert result["removed"][0]["source_record_id"] == "drop"


def test_identity_rewrite_updates_structural_references_only() -> None:
    original = {
        "doc_id": "old:0000000003",
        "unit_id": "old:0000000003:u0002",
        "track_doc_seq": 3,
        "source_line_no": 3,
        "dataset_name": "old",
        "dataset_source_path": "/old.jsonl.gz",
        "analysis": {
            "item_id": "old:0000000003:u0002:r0001c01",
            "submission_id": "historical-submission",
        },
    }
    rewritten = rewrite_identity(
        original,
        old_doc_id="old:0000000003",
        new_doc_id="new:0000000004",
        new_track_doc_seq=4,
        new_source_line_no=4,
        new_dataset_name="new",
        new_source_path=Path("/new.jsonl.gz"),
    )

    assert rewritten["doc_id"] == "new:0000000004"
    assert rewritten["unit_id"] == "new:0000000004:u0002"
    assert rewritten["track_doc_seq"] == 4
    assert rewritten["analysis"]["item_id"] == "new:0000000004:u0002:r0001c01"
    assert rewritten["analysis"]["submission_id"] == "historical-submission"


def test_recovery_unit_identity_can_be_rebased_to_canonical_unit_identity() -> None:
    value = {
        "unit_id": "recovery:campaign:source:hash",
        "analysis": {"item_id": "recovery:campaign:source:hash:r0001c01"},
    }
    rewritten = replace_string_prefix(
        value,
        "recovery:campaign:source:hash",
        "new:0000000004:u0002",
    )
    assert rewritten["unit_id"] == "new:0000000004:u0002"
    assert rewritten["analysis"]["item_id"] == "new:0000000004:u0002:r0001c01"
