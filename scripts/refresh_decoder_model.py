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

from yomi_corpus.decoder_models import refresh_decoder_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh a track-scoped yomi-decoder model from finalized batches."
    )
    parser.add_argument("--track", required=True, choices=["dev", "working"])
    parser.add_argument("--model-id", help="Model directory name. Defaults to UTC timestamp.")
    parser.add_argument(
        "--yomi-config",
        default="config/yomi/default.toml",
        help="Yomi config used to discover the base corpus by default.",
    )
    parser.add_argument(
        "--decoder-build-script",
        help="Path to yomi-decoder/scripts/build_model.py.",
    )
    parser.add_argument(
        "--base-corpus",
        help="Base raw SUW yomi corpus. Defaults to [corpus_frequency].source_corpus.",
    )
    parser.add_argument(
        "--corpus-root",
        default="data/decoder_corpora",
        help="Root for exported per-track decoder corpora.",
    )
    parser.add_argument(
        "--model-root",
        default="data/decoder_models",
        help="Root for generated per-track decoder models.",
    )
    parser.add_argument(
        "--skip-kenlm",
        action="store_true",
        help="Pass --skip-kenlm to yomi-decoder build_model.py. Useful for tests.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = refresh_decoder_model(
        root=PROJECT_ROOT,
        track_name=args.track,
        model_id=args.model_id,
        yomi_config_path=args.yomi_config,
        decoder_build_script=args.decoder_build_script,
        corpus_root=PROJECT_ROOT / args.corpus_root,
        model_root=PROJECT_ROOT / args.model_root,
        base_corpus=args.base_corpus,
        skip_kenlm=args.skip_kenlm,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
