from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from yomi_corpus.vocabulary_balance import (
    CurrentCorpusStats,
    build_bccwj_vocabulary,
    candidate_rows,
    collect_current_document_texts,
)


def bccwj_row(*, document: str, lemma: str, reading: str, pos: str, form: str) -> list[str]:
    fields = [""] * 25
    fields[0] = "OC"
    fields[1] = document
    fields[12] = lemma
    fields[13] = reading
    fields[16] = pos
    fields[21] = form
    return fields


def test_build_bccwj_vocabulary_counts_tokens_and_documents() -> None:
    rows = [
        bccwj_row(document="a", lemma="買収", reading="バイシュウ", pos="名詞-普通名詞-サ変可能", form="買収"),
        bccwj_row(document="a", lemma="買収", reading="バイシュウ", pos="名詞-普通名詞-サ変可能", form="買収"),
        bccwj_row(document="b", lemma="買収", reading="バイシュウ", pos="名詞-普通名詞-サ変可能", form="買収"),
    ]

    vocabulary, token_count, document_count = build_bccwj_vocabulary(rows)

    assert token_count == 3
    assert document_count == 2
    assert vocabulary["買収"].token_count == 3
    assert vocabulary["買収"].document_count == 2
    assert vocabulary["買収"].common_noun_count == 3
    assert vocabulary["買収"].common_noun_document_count == 2


def test_candidate_rows_require_kanji_common_noun_and_absence() -> None:
    rows = [
        bccwj_row(document="a", lemma="買収", reading="バイシュウ", pos="名詞-普通名詞-サ変可能", form="買収"),
        bccwj_row(document="a", lemma="東京", reading="トウキョウ", pos="名詞-固有名詞-地名-一般", form="東京"),
        bccwj_row(document="a", lemma="テレビ", reading="テレビ", pos="名詞-普通名詞-一般", form="テレビ"),
    ]
    vocabulary, _, _ = build_bccwj_vocabulary(rows)
    current = CurrentCorpusStats(token_counts=Counter({"別表記": 1}))

    candidates = candidate_rows(
        vocabulary,
        current,
        min_bccwj_count=1,
        max_current_count=0,
    )

    assert [row["lemma"] for row in candidates] == ["買収"]


def test_candidate_rows_match_any_observed_written_form() -> None:
    rows = [
        bccwj_row(document="a", lemma="引越し", reading="ヒッコシ", pos="名詞-普通名詞-サ変可能", form="引っ越し"),
    ]
    vocabulary, _, _ = build_bccwj_vocabulary(rows)
    current = CurrentCorpusStats(token_counts=Counter({"引っ越し": 2}))

    candidates = candidate_rows(
        vocabulary,
        current,
        min_bccwj_count=1,
        max_current_count=0,
    )

    assert candidates == []


def test_current_documents_use_final_data_only_for_finalized_batches(tmp_path: Path) -> None:
    state_root = tmp_path / "data/pipeline/batches"
    unit_root = tmp_path / "data/units"
    state_root.mkdir(parents=True)
    for batch_name, stage in (("done", "yomi_finalized"), ("active", "final_review_prepared")):
        (state_root / f"{batch_name}.json").write_text(
            json.dumps({"batch_name": batch_name, "track_name": "dev", "current_stage": stage}),
            encoding="utf-8",
        )
        batch_dir = unit_root / batch_name
        batch_dir.mkdir(parents=True)
        (batch_dir / "units.jsonl").write_text(
            json.dumps({"doc_id": batch_name, "unit_seq": 1, "text": f"raw-{batch_name}"}) + "\n",
            encoding="utf-8",
        )
        (batch_dir / "units.yomi.final.jsonl").write_text(
            json.dumps({"doc_id": batch_name, "unit_seq": 1, "text": f"final-{batch_name}"}) + "\n",
            encoding="utf-8",
        )

    documents, finalized = collect_current_document_texts(tmp_path, "dev")

    assert documents == {"active": "raw-active", "done": "final-done"}
    assert finalized == {"done"}
