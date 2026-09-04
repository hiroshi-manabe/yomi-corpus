#!/usr/bin/env python3
"""Build a full BCCWJ lemma inventory and find absent kanji common nouns."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yomi_corpus.vocabulary_balance import (  # noqa: E402
    build_bccwj_vocabulary,
    build_current_corpus_stats,
    candidate_rows,
    collect_current_document_texts,
    current_corpus_rows,
    iter_bccwj_rows,
    vocabulary_rows,
    write_tsv,
)


DEFAULT_BCCWJ_DIR = Path(
    "/panfs/panmt22/ltdata/ORIGINAL/corpus/monolingual/"
    "BCCWJ1.1/Disk2/TSV_SUW_NT"
)
DEFAULT_OUTPUT_DIR = ROOT / "data" / "analysis" / "vocabulary_balance"
VOCAB_FIELDS = (
    "lemma",
    "token_count",
    "document_count",
    "common_noun_count",
    "common_noun_document_count",
    "readings",
    "parts_of_speech",
    "written_forms",
    "registers",
)
CANDIDATE_FIELDS = (
    "lemma",
    "bccwj_count",
    "bccwj_document_count",
    "bccwj_common_noun_count",
    "bccwj_common_noun_document_count",
    "current_count",
    "current_document_count",
    "readings",
    "parts_of_speech",
    "written_forms",
    "registers",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bccwj-dir", type=Path, default=DEFAULT_BCCWJ_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--track", default="dev")
    parser.add_argument("--min-bccwj-count", type=int, default=5)
    parser.add_argument("--max-current-count", type=int, default=0)
    parser.add_argument("--sudachi-dictionary", choices=("small", "core", "full"), default="full")
    parser.add_argument(
        "--sudachi-split-mode",
        choices=("A", "B", "C"),
        default="A",
        help="Use mode A by default because BCCWJ input is SUW (short-unit) data.",
    )
    args = parser.parse_args()

    archive_paths = sorted(args.bccwj_dir.glob("*.zip"))
    if not archive_paths:
        parser.error(f"no BCCWJ ZIP archives found under {args.bccwj_dir}")

    def progress_rows():
        for count, row in enumerate(iter_bccwj_rows(args.bccwj_dir), start=1):
            if count % 5_000_000 == 0:
                print(f"BCCWJ tokens: {count:,}", file=sys.stderr, flush=True)
            yield row

    vocabulary, bccwj_token_count, bccwj_document_count = build_bccwj_vocabulary(progress_rows())
    documents, finalized_documents = collect_current_document_texts(ROOT, args.track)
    current = build_current_corpus_stats(
        documents,
        finalized_documents=finalized_documents,
        sudachi_dictionary=args.sudachi_dictionary,
        sudachi_split_mode=args.sudachi_split_mode,
    )
    candidates = candidate_rows(
        vocabulary,
        current,
        min_bccwj_count=args.min_bccwj_count,
        max_current_count=args.max_current_count,
    )

    vocabulary_path = args.output_dir / "bccwj_lemma_vocabulary.tsv.gz"
    current_path = args.output_dir / "current_corpus_vocabulary.tsv.gz"
    candidates_path = args.output_dir / "missing_kanji_common_nouns.tsv"
    manifest_path = args.output_dir / "manifest.json"
    write_tsv(vocabulary_path, vocabulary_rows(vocabulary), VOCAB_FIELDS)
    write_tsv(
        current_path,
        current_corpus_rows(current),
        ("surface_or_dictionary_form", "token_count", "document_count"),
    )
    write_tsv(candidates_path, candidates, CANDIDATE_FIELDS)

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "bccwj_directory": str(args.bccwj_dir.resolve()),
        "bccwj_archives": [
            {"path": str(path.resolve()), "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for path in archive_paths
        ],
        "bccwj_token_count": bccwj_token_count,
        "bccwj_document_count": bccwj_document_count,
        "bccwj_distinct_lemma_count": len(vocabulary),
        "track": args.track,
        "current_assigned_document_count": current.assigned_documents,
        "current_finalized_document_count": current.finalized_documents,
        "current_distinct_token_or_lemma_count": len(current.token_counts),
        "min_bccwj_count": args.min_bccwj_count,
        "max_current_count": args.max_current_count,
        "sudachi_dictionary": args.sudachi_dictionary,
        "sudachi_split_mode": args.sudachi_split_mode,
        "candidate_count": len(candidates),
        "vocabulary_path": str(vocabulary_path.resolve()),
        "current_vocabulary_path": str(current_path.resolve()),
        "candidate_path": str(candidates_path.resolve()),
        "comparison_note": (
            "A BCCWJ lemma is present when its lemma or any observed BCCWJ written form "
            "matches an exact Sudachi-A surface or dictionary form in an assigned document."
        ),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
