from __future__ import annotations

import csv
import json
from pathlib import Path

from yomi_corpus.yomi.stable_surface_lexicon import (
    StableSurfaceReadingLexicon,
    build_stable_surface_lexicon,
)


def write_corpus(path: Path, sentences: list[list[tuple[str, str]]]) -> None:
    lines: list[str] = []
    for sentence in sentences:
        for surface, reading in sentence:
            lines.append(f"{surface}\t名詞\t*\t*\t{reading}")
        lines.append("EOS")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["surface"]: row for row in csv.DictReader(handle, delimiter="\t")}


def test_builds_boundary_insensitive_unique_readings(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(
        corpus,
        [
            *[[('一', 'イッ'), ('回', 'カイ')] for _ in range(5)],
            *[[('皆', 'ミナ'), ('様', 'サマ')] for _ in range(3)],
            *[[('皆様', 'ミナサマ')] for _ in range(2)],
            *[[('身体', 'シンタイ')] for _ in range(4)],
            [('身体', 'カラダ')],
            *[[('𠮟', 'シカ'), ('る', 'ル')] for _ in range(5)],
        ],
    )
    output = tmp_path / "stable.tsv"
    manifest = tmp_path / "stable.manifest.json"

    summary = build_stable_surface_lexicon(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=manifest,
        source_corpus_version="test:v1",
        min_count=5,
        shard_count=2,
    )

    rows = load_rows(output)
    assert rows["一回"]["reading"] == "イッカイ"
    assert rows["一回"]["count"] == "5"
    assert rows["皆様"]["reading"] == "ミナサマ"
    assert rows["皆様"]["min_span_tokens"] == "1"
    assert rows["皆様"]["max_span_tokens"] == "2"
    segmentations = json.loads(rows["皆様"]["segmentation_counts_json"])
    assert segmentations == [
        {"surfaces": ["皆", "様"], "count": 3},
        {"surfaces": ["皆様"], "count": 2},
    ]
    assert rows["𠮟"]["reading"] == "シカ"
    assert "身体" not in rows
    assert summary.ambiguous_surface_count >= 1
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_corpus_version"] == "test:v1"
    assert payload["parameters"]["min_share"] == 0.98
    assert payload["parameters"]["require_unique_reading"] is False


def test_does_not_span_sentence_or_punctuation_boundaries(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(
        corpus,
        [
            *[[('株式', 'カブシキ')] for _ in range(5)],
            *[[('会社', 'ガイシャ')] for _ in range(5)],
            *[[('株式', 'カブシキ'), ('。', 'テン'), ('会社', 'ガイシャ')] for _ in range(5)],
        ],
    )
    output = tmp_path / "stable.tsv"

    build_stable_surface_lexicon(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=tmp_path / "manifest.json",
        source_corpus_version="test:v1",
        min_count=5,
        shard_count=2,
    )

    rows = load_rows(output)
    assert "株式会社" not in rows


def test_conflicting_tokenizations_compete_by_surface(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(
        corpus,
        [
            *[[('一', 'イッ'), ('回', 'カイ')] for _ in range(5)],
            [('一回', 'イチカイ')],
        ],
    )
    output = tmp_path / "stable.tsv"

    summary = build_stable_surface_lexicon(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=tmp_path / "manifest.json",
        source_corpus_version="test:v1",
        min_count=5,
        shard_count=2,
    )

    assert "一回" not in load_rows(output)
    assert summary.ambiguous_surface_count >= 1


def test_accepts_dominant_reading_at_share_threshold(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(
        corpus,
        [
            *[[('株式', 'カブシキ'), ('会社', 'ガイシャ')] for _ in range(19)],
            [('株式', 'カブシキ'), ('会社', 'カイシャ')],
        ],
    )
    output = tmp_path / "stable.tsv"

    build_stable_surface_lexicon(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=tmp_path / "manifest.json",
        source_corpus_version="test:v1",
        min_count=5,
        min_share=0.95,
        shard_count=2,
    )

    row = load_rows(output)["株式会社"]
    assert row["reading"] == "カブシキガイシャ"
    assert row["count"] == "19"
    assert row["surface_total_count"] == "20"
    assert row["share"] == "0.95"


def test_loaded_lexicon_requires_the_dominant_reading(tmp_path: Path) -> None:
    corpus = tmp_path / "source.txt"
    write_corpus(corpus, [[('一', 'イッ'), ('回', 'カイ')] for _ in range(5)])
    output = tmp_path / "stable.tsv"
    build_stable_surface_lexicon(
        source_corpus=corpus,
        output_tsv=output,
        manifest_json=tmp_path / "manifest.json",
        source_corpus_version="test:v1",
        shard_count=2,
    )

    lexicon = StableSurfaceReadingLexicon.load_tsv(output)

    accepted = lexicon.judge("一回", "イッカイ")
    rejected = lexicon.judge("一回", "イチカイ")
    assert accepted.value is True
    assert accepted.evidence is not None
    assert accepted.evidence.count == 5
    assert rejected.value is False
    assert rejected.reason == "stable_surface_reading_mismatch:イッカイ"


def test_loaded_lexicon_rejects_rows_below_runtime_share_threshold(tmp_path: Path) -> None:
    output = tmp_path / "stable.tsv"
    output.write_text(
        "surface\treading\tcount\tsurface_total_count\tshare\t"
        "source_corpus_version\n"
        "方へ\tカタヘ\t57\t60\t0.95\tfixture\n",
        encoding="utf-8",
    )

    lexicon = StableSurfaceReadingLexicon.load_tsv(output)

    judgment = lexicon.judge("方へ", "カタヘ")
    assert judgment.value is False
    assert judgment.reason == "stable_surface_share_below_threshold:0.98"
