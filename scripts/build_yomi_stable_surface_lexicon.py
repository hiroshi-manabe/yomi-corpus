#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.yomi.stable_surface_lexicon import build_stable_surface_lexicon


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a boundary-insensitive stable surface/reading lexicon."
    )
    parser.add_argument("--source-corpus", required=True)
    parser.add_argument("--extra-corpus", action="append", default=[])
    parser.add_argument("--source-corpus-version", required=True)
    parser.add_argument("--output-tsv", required=True)
    parser.add_argument("--manifest-json", required=True)
    parser.add_argument("--min-count", type=int, default=5)
    parser.add_argument("--min-share", type=float, default=0.95)
    parser.add_argument("--max-span-tokens", type=int, default=4)
    parser.add_argument("--max-surface-chars", type=int, default=16)
    parser.add_argument("--shard-count", type=int, default=64)
    parser.add_argument("--no-checksum", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_stable_surface_lexicon(
        source_corpus=args.source_corpus,
        additional_source_corpora=args.extra_corpus,
        source_corpus_version=args.source_corpus_version,
        output_tsv=args.output_tsv,
        manifest_json=args.manifest_json,
        min_count=args.min_count,
        min_share=args.min_share,
        max_span_tokens=args.max_span_tokens,
        max_surface_chars=args.max_surface_chars,
        shard_count=args.shard_count,
        checksum=not args.no_checksum,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
