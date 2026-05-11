from __future__ import annotations

import json
import subprocess

from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.types import DecoderCandidate, DecoderEntry, SudachiToken


def run_sudachi(text: str, config: YomiGenerationConfig) -> list[SudachiToken]:
    command = [config.sudachi_command, *config.sudachi_args]
    completed = subprocess.run(
        command,
        input=f"{text}\n",
        text=True,
        capture_output=True,
        check=True,
    )
    return parse_sudachi_output(completed.stdout)


def parse_sudachi_output(stdout: str) -> list[SudachiToken]:
    tokens: list[SudachiToken] = []
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip("\n")
        if not line or line == "EOS":
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        tokens.append(
            SudachiToken(
                surface=parts[0],
                pos=parts[1],
                dictionary_form=parts[2],
                normalized_form=parts[3],
                reading=parts[4],
            )
        )
    return tokens


def run_decoder(text: str, config: YomiGenerationConfig) -> list[DecoderCandidate]:
    command = [
        config.decoder_python,
        config.decoder_script,
        "--config",
        config.decoder_config,
        "--json",
        "--text",
        text,
        "--nbest",
        str(config.decoder_nbest),
    ]
    if config.decoder_beam is not None:
        command.extend(["--beam", str(config.decoder_beam)])

    completed = subprocess.run(command, text=True, capture_output=True, check=True)
    return parse_decoder_output(completed.stdout)


def parse_decoder_output(stdout: str) -> list[DecoderCandidate]:
    payload = json.loads(stdout)
    results = payload.get("results", [])
    candidates: list[DecoderCandidate] = []
    for row in results:
        candidates.append(
            DecoderCandidate(
                rank=int(row["rank"]),
                score=float(row["score"]),
                entries=[
                    DecoderEntry(
                        surface=str(entry["surface"]),
                        reading=str(entry["reading"]),
                        final_order=int(entry.get("final_order", 0)),
                        piece_orders=[int(value) for value in entry.get("piece_orders", [])],
                    )
                    for entry in row.get("entries", [])
                ],
            )
        )
    return candidates
