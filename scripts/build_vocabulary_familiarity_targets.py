#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize accepted vocabulary targets from a completed familiarity job."
    )
    parser.add_argument("--source-tsv", required=True)
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--results-jsonl", required=True)
    parser.add_argument("--output-tsv", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_rows = _read_jsonl(Path(args.input_jsonl))
    result_rows = _read_jsonl(Path(args.results_jsonl))

    lemma_by_id: dict[str, str] = {}
    for row in input_rows:
        item_id = str(row["item_id"])
        if item_id in lemma_by_id:
            raise ValueError(f"Duplicate input item_id: {item_id}")
        lemma_by_id[item_id] = str(row["lemma"])

    decisions: dict[str, bool] = {}
    for row in result_rows:
        item_id = str(row["item_id"])
        if item_id not in lemma_by_id:
            raise ValueError(f"Result has unknown item_id: {item_id}")
        if item_id in decisions:
            raise ValueError(f"Duplicate result item_id: {item_id}")
        if row.get("parse_error") is not None or not isinstance(row.get("parsed"), bool):
            raise ValueError(f"Result is not a parsed y/n decision: {item_id}")
        decisions[item_id] = bool(row["parsed"])

    missing = sorted(set(lemma_by_id) - set(decisions))
    if missing:
        raise ValueError(f"Missing {len(missing)} result(s), first: {missing[0]}")
    accepted = {
        lemma_by_id[item_id]
        for item_id, decision in decisions.items()
        if decision
    }

    source_path = Path(args.source_tsv)
    output_path = Path(args.output_tsv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open(encoding="utf-8", newline="") as source, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as destination:
        reader = csv.DictReader(source, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("Source TSV has no header.")
        writer = csv.DictWriter(
            destination,
            fieldnames=reader.fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        written = 0
        for row in reader:
            if row["lemma"] not in accepted:
                continue
            writer.writerow(row)
            written += 1
    if written != len(accepted):
        raise ValueError(f"Wrote {written} rows for {len(accepted)} accepted lemmas.")
    print(f"Wrote {written} accepted targets to {output_path}")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


if __name__ == "__main__":
    main()
