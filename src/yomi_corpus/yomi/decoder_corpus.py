from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class DecoderCorpusExportSummary:
    read_units: int
    written_units: int
    written_tokens: int
    output_txt: str
    manifest_json: str


def export_decoder_corpus_file(
    *,
    input_jsonl: Path,
    output_txt: Path,
    manifest_json: Path,
    source_name: str,
) -> DecoderCorpusExportSummary:
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    manifest_json.parent.mkdir(parents=True, exist_ok=True)
    read_units = 0
    written_units = 0
    written_tokens = 0
    with input_jsonl.open(encoding="utf-8") as src, output_txt.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            rendered = (
                unit.get("analysis", {})
                .get("mechanical", {})
                .get("yomi", {})
                .get("rendered")
            )
            if not isinstance(rendered, str) or not rendered.strip():
                continue
            pairs = list(parse_rendered_pairs(rendered))
            if not pairs:
                continue
            for surface, reading in pairs:
                dst.write(render_raw_token_line(surface, reading))
                written_tokens += 1
            dst.write("EOS\n")
            written_units += 1
    summary = DecoderCorpusExportSummary(
        read_units=read_units,
        written_units=written_units,
        written_tokens=written_tokens,
        output_txt=str(output_txt),
        manifest_json=str(manifest_json),
    )
    manifest = {
        **asdict(summary),
        "source_name": source_name,
        "format": "raw_suw_yomi_minimal_v1",
        "columns": ["surface", "pos", "dictionary_form", "normalized_form", "reading"],
    }
    manifest_json.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_rendered_pairs(rendered: str) -> Iterator[tuple[str, str]]:
    for token in rendered.split():
        if "/" not in token:
            yield token, ""
            continue
        surface, reading = token.rsplit("/", 1)
        yield surface, reading


def render_raw_token_line(surface: str, reading: str) -> str:
    return "\t".join([surface, "*", surface, surface, reading]) + "\n"
