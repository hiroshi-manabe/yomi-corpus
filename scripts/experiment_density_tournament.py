"""Compare random candidate groups drawn from the full density-eligible index."""
import argparse
import bisect
from collections import Counter
import json
from pathlib import Path
import random
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yomi_corpus.selection_experiments import _historical_gate_matches
from yomi_corpus.historical_register import classify_document
from yomi_corpus.selection_quality import annotation_heavy_sentences
from yomi_corpus.vocabulary_campaign import extract_source_texts, index_metadata
from measure_density_preview import describe
import statistics


def select_groups(rows, size, count):
    coverage = Counter()
    selected = []
    for offset in range(0, len(rows) - size + 1, size):
        group = rows[offset:offset + size]
        best = max(group, key=lambda r: sum(coverage[t] == 0 for t in r["targets"]))
        selected.append(best)
        coverage.update(best["targets"])
        if len(selected) == count:
            return selected, coverage
    raise ValueError("Insufficient eligible random candidates")


def select_length_matched(rows, count, seed):
    references = rows[:count]
    alternatives = sorted(rows[count:], key=lambda r: r["text_length"])
    lengths = [r["text_length"] for r in alternatives]
    rng = random.Random(seed)
    selected = {size: [] for size in (1, 5, 20)}
    coverage = {size: Counter() for size in selected}
    used = set()
    rounds = []
    for reference in references:
        length = reference["text_length"]
        lo = bisect.bisect_left(lengths, length * 0.8)
        hi = bisect.bisect_right(lengths, length * 1.2)
        available = [r for r in alternatives[lo:hi] if r["source_line_no"] not in used]
        group = [reference] + rng.sample(available, min(19, len(available)))
        rounds.append([r["source_line_no"] for r in group])
        for size in selected:
            best = max(group[:size], key=lambda r: sum(coverage[size][t] == 0 for t in r["targets"]))
            selected[size].append(best)
            coverage[size].update(best["targets"])
        # Reserve references for their own rounds; remove any winner from later alternatives.
        used.update(selected[size][-1]["source_line_no"] for size in selected)
    return selected, coverage, rounds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--length-matched", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(args.index) as conn:
        meta = index_metadata(conn)
        names = dict(conn.execute("SELECT target_id, lemma FROM targets"))
        eligible = [r[0] for r in conn.execute(
            "SELECT d.source_line_no FROM documents d JOIN hits h USING(source_line_no) "
            "WHERE d.source_line_no > 50000 AND d.text_length > 0 "
            "GROUP BY d.source_line_no HAVING COUNT(*) * 1000 >= 5 * d.text_length "
            "ORDER BY d.source_line_no")]
        print(f"Density eligible: {len(eligible)}", flush=True)
        random.Random(20260907).shuffle(eligible)
        # Deferred quality checks on a uniform random prefix avoid analyzing millions of texts.
        candidates = eligible[:50000]
        conn.execute("CREATE TEMP TABLE chosen(line INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO chosen VALUES (?)", ((n,) for n in candidates))
        targets = {}
        for line, target in conn.execute(
            "SELECT source_line_no,target_id FROM hits JOIN chosen ON line=source_line_no"):
            targets.setdefault(line, []).append(target)
    print("Reading candidate text", flush=True)
    texts = extract_source_texts(Path(meta["source_path"]), set(candidates))
    rows = []
    rejected = []
    for line in candidates:
        text = texts[line]
        if _historical_gate_matches(classify_document(text)) or annotation_heavy_sentences(text):
            rejected.append(line)
            continue
        rows.append({"source_line_no": line, "targets": targets[line], "text_length": len(text)})
        if len(rows) == 40000:
            break
    print(f"Quality eligible prefix: {len(rows)}; rejected {len(rejected)}", flush=True)
    summaries = []
    selections = {}
    cache = {}
    rounds = []
    if args.length_matched:
        matched, matched_coverage, rounds = select_length_matched(rows, 2000, 20260907)
    for size in (1, 5, 20):
        selected, coverage = (matched[size], matched_coverage[size]) if args.length_matched else select_groups(rows, size, 2000)
        lengths = [len(texts[r["source_line_no"]]) for r in selected]
        total = sum(lengths)
        for row in selected:
            line = row["source_line_no"]
            if line not in cache:
                cache[line] = describe({"text": texts[line]})
        stats = [cache[r["source_line_no"]] for r in selected]
        summary = {
            "group_size": size, "documents": len(selected), "characters": total,
            "median_document_characters": statistics.median(lengths),
            "targets_at_least": {n: sum(c >= n for c in coverage.values()) for n in (1, 2, 3)},
            "distinct_per_100000_chars": round(len(coverage) * 100000 / total, 1),
            "three_example_slots": sum(min(3, c) for c in coverage.values()),
        }
        for key in ("kanji", "hiragana", "brackets", "spaces", "historical_characters"):
            summary[key + "_pct"] = round(100 * sum(s[key] for s in stats) / total, 2)
        summary["repeated_20char_windows_pct"] = round(100 * sum(s["repeated_windows"] for s in stats) / sum(s["windows"] for s in stats), 2)
        summaries.append(summary)
        selections[size] = {"source_lines": [r["source_line_no"] for r in selected],
                            "target_document_counts": {names[t]: coverage[t] for t in names}}
        print(json.dumps(summary), flush=True)
    args.output.write_text(json.dumps({"status": "analysis_only", "seed": 20260907,
        "threshold": 5, "reserved_source_documents": 50000,
        "density_eligible_count": len(eligible), "target_count": len(names),
        "quality_rejected_lines": rejected, "summaries": summaries,
        "length_matched": args.length_matched, "length_tolerance": 0.2 if args.length_matched else None,
        "candidate_rounds": rounds,
        "selections": selections}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
