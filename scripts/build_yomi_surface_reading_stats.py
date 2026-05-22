#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.paths import resolve_repo_path
from yomi_corpus.yomi.corpus_frequency import build_surface_reading_stats
from yomi_corpus.yomi.config import resolve_config_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build surface/reading frequency stats from the yomi source corpus."
    )
    parser.add_argument(
        "--config",
        default="config/yomi/default.toml",
        help="Yomi config with a [corpus_frequency] section.",
    )
    parser.add_argument("--source-corpus", help="Override source corpus path.")
    parser.add_argument("--source-corpus-version", help="Override source corpus version label.")
    parser.add_argument("--output-tsv", help="Override stats TSV output path.")
    parser.add_argument("--manifest-json", help="Override manifest JSON output path.")
    parser.add_argument(
        "--surface-filter",
        choices=("all", "target"),
        help="Count all surfaces or only surfaces containing kanji-like/Latin characters.",
    )
    parser.add_argument(
        "--no-checksum",
        action="store_true",
        help="Skip SHA-256 checksum calculation for the source corpus.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = resolve_repo_path(args.config)
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    config = payload.get("corpus_frequency", {})

    source_corpus = (
        resolve_repo_path(args.source_corpus)
        if args.source_corpus
        else Path(config["source_corpus"])
    )
    output_tsv = (
        resolve_repo_path(args.output_tsv)
        if args.output_tsv
        else resolve_config_path(config_path, config["stats_artifact"])
    )
    manifest_json = (
        resolve_repo_path(args.manifest_json)
        if args.manifest_json
        else resolve_config_path(config_path, config["manifest"])
    )
    source_corpus_version = str(
        args.source_corpus_version
        or config.get("source_corpus_version")
        or source_corpus.stem
    )
    surface_filter = str(args.surface_filter or config.get("surface_filter", "target"))

    summary = build_surface_reading_stats(
        source_corpus=source_corpus,
        output_tsv=output_tsv,
        manifest_json=manifest_json,
        source_corpus_version=source_corpus_version,
        surface_filter=surface_filter,
        checksum=not args.no_checksum,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
if __name__ == "__main__":
    main()
