from __future__ import annotations

import re


# ASCII Roman-looking strings remain alphabetic. Unicode Roman numeral symbols
# and Japanese numeral digits are members of the numeric layer. Unit kanji such
# as 十, 百, 万, and 京 stay lexical because they require ordinary readings and
# can also be words or names.
NUMERIC_DIGIT_SURFACE_RE = re.compile(
    r"[0-9０-９ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅬⅭⅮⅯⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹⅺⅻⅼⅽⅾⅿ"
    r"〇○零一二三四五六七八九]+"
)
JAPANESE_NUMERAL_DIGIT_RE = re.compile(r"[〇○零一二三四五六七八九]+")
FORMATTED_ARABIC_NUMBER_RE = re.compile(
    r"[+＋\-－−]?(?:(?:[0-9０-９]{1,3}(?:[,，][0-9０-９]{3})+)|[0-9０-９]+)"
    r"(?:[.．][0-9０-９]+)?"
)


def is_numeric_digit_surface(surface: str) -> bool:
    return bool(surface and NUMERIC_DIGIT_SURFACE_RE.fullmatch(surface))


def is_numeric_only_surface(surface: str) -> bool:
    if is_formatted_arabic_number_surface(surface):
        return True
    if not is_numeric_digit_surface(surface):
        return False
    if not JAPANESE_NUMERAL_DIGIT_RE.fullmatch(surface):
        return True
    # A run made entirely of white circles is normally a redaction or
    # placeholder, not a number. A circle inside an otherwise numeric run,
    # such as 一○ or 二○二六, retains its zero-like numeric role.
    if set(surface) == {"○"}:
        return False
    # Multi-character digit-style runs are delegated to the numeric layer.
    # Single lexical numerals retain their ordinary reading; ideographic zero
    # remains no-ruby even by itself.
    return len(surface) >= 2 or surface == "〇"


def allows_optional_japanese_numeral_reading(surface: str) -> bool:
    """Return whether a digit-style kanji run may retain a lexical reading."""
    return bool(
        len(surface) >= 2
        and JAPANESE_NUMERAL_DIGIT_RE.fullmatch(surface)
        and set(surface) != {"○"}
    )


def is_formatted_arabic_number_surface(surface: str) -> bool:
    return bool(surface and FORMATTED_ARABIC_NUMBER_RE.fullmatch(surface))
