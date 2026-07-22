from yomi_corpus.yomi.numeric_surfaces import is_numeric_only_surface


def test_japanese_numeral_surface_is_numeric() -> None:
    assert is_numeric_only_surface("二〇〇二")
    assert is_numeric_only_surface("二九三")


def test_mixed_lexical_surface_is_not_numeric_only() -> None:
    assert not is_numeric_only_surface("二年")
    assert not is_numeric_only_surface("一人")
    assert not is_numeric_only_surface("聖飢魔Ⅱ")
    assert not is_numeric_only_surface("二千二")
    assert not is_numeric_only_surface("京")
    assert not is_numeric_only_surface("III")
