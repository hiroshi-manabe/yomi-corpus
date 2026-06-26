#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.yomi.decoder_corpus import export_decoder_corpus_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export finalized yomi units as an extra yomi-decoder training corpus"
    )
    parser.add_argument(
        "--input",
        help="Input units.yomi.final.jsonl. Defaults to data/units/<batch>/units.yomi.final.jsonl.",
    )
    parser.add_argument("--batch", help="Batch name used when --input is omitted")
    parser.add_argument(
        "--track",
        choices=["dev", "working"],
        help="Track name for the default output path.",
    )
    parser.add_argument(
        "--output",
        help=(
            "Output raw corpus text. Defaults to "
            "data/decoder_corpora/<track>/<batch>.txt when --track and --batch are given, "
            "otherwise data/exports/decoder_corpus/<batch>.txt."
        ),
    )
    parser.add_argument("--manifest", help="Output manifest JSON path")
    parser.add_argument("--source-name", help="Source name for manifest metadata")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = resolve_input(args)
    output_path = resolve_output(args, input_path)
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else output_path.with_suffix(output_path.suffix + ".manifest.json")
    )
    source_name = args.source_name or (args.batch or input_path.stem)
    summary = export_decoder_corpus_file(
        input_jsonl=input_path,
        output_txt=output_path,
        manifest_json=manifest_path,
        source_name=source_name,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


def resolve_input(args: argparse.Namespace) -> Path:
    if args.input:
        return Path(args.input)
    if not args.batch:
        raise SystemExit("Provide --input or --batch.")
    return PROJECT_ROOT / "data" / "units" / args.batch / "units.yomi.final.jsonl"


def resolve_output(args: argparse.Namespace, input_path: Path) -> Path:
    if args.output:
        return Path(args.output)
    if args.track and args.batch:
        return PROJECT_ROOT / "data" / "decoder_corpora" / args.track / f"{args.batch}.txt"
    if args.batch:
        return PROJECT_ROOT / "data" / "exports" / "decoder_corpus" / f"{args.batch}.txt"
    return input_path.with_suffix(".decoder_corpus.txt")


if __name__ == "__main__":
    main()
