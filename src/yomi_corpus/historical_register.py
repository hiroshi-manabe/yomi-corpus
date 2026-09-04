from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import re
from typing import Iterable

from yomi_corpus.splitter import split_text_into_units


CATEGORIES = ("historical_kana", "old_kanji", "kanbun")
DEFAULT_MIN_FLAGGED_SENTENCES = 2
DEFAULT_MIN_SENTENCE_RATIO = 0.4
DEFAULT_MIN_CHARACTER_RATIO = 0.35

# One occurrence is useful evidence. It is not sufficient by itself because some
# old forms remain common in names and quotations embedded in modern prose.
OLD_KANJI = {
    "亞", "惡", "壓", "圍", "爲", "醫", "壹", "榮", "衞", "驛", "圓", "鹽",
    "緣", "艷", "應", "奧", "橫", "溫", "穩", "假", "價", "畫", "會", "壞",
    "懷", "繪", "擴", "殼", "覺", "學", "樂", "渴", "卷", "勸", "寬", "歡",
    "觀", "關", "陷", "顏", "歸", "氣", "龜", "戲", "犧", "舊", "據", "擧",
    "峽", "挾", "狹", "曉", "區", "驅", "勳", "徑", "惠", "揭", "溪", "經",
    "繼", "莖", "螢", "輕", "鷄", "藝", "缺", "儉", "劍", "圈", "檢", "權",
    "獻", "縣", "險", "顯", "驗", "嚴", "效", "廣", "恆", "鑛", "號", "國",
    "黑", "濟", "碎", "劑", "册", "雜", "參", "慘", "棧", "蠶", "贊", "殘",
    "絲", "齒", "兒", "辭", "濕", "實", "舍", "寫", "釋", "壽", "收", "從",
    "澁", "獸", "縱", "肅", "處", "敍", "將", "燒", "稱", "證", "乘", "剩",
    "壤", "孃", "條", "淨", "狀", "疊", "穰", "讓", "釀", "觸", "寢", "愼",
    "眞", "盡", "圖", "醉", "隨", "髓", "數", "樞", "瀨", "聲", "靜", "齊",
    "攝", "竊", "專", "淺", "戰", "纖", "禪", "壯", "爭", "莊", "搜", "插",
    "巢", "曾", "裝", "騷", "增", "臟", "藏", "屬", "續", "墮", "體", "對",
    "帶", "滯", "臺", "瀧", "擇", "澤", "單", "膽", "團", "斷", "彈", "遲",
    "晝", "蟲", "鑄", "廳", "聽", "敕", "鎭", "塚", "遞", "鐵", "轉", "點",
    "傳", "黨", "盜", "燈", "當", "鬭", "德", "獨", "讀", "屆", "貳", "腦",
    "霸", "廢", "拜", "賣", "麥", "發", "髮", "拔", "蠻", "祕", "濱", "拂",
    "佛", "竝", "變", "邊", "辨", "瓣", "辯", "舖", "穗", "寶", "豐", "沒",
    "飜", "萬", "滿", "默", "餠", "藥", "譯", "豫", "餘", "與", "譽", "搖",
    "樣", "謠", "遙", "慾", "來", "覽", "龍", "兩", "獵", "綠", "鄰", "靈",
    "齡", "曆", "歷", "戀", "練", "鍊", "爐", "勞", "樓", "錄", "灣", "櫻",
}

# These are still productive in personal and place names. They count as weak
# evidence unless another historical-register signal is present.
NAME_PRONE_OLD_KANJI = {
    "櫻", "澤", "瀧", "濱", "邊", "龍", "國", "德", "惠", "眞", "齊", "穗",
    "塚", "來", "廣", "黑", "榮", "萬", "兒",
}

HISTORICAL_SPELLING_PATTERNS = (
    re.compile(r"(?:けふ|きのふ|てふ|でせう|ませう|であらう|なほ|猶ほ)"),
    re.compile(r"(?:云|言|謂|思|行|扱|從|従|伴|向|追|拂|払|拾|誘|醉|酔|迷|歌|買|負|問|祝|救|會|会|願|笑|使|習|吸|沿|倣|慕|厭|請|覆|叶|匂|漂|憂|報|酬|償|煩|勞|労)ふ"),
    re.compile(r"(?:あつ|なつ|云つ|言つ|思つ|行つ|向つ|入つ|乘つ|乗つ|取つ|歸つ|帰つ|知つ|持つ|立つ|至つ|殘つ|残つ|追つ|切つ|成つ|違つ|廻つ|解つ|寄つ|下つ|戻つ|終つ|作つ|掛つ|通つ)(?:た|て|と|たり)"),
    re.compile(r"(?:つ[ゝヽ]|る[ゝヽ]|れ[ゝヽ]|し[ゝヽ]|見へ|覺へ|覚へ|聞へ|越へ)"),
    re.compile(r"(?:ぢや|しやう|行かう|せらるゝ|ものゝ|ながらへ)"),
)

KANBUN_EXPLICIT_PATTERNS = (
    re.compile(r"(?:曰く|曰はく|謂へらく|訓へに曰く)"),
    re.compile(r"(?:之れ|是れ|其れ|吾れ|汝|豈に|乃ち|則ち|焉んぞ)"),
    re.compile(r"(?:無之|有之|候得共|候間|候儀|候處|候者|被[一-龯々]{1,4}候)"),
)
KANBUN_FUNCTION_CHARS = set("之也矣焉哉乎而於乃則豈兮者")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")


@dataclass(frozen=True)
class RegisterEvidence:
    category: str
    kind: str
    text: str
    start: int
    end: int
    weight: float


@dataclass(frozen=True)
class SentenceRegister:
    start: int
    end: int
    text: str
    labels: tuple[str, ...]
    confidence: str
    scores: dict[str, float]
    evidence: tuple[RegisterEvidence, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DocumentRegister:
    sentences: tuple[SentenceRegister, ...]
    sentence_count: int
    flagged_sentence_count: int
    flagged_sentence_ratio: float
    character_count: int
    flagged_character_count: int
    flagged_character_ratio: float
    category_sentence_counts: dict[str, int]
    recommend_drop: bool
    drop_reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def classify_sentence(text: str, *, start: int = 0) -> SentenceRegister:
    evidence: list[RegisterEvidence] = []

    for match in re.finditer(r"[ゐゑヰヱ]", text):
        evidence.append(_evidence("historical_kana", "archaic_kana", match, start, 1.5))
    for pattern in HISTORICAL_SPELLING_PATTERNS:
        for match in pattern.finditer(text):
            evidence.append(_evidence("historical_kana", "historical_spelling", match, start, 1.05))

    old_positions = [(index, char) for index, char in enumerate(text) if char in OLD_KANJI]
    for index, char in old_positions:
        weight = 0.25 if char in NAME_PRONE_OLD_KANJI else 0.6
        evidence.append(
            RegisterEvidence("old_kanji", "old_character", char, start + index, start + index + 1, weight)
        )

    for pattern in KANBUN_EXPLICIT_PATTERNS:
        for match in pattern.finditer(text):
            evidence.append(_evidence("kanbun", "kanbun_syntax", match, start, 1.05))

    visible = [char for char in text if not char.isspace()]
    han_count = sum(bool(HAN_RE.fullmatch(char)) for char in visible)
    kana_count = sum(bool(KANA_RE.fullmatch(char)) for char in visible)
    function_count = sum(char in KANBUN_FUNCTION_CHARS for char in visible)
    if len(visible) >= 8 and han_count / len(visible) >= 0.72 and kana_count / len(visible) <= 0.12:
        weight = 0.7 + min(0.6, function_count * 0.15)
        evidence.append(RegisterEvidence("kanbun", "han_dense", text, start, start + len(text), weight))

    scores = Counter[str]()
    for item in evidence:
        scores[item.category] += item.weight
    if len(old_positions) >= 2 and len({char for _, char in old_positions}) >= 2:
        scores["old_kanji"] += 0.45
    if scores["old_kanji"] and (scores["historical_kana"] or scores["kanbun"]):
        scores["old_kanji"] += 0.25

    rounded = {category: round(scores[category], 3) for category in CATEGORIES}
    labels = tuple(category for category in CATEGORIES if rounded[category] >= 1.0)
    top_score = max(rounded.values(), default=0.0)
    confidence = "high" if top_score >= 2.0 else "medium" if top_score >= 1.0 else "low"
    return SentenceRegister(
        start=start,
        end=start + len(text),
        text=text,
        labels=labels,
        confidence=confidence,
        scores=rounded,
        evidence=tuple(evidence),
    )


def classify_document(
    text: str,
    *,
    min_flagged_sentences: int = DEFAULT_MIN_FLAGGED_SENTENCES,
    min_sentence_ratio: float = DEFAULT_MIN_SENTENCE_RATIO,
    min_character_ratio: float = DEFAULT_MIN_CHARACTER_RATIO,
) -> DocumentRegister:
    sentences = tuple(
        classify_sentence(span.text, start=span.start)
        for span in split_text_into_units(text)
    )
    flagged = [sentence for sentence in sentences if sentence.labels]
    character_count = sum(_visible_length(sentence.text) for sentence in sentences)
    flagged_characters = sum(_visible_length(sentence.text) for sentence in flagged)
    sentence_ratio = len(flagged) / len(sentences) if sentences else 0.0
    character_ratio = flagged_characters / character_count if character_count else 0.0
    category_counts = {
        category: sum(category in sentence.labels for sentence in sentences)
        for category in CATEGORIES
    }
    reasons: list[str] = []
    if len(flagged) >= min_flagged_sentences and sentence_ratio >= min_sentence_ratio:
        reasons.append("flagged_sentence_ratio")
    if len(flagged) >= min_flagged_sentences and character_ratio >= min_character_ratio:
        reasons.append("flagged_character_ratio")
    recommend_drop = len(reasons) == 2
    return DocumentRegister(
        sentences=sentences,
        sentence_count=len(sentences),
        flagged_sentence_count=len(flagged),
        flagged_sentence_ratio=round(sentence_ratio, 6),
        character_count=character_count,
        flagged_character_count=flagged_characters,
        flagged_character_ratio=round(character_ratio, 6),
        category_sentence_counts=category_counts,
        recommend_drop=recommend_drop,
        drop_reasons=tuple(reasons),
    )


def summarize_documents(documents: Iterable[DocumentRegister]) -> dict:
    rows = list(documents)
    category_counts = {
        category: sum(document.category_sentence_counts[category] for document in rows)
        for category in CATEGORIES
    }
    return {
        "document_count": len(rows),
        "recommended_drop_count": sum(document.recommend_drop for document in rows),
        "sentence_count": sum(document.sentence_count for document in rows),
        "flagged_sentence_count": sum(document.flagged_sentence_count for document in rows),
        "category_sentence_counts": category_counts,
    }


def _evidence(category: str, kind: str, match: re.Match[str], offset: int, weight: float) -> RegisterEvidence:
    return RegisterEvidence(category, kind, match.group(), offset + match.start(), offset + match.end(), weight)


def _visible_length(text: str) -> int:
    return sum(not char.isspace() for char in text)
