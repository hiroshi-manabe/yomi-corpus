#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys
import tomllib
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.sentence_boundary_diagnostics import compare_document_boundaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the current character-based unit boundaries with boundaries "
            "derived from Sudachi sentence-punctuation tokens."
        )
    )
    parser.add_argument("--track", default="dev")
    parser.add_argument(
        "--ledger",
        type=Path,
        help="Document ledger path. Defaults to data/pipeline/document_ledger/<track>.json.",
    )
    parser.add_argument(
        "--max-track-doc-seq",
        type=int,
        help="Only inspect documents through this stable document number.",
    )
    parser.add_argument(
        "--dictionary",
        choices=("small", "core", "full"),
        default="full",
    )
    parser.add_argument("--context-chars", type=int, default=35)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ledger_path = args.ledger or (
        PROJECT_ROOT / "data" / "pipeline" / "document_ledger" / f"{args.track}.json"
    )
    documents = load_ledger_documents(
        ledger_path,
        max_track_doc_seq=args.max_track_doc_seq,
    )
    source_paths = load_dataset_source_paths(documents)
    source_texts = load_source_texts(documents, source_paths=source_paths)

    from sudachipy import dictionary, tokenizer as sudachi_tokenizer

    tokenizer = dictionary.Dictionary(dict=args.dictionary).create()
    split_mode = sudachi_tokenizer.Tokenizer.SplitMode.C
    differences: list[dict[str, Any]] = []
    totals = {
        "documents_analyzed": 0,
        "documents_with_differences": 0,
        "current_boundary_count": 0,
        "sudachi_boundary_count": 0,
        "current_only_boundary_count": 0,
        "sudachi_only_boundary_count": 0,
    }
    for document in documents:
        doc_id = str(document["doc_id"])
        text = source_texts.get(doc_id)
        if text is None:
            raise RuntimeError(f"Source text was not loaded for {doc_id}")
        comparison = compare_document_boundaries(
            text,
            tokenizer=tokenizer,
            split_mode=split_mode,
            context_chars=args.context_chars,
        )
        totals["documents_analyzed"] += 1
        totals["current_boundary_count"] += comparison["current_boundary_count"]
        totals["sudachi_boundary_count"] += comparison["sudachi_boundary_count"]
        totals["current_only_boundary_count"] += len(comparison["current_only"])
        totals["sudachi_only_boundary_count"] += len(comparison["sudachi_only"])
        if not comparison["current_only"] and not comparison["sudachi_only"]:
            continue
        totals["documents_with_differences"] += 1
        differences.append(
            {
                "track_doc_seq": int(document["track_doc_seq"]),
                "doc_id": doc_id,
                "dataset_name": str(document["dataset_name"]),
                "source_line_no": int(document["source_line_no"]),
                **comparison,
            }
        )

    result = {
        "schema_version": 1,
        "track": args.track,
        "dictionary": args.dictionary,
        "boundary_policy": (
            "Sudachi mode C; split after 補助記号/句点 tokens and newline characters"
        ),
        "totals": totals,
        "difference_character_counts": {
            "current_only": boundary_character_counts(differences, "current_only"),
            "sudachi_only": boundary_character_counts(differences, "sudachi_only"),
        },
        "differences": differences,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output_path = args.output
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output_path}")
        print(json.dumps(totals, ensure_ascii=False, indent=2))
    else:
        print(rendered, end="")


def boundary_character_counts(
    differences: list[dict[str, Any]],
    comparison_key: str,
) -> dict[str, int]:
    counts = Counter(
        str(boundary["preceding_character"])
        for difference in differences
        for boundary in difference[comparison_key]
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def load_ledger_documents(
    ledger_path: Path,
    *,
    max_track_doc_seq: int | None,
) -> list[dict[str, Any]]:
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    documents = [
        document
        for document in payload.get("documents", [])
        if max_track_doc_seq is None
        or int(document.get("track_doc_seq") or 0) <= max_track_doc_seq
    ]
    return sorted(documents, key=lambda document: int(document["track_doc_seq"]))


def load_dataset_source_paths(documents: list[dict[str, Any]]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for document in documents:
        dataset_name = str(document["dataset_name"])
        if dataset_name in paths:
            continue
        config_path = PROJECT_ROOT / "config" / "datasets" / f"{dataset_name}.toml"
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        source_path = Path(str(config["source_path"]))
        if not source_path.is_absolute():
            source_path = (PROJECT_ROOT / source_path).resolve()
        paths[dataset_name] = source_path
    return paths


def load_source_texts(
    documents: list[dict[str, Any]],
    *,
    source_paths: dict[str, Path],
) -> dict[str, str]:
    by_dataset: dict[str, dict[int, str]] = {}
    for document in documents:
        by_dataset.setdefault(str(document["dataset_name"]), {})[
            int(document["source_line_no"])
        ] = str(document["doc_id"])

    texts: dict[str, str] = {}
    for dataset_name, targets in by_dataset.items():
        remaining = set(targets)
        with gzip.open(source_paths[dataset_name], "rt", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if line_no not in remaining:
                    continue
                payload = json.loads(line)
                text = payload.get("text")
                if not isinstance(text, str):
                    raise ValueError(f"Missing text at {dataset_name}:{line_no}")
                texts[targets[line_no]] = text
                remaining.remove(line_no)
                if not remaining:
                    break
        if remaining:
            missing = ", ".join(str(line_no) for line_no in sorted(remaining)[:10])
            raise RuntimeError(f"Missing source lines for {dataset_name}: {missing}")
    return texts


if __name__ == "__main__":
    main()
