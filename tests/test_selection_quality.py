from __future__ import annotations

from yomi_corpus.selection_quality import annotation_heavy_sentences, parenthetical_readings


def test_single_name_reading_is_not_annotation_heavy() -> None:
    text = "山田太郎（やまだたろう）が訪れた。"

    assert len(parenthetical_readings(text)) == 1
    assert annotation_heavy_sentences(text) == ()


def test_four_parenthetical_readings_make_sentence_annotation_heavy() -> None:
    text = "只（ただ）仮りの御位（みくらい）を、曾（かつ）て猶（なお）疑った。"

    rows = annotation_heavy_sentences(text)

    assert len(rows) == 1
    assert [(item.surface, item.reading) for item in rows[0].matches] == [
        ("只", "ただ"),
        ("御位", "みくらい"),
        ("曾", "かつ"),
        ("猶", "なお"),
    ]


def test_readings_in_separate_sentences_do_not_accumulate() -> None:
    text = "山田（やまだ）と田中（たなか）が来た。佐藤（さとう）と鈴木（すずき）が帰った。"

    assert annotation_heavy_sentences(text) == ()
