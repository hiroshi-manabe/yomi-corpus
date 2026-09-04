from __future__ import annotations

from yomi_corpus.historical_register import classify_document, classify_sentence


def test_classifies_historical_kana_without_confusing_modern_prose() -> None:
    old = classify_sentence("敵艦を追ひつゝ港へ向つた。")
    modern = classify_sentence("敵艦を追いながら港へ向かった。")

    assert "historical_kana" in old.labels
    assert modern.labels == ()


def test_old_kanji_requires_more_than_one_name_prone_character() -> None:
    old = classify_sentence("舊國體を論ず。")
    surname = classify_sentence("龍澤さんが来ました。")

    assert "old_kanji" in old.labels
    assert "old_kanji" not in surname.labels


def test_classifies_kanbun_but_not_a_short_kanji_name() -> None:
    kanbun = classify_sentence("学而時習之、不亦説乎。")
    name = classify_sentence("山田太郎。")

    assert "kanbun" in kanbun.labels
    assert name.labels == ()


def test_old_japanese_spelling_alone_is_not_mislabeled_as_kanbun() -> None:
    result = classify_sentence("昔の城跡であったと云ふ。")

    assert "historical_kana" in result.labels
    assert "kanbun" not in result.labels


def test_modern_sino_japanese_legal_prose_is_not_kanbun() -> None:
    result = classify_sentence("不法行為の差止請求と国家賠償法の適用を検討する。")

    assert "kanbun" not in result.labels


def test_document_decision_requires_historical_material_to_dominate() -> None:
    mixed = classify_document("これは現代文です。学而時習之、不亦説乎。もう一つ現代文です。")
    historical = classify_document("舊國體を論ず。敵艦を追ひつゝ港へ向つた。之れを可と爲す。")

    assert mixed.recommend_drop is False
    assert historical.recommend_drop is True
    assert historical.flagged_sentence_count == 3


def test_sentence_offsets_are_document_offsets() -> None:
    result = classify_document("現代文です。\n舊國體を論ず。")

    assert result.sentences[1].start == len("現代文です。\n")
    evidence = result.sentences[1].evidence[0]
    assert evidence.start >= result.sentences[1].start
