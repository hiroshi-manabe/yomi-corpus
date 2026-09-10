"""Measure a fixed-size random campaign using a preview's pool and exclusions."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sqlite3
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=5)
    parser.add_argument("--documents", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260907)
    args = parser.parse_args()
    pool = json.loads(args.pool.read_text())
    preview = json.loads(args.preview.read_text())
    if pool["plan_id"] != preview["plan_id"]:
        raise ValueError("Pool and preview must belong to the same experiment")
    excluded = {r["source_line_no"]
                for gate in ("historical_gate", "parenthetical_reading_gate")
                for r in preview[gate]["excluded_documents"]}
    ordered = sorted(pool["selection"], key=lambda r: r["source_line_no"])
    random.Random(args.seed).shuffle(ordered)
    eligible = [r for r in ordered if r["source_line_no"] not in excluded
                and r["text_length"] > 0
                and len(set(r["targets"])) * 1000 / r["text_length"] >= args.threshold]
    if len(eligible) < args.documents:
        raise ValueError(f"Only {len(eligible)} eligible documents")
    selected = eligible[:args.documents]
    coverage = Counter(t for r in selected for t in set(r["targets"]))
    with sqlite3.connect(args.index) as conn:
        names = [r[0] for r in conn.execute("SELECT lemma FROM targets ORDER BY lemma")]
    lengths = [r["text_length"] for r in selected]
    total = sum(lengths)
    summary = {
        "pool_documents": len(ordered), "eligible_documents": len(eligible),
        "selected_documents": len(selected), "characters": total,
        "mean_document_characters": round(statistics.mean(lengths), 1),
        "median_document_characters": statistics.median(lengths),
        "target_vocabulary": len(names),
        "targets_at_least": {str(n): sum(c >= n for c in coverage.values()) for n in (1, 2, 3, 5, 10)},
        "target_document_hits": sum(coverage.values()),
        "distinct_targets_per_100000_characters": round(len(coverage) * 100000 / total, 1),
        "three_example_goal_filled_slots": sum(min(3, c) for c in coverage.values()),
    }
    artifact = {"experiment_id": preview["experiment_id"], "seed": args.seed,
                "threshold_per_1000_chars": args.threshold,
                "status": "analysis_only", "summary": summary,
                "selection": selected,
                "target_document_counts": {name: coverage[name] for name in names}}
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
