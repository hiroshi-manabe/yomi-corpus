from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.yomi.ngram_reading_transitions import (
    NgramReadingTransitionStats,
    build_ngram_reading_transition_stats,
)


def write_corpus(path: Path, sentences: list[list[tuple[str, str]]]) -> None:
    lines: list[str] = []
    for sentence in sentences:
        for surface, reading in sentence:
            lines.append(f"{surface}\t*\t{surface}\t{surface}\t{reading}")
        lines.append("EOS")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_transition_stats_reject_weak_and_non_dominant_readings(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(
        corpus,
        [
            *[[('一', 'イッ'), ('周', 'シュウ')] for _ in range(6)],
            *[[('一', 'イチ'), ('周', 'シュウ')] for _ in range(2)],
            *[[('学', 'ガク'), ('校', 'コウ')] for _ in range(5)],
        ],
    )
    output = tmp_path / "transitions.tsv"
    manifest = tmp_path / "transitions.manifest.json"

    summary = build_ngram_reading_transition_stats(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=manifest,
        source_corpus_version="test:v1",
        shard_count=2,
    )
    stats = NgramReadingTransitionStats.load_tsv(output)

    correct = stats.judge("一", "イッ", "周", "シュウ")
    incorrect = stats.judge("一", "イチ", "周", "シュウ")
    stable = stats.judge("学", "ガク", "校", "コウ")
    assert correct.value is False
    assert correct.reason == "weak_dominant_share"
    assert correct.candidate is not None
    assert correct.candidate.count == 6
    assert correct.candidate.surface_total_count == 8
    assert correct.candidate.share == 0.75
    assert incorrect.value is False
    assert incorrect.reason == "non_dominant_reading_transition"
    assert incorrect.candidate is not None
    assert incorrect.candidate.count == 2
    assert incorrect.candidate.share == 0.25
    assert incorrect.dominant == correct.dominant
    assert stable.value is True
    assert summary.retained_surface_transition_count == 2
    assert summary.retained_reading_transition_count == 3
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["parameters"]["default_safety_min_reading_share"] == 0.95


def test_transition_stats_do_not_cross_sentence_boundaries(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(
        corpus,
        [[("一", "イチ")], [("周", "シュウ")]],
    )
    output = tmp_path / "transitions.tsv"
    build_ngram_reading_transition_stats(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=tmp_path / "manifest.json",
        source_corpus_version="test:v1",
        min_surface_count=1,
        shard_count=1,
    )

    stats = NgramReadingTransitionStats.load_tsv(output)
    assert stats.judge("一", "イチ", "周", "シュウ").reason == (
        "missing_surface_transition"
    )
