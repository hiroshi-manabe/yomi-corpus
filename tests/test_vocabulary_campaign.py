from __future__ import annotations

import csv
import gzip
import json
from array import array
from pathlib import Path

from yomi_corpus.vocabulary_campaign import (
    FixedStringMatcher,
    build_coverage_index,
    build_preview_artifact,
    build_selection_plan,
    read_vocabulary_targets,
)
from yomi_corpus.selection_experiments import build_selection_experiment


def test_matcher_counts_overlapping_patterns() -> None:
    matcher = FixedStringMatcher(["株式会社", "会社"])

    assert matcher.count("株式会社と会社") == {"株式会社": 1, "会社": 2}


def test_targets_keep_full_inventory_but_only_search_specific_kanji_forms(tmp_path: Path) -> None:
    path = tmp_path / "targets.tsv"
    write_targets(
        path,
        [
            ("唇", "唇:3|くちびる:1"),
            ("買収", "買収:5"),
        ],
    )

    targets = read_vocabulary_targets(path)

    assert [target.lemma for target in targets] == ["唇", "買収"]
    assert targets[0].forms == ()
    assert targets[1].forms == ("買収",)


def test_index_plan_and_preview_are_advisory_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl.gz"
    documents = [
        ("a", "通常文です。"),
        ("b", "買収と落札を扱う文書です。"),
        ("c", "買収だけを扱います。"),
        ("d", "落札だけを扱います。"),
        ("e", "買収と落札をもう一度扱います。"),
    ]
    with gzip.open(source, "wt", encoding="utf-8") as output:
        for doc_id, text in documents:
            output.write(json.dumps({"text": text, "meta": {"docId": doc_id}}, ensure_ascii=False) + "\n")
    order = tmp_path / "dev.u32"
    values = array("I", range(1, len(documents) + 1))
    order.write_bytes(values.tobytes())
    targets = tmp_path / "targets.tsv"
    write_targets(targets, [("買収", "買収:5"), ("落札", "落札:5"), ("唇", "唇:5")])
    manifest = {
        "cursor": 2,
        "reservation": None,
        "order_generation": 1,
        "source_content_sha256": "source-hash",
        "source_sequence_epoch": "fixture",
    }
    index = tmp_path / "coverage.sqlite3"
    plan_path = tmp_path / "selection.json"
    preview_path = tmp_path / "preview.json"

    metadata = build_coverage_index(
        source_path=source,
        order_path=order,
        order_manifest=manifest,
        targets_path=targets,
        output_path=index,
        reserved_source_documents=1,
        first_mutable_slot=2,
        progress_every=0,
    )
    plan = build_selection_plan(
        index_path=index,
        output_path=plan_path,
        slot_start=2,
        document_count=3,
        target_examples=2,
        order_manifest=manifest,
    )
    first_preview = build_preview_artifact(
        plan_path=plan_path,
        index_path=index,
        output_path=preview_path,
        sample_size=3,
    )
    second_preview = build_preview_artifact(
        plan_path=plan_path,
        index_path=index,
        output_path=tmp_path / "preview2.json",
        sample_size=3,
    )

    assert metadata["target_count"] == 3
    assert metadata["searchable_target_count"] == 2
    assert plan["installed"] is False
    assert plan["status"] == "proposal"
    assert [row["assigned_slot"] for row in plan["selection"]] == [2, 3, 4]
    assert first_preview["read_only"] is True
    assert first_preview["installation_status"] == "not_installed"
    assert [row["source_record_id"] for row in first_preview["documents"]] == [
        row["source_record_id"] for row in second_preview["documents"]
    ]

    experiment = build_selection_experiment(
        plan_path=plan_path,
        index_path=index,
        output_path=tmp_path / "experiment.json",
        character_budgets=(40, 80),
        preview_budget=40,
    )
    assert experiment["artifact_type"] == "vocabulary-selection-experiment"
    assert experiment["read_only"] is True
    assert len(experiment["strategies"]) == 4
    assert all(len(strategy["runs"]) == 2 for strategy in experiment["strategies"])


def write_targets(path: Path, rows: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "lemma",
                "bccwj_common_noun_count",
                "bccwj_common_noun_document_count",
                "written_forms",
            ),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for lemma, forms in rows:
            writer.writerow(
                {
                    "lemma": lemma,
                    "bccwj_common_noun_count": 5,
                    "bccwj_common_noun_document_count": 4,
                    "written_forms": forms,
                }
            )
