import json

import pytest

from yomi_corpus.yomi.final_review import (
    apply_strong_repair_dispositions_file,
    replay_simple_accept_reject_submissions,
)


@pytest.mark.parametrize("disposition", ["Keep", "Skip", "Exclude"])
def test_disposition_replay_and_application(tmp_path, disposition):
    pack = {"items": [{"item_id": "u::strong_repair", "unit_id": "u", "seq": 1}]}
    submission = {
        "submission_id": "s",
        "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
        "overrides": [{"item_id": "u::strong_repair", "disposition": disposition}],
    }
    effective = replay_simple_accept_reject_submissions(pack, [submission])
    path = tmp_path / "units.jsonl"
    path.write_text(json.dumps({"unit_id": "u", "text": "original"}) + "\n")
    apply_strong_repair_dispositions_file(pack, effective, path)
    unit = json.loads(path.read_text())
    review = unit["analysis"]["human_review"]["yomi_final"]
    assert review["disposition"] == disposition
    assert review["skip"] == (disposition != "Keep")
    assert review["submission_id"] == "s"
    assert unit["text"] == "original"


def test_invalid_disposition_rejected():
    pack = {"items": [{"item_id": "u", "seq": 1}]}
    with pytest.raises(ValueError, match="disposition"):
        replay_simple_accept_reject_submissions(pack, [{
            "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
            "overrides": [{"item_id": "u", "disposition": "invalid"}],
        }])
