from __future__ import annotations

import re


# ASCII Roman-looking strings remain alphabetic. Unicode Roman numeral symbols
# and Japanese numeral digits are members of the numeric layer. Unit kanji such
# as 万 and 京 stay lexical because they are also ordinary words and names.
NUMERIC_ONLY_SURFACE_RE = re.compile(
    r"[0-9０-９ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅬⅭⅮⅯⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹⅺⅻⅼⅽⅾⅿ"
    r"〇零一二三四五六七八九]+"
)


def is_numeric_only_surface(surface: str) -> bool:
    return bool(surface and NUMERIC_ONLY_SURFACE_RE.fullmatch(surface))
