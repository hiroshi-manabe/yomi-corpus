#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_QUEUE = Path("data/units/dev_batch_0001/yomi_reading_input.jsonl")
DEFAULT_REGRESSION = Path("data/evals/yomi_reading/regression_v1.jsonl")
DEFAULT_SCORED = Path(
    "runs/prompt_experiments/yomi_reading_completion_v1_latin_gpt54mini_dev155/scored.jsonl"
)
DEFAULT_OUTPUT_TSV = Path("data/evals/yomi_reading/gold_review_seed_v1.tsv")
DEFAULT_OUTPUT_JSONL = Path("data/evals/yomi_reading/gold_review_seed_v1.jsonl")
DEFAULT_SUMMARY = Path("data/evals/yomi_reading/gold_review_seed_v1.summary.json")

LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")
KANJI_RE = re.compile(r"[\u3400-\u9fff々〆〻]")
ITERATION_RE = re.compile(r"[々〆〻]")

REVIEW_COLUMNS = [
    "review_status",
    "expected_reading",
    "item_id",
    "surface",
    "current_reading_hiragana",
    "llm_reading",
    "llm_raw_text",
    "llm_parse_error",
    "seed_source",
    "feature_bucket",
    "unit_id",
    "token_surface",
    "chunk_index",
    "marked_text",
    "note",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a review pack for manually creating yomi-reading gold data."
    )
    parser.add_argument("--queue-jsonl", default=str(DEFAULT_QUEUE))
    parser.add_argument("--regression-jsonl", default=str(DEFAULT_REGRESSION))
    parser.add_argument("--scored-jsonl", default=str(DEFAULT_SCORED))
    parser.add_argument("--output-tsv", default=str(DEFAULT_OUTPUT_TSV))
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT_JSONL))
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--target-size", type=int, default=120)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    queue_rows = load_jsonl(Path(args.queue_jsonl))
    regression_rows = load_jsonl(Path(args.regression_jsonl))
    scored_rows = load_jsonl(Path(args.scored_jsonl)) if Path(args.scored_jsonl).exists() else []

    queue_by_id = {str(row["item_id"]): row for row in queue_rows}
    scored_by_id = {str(row["item_id"]): row for row in scored_rows}
    failed_ids = [str(row["item_id"]) for row in scored_rows if not row.get("passed")]

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in regression_rows:
        item_id = str(row["item_id"])
        selected.append(review_row_from_regression(row))
        seen.add(item_id)

    for item_id in failed_ids:
        queue_row = queue_by_id.get(item_id)
        if queue_row is None or item_id in seen:
            continue
        selected.append(review_row_from_queue(queue_row, scored_by_id.get(item_id), "llm_failure_seed"))
        seen.add(item_id)

    rng = random.Random(args.seed)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in queue_rows:
        item_id = str(row["item_id"])
        if item_id in seen:
            continue
        buckets[classify_queue_row(row)].append(row)
    for rows in buckets.values():
        rng.shuffle(rows)

    bucket_order = [
        "latin",
        "iteration_mark",
        "okurigana_chunk",
        "single_kanji_context",
        "multi_kanji_compound",
        "other_kanji_or_latin",
    ]
    while len(selected) < args.target_size:
        added = False
        for bucket in bucket_order:
            rows = buckets.get(bucket) or []
            while rows and str(rows[-1]["item_id"]) in seen:
                rows.pop()
            if not rows:
                continue
            row = rows.pop()
            selected.append(review_row_from_queue(row, scored_by_id.get(str(row["item_id"])), "stratified_queue_sample"))
            seen.add(str(row["item_id"]))
            added = True
            if len(selected) >= args.target_size:
                break
        if not added:
            break

    output_tsv = Path(args.output_tsv)
    output_jsonl = Path(args.output_jsonl)
    summary_json = Path(args.summary_json)
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(output_tsv, selected)
    write_jsonl(output_jsonl, selected)

    summary = {
        "queue_jsonl": str(args.queue_jsonl),
        "regression_jsonl": str(args.regression_jsonl),
        "scored_jsonl": str(args.scored_jsonl),
        "output_tsv": str(output_tsv),
        "output_jsonl": str(output_jsonl),
        "target_size": args.target_size,
        "selected_count": len(selected),
        "seed": args.seed,
        "seed_source_counts": dict(Counter(row["seed_source"] for row in selected)),
        "feature_bucket_counts": dict(Counter(row["feature_bucket"] for row in selected)),
        "notes": [
            "Rows with seed_source=regression_gold_seed have expected_reading prefilled.",
            "Other rows intentionally leave expected_reading blank for human review.",
            "current_reading_hiragana is context, not gold.",
        ],
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def review_row_from_regression(row: dict[str, Any]) -> dict[str, str]:
    surface = str(row.get("surface", ""))
    return normalize_review_row(
        {
            "review_status": "accepted_seed",
            "expected_reading": str(row.get("expected_reading", "")),
            "item_id": str(row.get("item_id", "")),
            "surface": surface,
            "current_reading_hiragana": "",
            "llm_reading": "",
            "llm_raw_text": "",
            "llm_parse_error": "",
            "seed_source": "regression_gold_seed",
            "feature_bucket": classify_surface(surface, token_surface=surface, chunk_index=""),
            "unit_id": str(row.get("unit_id", "")),
            "token_surface": surface,
            "chunk_index": "",
            "marked_text": str(row.get("marked_text", "")),
            "note": str(row.get("note", "")),
        }
    )


def review_row_from_queue(
    row: dict[str, Any],
    scored: dict[str, Any] | None,
    seed_source: str,
) -> dict[str, str]:
    surface = str(row.get("surface", ""))
    actual = scored.get("actual") if isinstance(scored, dict) else None
    llm_reading = actual.get("reading") if isinstance(actual, dict) else ""
    return normalize_review_row(
        {
            "review_status": "",
            "expected_reading": "",
            "item_id": str(row.get("item_id", "")),
            "surface": surface,
            "current_reading_hiragana": str(row.get("current_reading_hiragana", "")),
            "llm_reading": "" if llm_reading is None else str(llm_reading),
            "llm_raw_text": "" if scored is None else str(scored.get("raw_text") or ""),
            "llm_parse_error": "" if scored is None else str(scored.get("parse_error") or ""),
            "seed_source": seed_source,
            "feature_bucket": classify_queue_row(row),
            "unit_id": str(row.get("unit_id", "")),
            "token_surface": str(row.get("token_surface", "")),
            "chunk_index": str(row.get("chunk_index", "")),
            "marked_text": str(row.get("marked_text", "")),
            "note": str(row.get("note", "")),
        }
    )


def normalize_review_row(row: dict[str, Any]) -> dict[str, str]:
    return {column: str(row.get(column, "")) for column in REVIEW_COLUMNS}


def classify_queue_row(row: dict[str, Any]) -> str:
    return classify_surface(
        str(row.get("surface", "")),
        token_surface=str(row.get("token_surface", "")),
        chunk_index=str(row.get("chunk_index", "")),
    )


def classify_surface(surface: str, *, token_surface: str, chunk_index: str) -> str:
    if LATIN_RE.search(surface):
        return "latin"
    if ITERATION_RE.search(surface):
        return "iteration_mark"
    if token_surface and token_surface != surface:
        return "okurigana_chunk"
    kanji_count = len(KANJI_RE.findall(surface))
    if kanji_count == 1:
        return "single_kanji_context"
    if kanji_count >= 2:
        return "multi_kanji_compound"
    return "other_kanji_or_latin"


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, dialect="excel-tab", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
