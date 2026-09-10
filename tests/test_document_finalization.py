import json
from copy import deepcopy

import pytest

from yomi_corpus.document_finalization import (
    MANIFEST,
    OUTPUTS,
    finalize_ready_documents,
    read_rows,
    close_finalized_documents,
)
from yomi_corpus.document_review_state import (
    build_initial_document_review_state,
    write_document_review_state,
)
from yomi_corpus.review_site import collect_finalized_archive_documents


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


@pytest.fixture
def batch(tmp_path):
    directory = tmp_path / "data/units/batch"
    directory.mkdir(parents=True)
    rows = [
        {
            "doc_id": f"d{index}",
            "unit_id": f"u{index}",
            "track_doc_seq": index,
            "unit_seq": 1,
            "text": "猫",
            "analysis": {
                "mechanical": {"yomi": {"tokens": [["猫", "ネコ"]]}},
                "human_review": {
                    "yomi_final": {"reviewed": True, "disposition": "Keep"}
                },
            },
        }
        for index in (1, 2)
    ]
    write_rows(directory / "units.jsonl", rows)
    write_rows(directory / "units.yomi.reviewed.jsonl", rows)
    write_rows(directory / "yomi_strong_repair_queue.jsonl", [{"doc_id": "d2"}])
    state = build_initial_document_review_state(
        units_jsonl=directory / "units.jsonl", batch_name="batch", track_name="dev"
    )
    state["documents"][0]["state"] = "complete"
    state["documents"][1]["state"] = "strong_pending"
    state_path = directory / "document_review_state.json"
    write_document_review_state(state_path, state)
    pipeline = tmp_path / "data/pipeline/batches"
    pipeline.mkdir(parents=True)
    (pipeline / "batch.json").write_text(
        json.dumps(
            {
                "batch_name": "batch",
                "track_name": "dev",
                "current_stage": "yomi_strong_repair_llm_completed",
            }
        )
    )
    return directory, state_path, rows, state


def test_independent_finalization_and_archive(batch, tmp_path):
    directory, state_path, rows, state = batch
    result = finalize_ready_documents(directory, state_path)
    assert result == {"finalized_documents": ["d1"], "failures": {}}
    assert [row["doc_id"] for row in read_rows(directory / OUTPUTS[0])] == ["d1"]
    assert [
        doc["doc_id"] for doc in collect_finalized_archive_documents(tmp_path, "dev")
    ] == ["d1"]
    assert not close_finalized_documents(
        directory, state_path, directory / "summary.json"
    )["stage_complete"]
    assert finalize_ready_documents(directory, state_path)["finalized_documents"] == []


def test_preserves_later_corrections_when_other_document_finishes(batch):
    directory, state_path, rows, state = batch
    finalize_ready_documents(directory, state_path)
    committed = read_rows(directory / OUTPUTS[0])
    committed[0]["correction_test_marker"] = "authoritative"
    write_rows(directory / OUTPUTS[0], committed)
    repaired = deepcopy(rows)
    repaired[1]["analysis"]["mechanical"]["yomi"]["tokens"] = [["猫", "ビョウ"]]
    repaired[1]["analysis"]["human_review"] = {}
    write_rows(directory / "units.yomi.strong_repaired.jsonl", repaired)
    state["documents"][1]["state"] = "strong_reviewed"
    write_document_review_state(state_path, state)
    assert finalize_ready_documents(directory, state_path)["finalized_documents"] == [
        "d2"
    ]
    final = read_rows(directory / OUTPUTS[0])
    assert final[0]["correction_test_marker"] == "authoritative"
    assert final[1]["analysis"]["mechanical"]["yomi"]["tokens"] == [["猫", "ビョウ"]]
    summary = close_finalized_documents(
        directory, state_path, directory / "summary.json"
    )
    assert summary["written_units"] == 2
    assert read_rows(directory / OUTPUTS[0]) == final


@pytest.mark.parametrize("disposition,index", [("Skip", 1), ("Exclude", 2)])
def test_dispositions_preserved(batch, disposition, index):
    directory, state_path, rows, _ = batch
    rows[0]["analysis"]["human_review"]["yomi_final"]["disposition"] = disposition
    write_rows(directory / "units.yomi.reviewed.jsonl", rows)
    assert finalize_ready_documents(directory, state_path)["finalized_documents"] == [
        "d1"
    ]
    assert read_rows(directory / OUTPUTS[0]) == []
    assert len(read_rows(directory / OUTPUTS[index])) == 1


def test_invalid_document_does_not_block_other_document(batch):
    directory, state_path, rows, state = batch
    rows[0]["analysis"]["human_review"]["yomi_final"]["reviewed"] = False
    state["documents"][1]["state"] = "complete"
    write_document_review_state(state_path, state)
    write_rows(directory / "yomi_strong_repair_queue.jsonl", [])
    write_rows(directory / "units.yomi.reviewed.jsonl", rows)
    result = finalize_ready_documents(directory, state_path)
    assert result["finalized_documents"] == ["d2"]
    assert "d1" in result["failures"]


def test_uncommitted_rows_not_published_or_duplicated(batch, tmp_path):
    directory, state_path, rows, _ = batch
    finalize_ready_documents(directory, state_path)
    final = read_rows(directory / OUTPUTS[0])
    write_rows(directory / OUTPUTS[0], final + [rows[1]])
    assert [
        doc["doc_id"] for doc in collect_finalized_archive_documents(tmp_path, "dev")
    ] == ["d1"]
    state = json.loads(state_path.read_text())
    state["documents"][1]["state"] = "strong_reviewed"
    write_document_review_state(state_path, state)
    write_rows(directory / "units.yomi.strong_repaired.jsonl", rows)
    finalize_ready_documents(directory, state_path)
    assert len(read_rows(directory / OUTPUTS[0])) == 2


def test_missing_committed_rows_are_not_silently_overwritten(batch):
    directory, state_path, rows, state = batch
    finalize_ready_documents(directory, state_path)
    (directory / OUTPUTS[0]).unlink()
    with pytest.raises(ValueError, match="Committed document coverage"):
        finalize_ready_documents(directory, state_path)


def test_failed_manifest_commit_is_retried(batch, monkeypatch, tmp_path):
    import yomi_corpus.document_finalization as module

    directory, state_path, _, _ = batch
    original = module.atomic_text

    def fail_manifest(path, text):
        if path.name == MANIFEST:
            raise OSError("interrupted")
        original(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_text", fail_manifest)
        with pytest.raises(OSError, match="interrupted"):
            finalize_ready_documents(directory, state_path)
    assert collect_finalized_archive_documents(tmp_path, "dev") == []
    assert finalize_ready_documents(directory, state_path)["finalized_documents"] == [
        "d1"
    ]
    assert len(read_rows(directory / OUTPUTS[0])) == 1


@pytest.mark.parametrize("failure", ["missing", "duplicate", "invalid"])
def test_document_validation_blocks_commit(batch, failure, monkeypatch):
    directory, state_path, rows, _ = batch
    if failure == "missing":
        rows = rows[1:]
    elif failure == "duplicate":
        rows.append(deepcopy(rows[0]))
    else:
        monkeypatch.setattr(
            'yomi_corpus.yomi.final_review.load_final_review_surface_readings',
            lambda: {},
        )
        rows[0]["analysis"]["mechanical"]["yomi"]["tokens"] = [["猫", "INVALID"]]
    write_rows(directory / "units.yomi.reviewed.jsonl", rows)
    result = finalize_ready_documents(directory, state_path)
    assert result["finalized_documents"] == []
    assert "d1" in result["failures"]
