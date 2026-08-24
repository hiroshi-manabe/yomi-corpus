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

from yomi_corpus.yomi.ngram_reading_transitions import (
    build_ngram_reading_transition_stats,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build surface-bigram reading-transition statistics."
    )
    parser.add_argument("--source-corpus", required=True)
    parser.add_argument("--extra-corpus", action="append", default=[])
    parser.add_argument("--source-corpus-version", required=True)
    parser.add_argument("--output-tsv", required=True)
    parser.add_argument("--manifest-json", required=True)
    parser.add_argument("--min-surface-count", type=int, default=5)
    parser.add_argument("--shard-count", type=int, default=64)
    parser.add_argument("--no-checksum", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_ngram_reading_transition_stats(
        source_corpus=args.source_corpus,
        additional_source_corpora=args.extra_corpus,
        source_corpus_version=args.source_corpus_version,
        output_tsv=args.output_tsv,
        manifest_json=args.manifest_json,
        min_surface_count=args.min_surface_count,
        shard_count=args.shard_count,
        checksum=not args.no_checksum,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
