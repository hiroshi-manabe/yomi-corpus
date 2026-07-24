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


def is_numeric_digit_surface(surface: str) -> bool:
    return bool(surface and NUMERIC_DIGIT_SURFACE_RE.fullmatch(surface))


def is_numeric_only_surface(surface: str) -> bool:
    if not is_numeric_digit_surface(surface):
        return False
    if not JAPANESE_NUMERAL_DIGIT_RE.fullmatch(surface):
        return True
    # Multi-character digit-style runs are delegated to the numeric layer.
    # Single lexical numerals retain their ordinary reading; circle zero is a
    # notation symbol and remains no-ruby even by itself.
    return len(surface) >= 2 or surface in {"〇", "○"}
