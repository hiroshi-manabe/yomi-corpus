from yomi_corpus.yomi.numeric_surfaces import (
    allows_optional_japanese_numeral_reading,
    is_formatted_arabic_number_surface,
    is_numeric_digit_surface,
    is_numeric_only_surface,
)


def test_formatted_arabic_number_surface_is_numeric() -> None:
    for surface in ("2,035.28", "-2.4", "+1,000", "＋１，０００．５"):
        assert is_formatted_arabic_number_surface(surface)
        assert is_numeric_only_surface(surface)
    for surface in ("2,03", "2.", "1,000kg", "--2"):
        assert not is_formatted_arabic_number_surface(surface)


def test_japanese_numeral_surface_is_numeric() -> None:
    assert is_numeric_only_surface("二〇〇二")
    assert is_numeric_only_surface("二九三")
    assert is_numeric_only_surface("二○二六")
    assert is_numeric_only_surface("一○")
    assert is_numeric_only_surface("〇")


def test_white_circle_placeholders_are_symbols() -> None:
    assert not is_numeric_only_surface("○")
    assert not is_numeric_only_surface("○○")
    assert not allows_optional_japanese_numeral_reading("○○")


def test_multi_character_japanese_numeral_reading_is_optional() -> None:
    for surface in ("一三", "一二三", "二〇〇二", "二○二六", "一○"):
        assert allows_optional_japanese_numeral_reading(surface)
    for surface in ("一", "〇", "13", "Ⅲ", "十三"):
        assert not allows_optional_japanese_numeral_reading(surface)


def test_single_lexical_japanese_numeral_keeps_ordinary_reading() -> None:
    assert is_numeric_digit_surface("七")
    assert not is_numeric_only_surface("七")
    assert not is_numeric_only_surface("零")


def test_mixed_lexical_surface_is_not_numeric_only() -> None:
    assert not is_numeric_only_surface("二年")
    assert not is_numeric_only_surface("一人")
    assert not is_numeric_only_surface("聖飢魔Ⅱ")
    assert not is_numeric_only_surface("二千二")
    assert not is_numeric_only_surface("二十五")
    assert not is_numeric_only_surface("京")
    assert not is_numeric_only_surface("III")
