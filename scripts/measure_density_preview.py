"""Describe vocabulary gains and observable text shifts in preview samples."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yomi_corpus.historical_register import classify_document
from yomi_corpus.selection_quality import parenthetical_readings
from yomi_corpus.splitter import split_text_into_units


def describe(doc):
    text = doc["text"]
    windows = Counter(text[i:i + 20] for i in range(max(0, len(text) - 19)))
    sentences = [len(s.text) for s in split_text_into_units(text)]
    return {
        "characters": len(text),
        "kanji": len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text)),
        "hiragana": len(re.findall(r"[ぁ-ゖ]", text)),
        "spaces": text.count(" "),
        "brackets": sum(text.count(c) for c in "「」『』【】（）()"),
        "repeated_windows": sum(n - 1 for n in windows.values()),
        "windows": sum(windows.values()),
        "historical_characters": classify_document(text).flagged_character_count,
        "reading_glosses": len(parenthetical_readings(text)),
        "sentence_lengths": sentences,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    preview = json.loads(args.preview.read_text())
    docs = {d["source_line_no"]: d for d in preview["documents"]}
    stats = {line: describe(doc) for line, doc in docs.items()}
    baseline = set(preview["strategies"][0]["preview_source_line_nos"])
    rows = []
    for strategy in preview["strategies"]:
        selected = strategy["preview_source_line_nos"]
        lengths = [stats[n]["characters"] for n in selected]
        total = sum(lengths)
        sentences = [s for n in selected for s in stats[n]["sentence_lengths"]]
        run = next(r for r in strategy["runs"] if r["character_budget"] == preview["preview_character_budget"])
        row = {
            "strategy": strategy["strategy_id"], "documents": len(selected),
            "characters": total, "distinct_targets": run["distinct_target_count"],
            "targets_with_three_examples": run["targets_with_three_examples"],
            "median_document_chars": statistics.median(lengths),
            "median_sentence_chars": statistics.median(sentences),
            "chars_in_docs_under_500_pct": 100 * sum(n for n in lengths if n < 500) / total,
            "chars_in_sentences_over_200_pct": 100 * sum(n for n in sentences if n > 200) / total,
            "baseline_overlap_chars_pct": 100 * sum(stats[n]["characters"] for n in selected if n in baseline) / total,
            "repeated_20char_windows_pct": 100 * sum(stats[n]["repeated_windows"] for n in selected) / max(1, sum(stats[n]["windows"] for n in selected)),
            "reading_glosses_per_10000_chars": 10000 * sum(stats[n]["reading_glosses"] for n in selected) / total,
        }
        for key in ("kanji", "hiragana", "spaces", "brackets", "historical_characters"):
            row[key + "_pct"] = 100 * sum(stats[n][key] for n in selected) / total
        rows.append({k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()})
    payload = {"experiment_id": preview["experiment_id"], "metrics": rows,
               "note": "Descriptive proxies, not quality labels. Samples overlap; one seed only.",
               "document_metrics": stats}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
