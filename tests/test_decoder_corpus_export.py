from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.yomi.decoder_corpus import export_decoder_corpus_file, parse_rendered_pairs


def test_parse_rendered_pairs_keeps_empty_reading() -> None:
    assert list(parse_rendered_pairs("2021/ 年/ネン 。/。")) == [
        ("2021", ""),
        ("年", "ネン"),
        ("。", "。"),
    ]


def test_export_decoder_corpus_file_writes_minimal_raw_suw(tmp_path: Path) -> None:
    input_jsonl = tmp_path / "units.yomi.final.jsonl"
    output_txt = tmp_path / "decoder_corpus.txt"
    manifest_json = tmp_path / "decoder_corpus.txt.manifest.json"
    rows = [
        {
            "unit_id": "u1",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "近々/チカヂカ です/デス 。/。",
                    }
                }
            },
        },
        {"unit_id": "u2", "analysis": {"mechanical": {"yomi": {}}}},
    ]
    input_jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary = export_decoder_corpus_file(
        input_jsonl=input_jsonl,
        output_txt=output_txt,
        manifest_json=manifest_json,
        source_name="dev_batch_test",
    )

    assert summary.read_units == 2
    assert summary.written_units == 1
    assert summary.written_tokens == 3
    assert output_txt.read_text(encoding="utf-8") == (
        "近々\t*\t近々\t近々\tチカヂカ\n"
        "です\t*\tです\tです\tデス\n"
        "。\t*\t。\t。\t。\n"
        "EOS\n"
    )
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    assert manifest["source_name"] == "dev_batch_test"
    assert manifest["format"] == "raw_suw_yomi_minimal_v1"

