import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from yomi_corpus.yomi.adapters import run_decoder_many
from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.runtime import generate_mechanical_yomi_many
from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.export import export_jsonl_yomi


def config():
    return YomiGenerationConfig("sudachi", (), "python", "decode.py", "config.toml", 150, 5, "aligned_hybrid_v1")


def test_decoder_batch_uses_one_process_and_preserves_order():
    output = "\n".join(json.dumps({"text": text, "results": []}) for text in ("甲", "乙"))
    with patch("yomi_corpus.yomi.adapters.subprocess.run", return_value=SimpleNamespace(stdout=output)) as run:
        assert run_decoder_many(["甲", "乙"], config(), source_texts=["甲", "乙"]) == [[], []]
    run.assert_called_once()
    assert run.call_args.kwargs["input"] == "甲\n乙\n"
    assert "--text" not in run.call_args.args[0]


@pytest.mark.parametrize("output", [
    '{"text":"甲","results":[]}',
    '{"text":"乙","results":[]}\n{"text":"甲","results":[]}',
    'invalid\ninvalid',
])
def test_decoder_batch_rejects_missing_reordered_or_malformed_results(output):
    with patch("yomi_corpus.yomi.adapters.subprocess.run", return_value=SimpleNamespace(stdout=output)):
        with pytest.raises(ValueError):
            run_decoder_many(["甲", "乙"], config(), source_texts=["甲", "乙"])


def test_multiline_inputs_use_single_text_path():
    with patch("yomi_corpus.yomi.runtime.generate_mechanical_yomi", return_value="single") as single:
        with patch("yomi_corpus.yomi.runtime.run_decoder_many") as batch:
            assert generate_mechanical_yomi_many(["甲\n乙"], config=config()) == ["single"]
    single.assert_called_once()
    batch.assert_not_called()


def test_empty_batch_does_not_launch_processes():
    with patch("yomi_corpus.yomi.adapters.subprocess.run") as run:
        assert generate_mechanical_yomi_many([], config=config()) == []
    run.assert_not_called()


def test_export_chunks_preserve_row_order_and_partial_last_chunk(tmp_path):
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    rows = [{"unit_id": str(i), "text": str(i), "analysis": {"mechanical": {}}} for i in range(5)]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with patch("yomi_corpus.yomi.export.generate_mechanical_yomi_many") as many:
        many.side_effect = lambda texts, **kwargs: [MechanicalYomi(rendered=text, certain=True) for text in texts]
        summary = export_jsonl_yomi(input_jsonl=source, output_jsonl=output,
                                   config=replace(config(), generation_batch_size=2), strategy_name=None)
    assert [call.args[0] for call in many.call_args_list] == [["0", "1"], ["2", "3"], ["4"]]
    assert summary["written"] == 5
    assert [json.loads(line)["unit_id"] for line in output.read_text().splitlines()] == list(map(str, range(5)))
