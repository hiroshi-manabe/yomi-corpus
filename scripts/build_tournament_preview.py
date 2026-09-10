"""Build read-only random samples from the density tournament experiment."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yomi_corpus.vocabulary_campaign import extract_source_texts, index_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    experiment = json.loads(args.experiment.read_text())
    samples = {size: random.Random(experiment["seed"]).sample(selection["source_lines"], 50)
               for size, selection in experiment["selections"].items()}
    lines = {line for sample in samples.values() for line in sample}
    with sqlite3.connect(args.index) as conn:
        meta = index_metadata(conn)
        documents = []
        for line in sorted(lines):
            record, slot, length = conn.execute(
                "SELECT source_record_id,current_slot,text_length FROM documents WHERE source_line_no=?",
                (line,)).fetchone()
            targets = [r[0] for r in conn.execute(
                "SELECT lemma FROM hits JOIN targets USING(target_id) WHERE source_line_no=? ORDER BY lemma", (line,))]
            documents.append(dict(source_line_no=line, source_record_id=record, current_slot=slot,
                                  text_length=length, matched_targets=targets))
    texts = extract_source_texts(Path(meta["source_path"]), lines)
    for doc in documents:
        doc["text"] = texts[doc["source_line_no"]]
    strategies = []
    for summary in experiment["summaries"]:
        size = summary["group_size"]
        counts = experiment["selections"][str(size)]["target_document_counts"]
        hits = sum(counts.values())
        distinct = summary["targets_at_least"]["1"]
        strategies.append(dict(
            strategy_id=f"group_{size}", label=f"候補群 {size}" + ("（無作為）" if size == 1 else ""),
            description=f"密度5語/千字以上。{size}文書から選定済み文書に未出現の対象語が最も多いものを選択。" +
                ("共通の無作為な基準文書に対し、長さ±20%以内の候補を比較。" if experiment.get("length_matched") else "") +
                "表は2,000文書全体、下は無作為に抽出した50文書です。",
            preview_source_line_nos=samples[str(size)],
            runs=[dict(character_budget=summary["characters"], selected_document_count=2000,
                       distinct_target_count=distinct,
                       targets_with_two_examples=summary["targets_at_least"]["2"],
                       targets_with_three_examples=summary["targets_at_least"]["3"],
                       distinct_targets_per_10000_characters=distinct*10000/summary["characters"],
                       duplicate_hit_share=(hits-distinct)/hits)]))
    identity = hashlib.sha256(args.experiment.read_bytes()).hexdigest()[:16]
    artifact = dict(schema_version=2, artifact_type="vocabulary-selection-experiment",
        experiment_id=identity, read_only=True, installation_status="not_installed",
        candidate_document_count=experiment["density_eligible_count"], preview_character_budget=0,
        preview_description=("長さを揃えた比較 · " if experiment.get("length_matched") else "") + "候補群1・5・20 · 各2,000文書から無作為に50文書を表示 · 未適用",
        quality_description="全方式で密度5語/千字以上、古文等・読み注記過多の共通フィルターを適用しています。",
        sample_heading="2,000文書から無作為に抽出した50文書", strategies=strategies, documents=documents)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(identity, len(documents))


if __name__ == "__main__":
    main()
