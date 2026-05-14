#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


DEFAULT_SOURCE = Path("data/units/batch_0001/units.yomi.aligned_hybrid.jsonl")
DEFAULT_OUTPUT = Path("data/evals/yomi_triage/ok_review_skip_draft_v1.jsonl")
DEFAULT_SUMMARY = Path("data/evals/yomi_triage/ok_review_skip_draft_v1.summary.json")
DEFAULT_SKIP_SOURCE_DIR = Path("data/evals/yomi_triage/raw_skip_sources")

LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")
DIGIT_RE = re.compile(r"[0-9０-９]")
TRAILING_COUNT_RE = re.compile(r"\s+\d+\s*$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")
CHINESE_ONLY_HINTS = set(
    "这们为过没样时说实发个对见请从号后里吗谁么开关于"
    "办证书毕毕业绩成绩单凭历认认证微澳洲荷兰阿姆斯特丹"
    "廣播線氣電視體聽寫"
)
OLD_KANA_HINTS = set("ゐゑヰヱ")
KANBUN_HINTS = set("之爲為無以故其於者乎也而天下聖曰謂焉")
OLD_KANJI_HINTS = set("醫舊舎臺體國學會號圓當處實氣發讀廣應戰藝價")
MODERN_FRAME_PATTERNS = [
    "です",
    "ます",
    "ました",
    "でした",
    "されています",
    "されたもの",
    "したもの",
    "ございます",
    "ございました",
    "本書は",
    "刊行された",
    "連載した",
    "著",
    "書店",
    "原材料",
    "株式会社",
    "特徴",
    "活動",
]
MODERN_INCIDENTAL_NON_TARGET_SUBSTRINGS = [
    "東京掃苔録",
    "東都掃苔記",
    "日本醫事新報",
    "黄飛鴻",
    "中国問題",
    "原材料",
    "株式会社",
    "ロータリーの樂器",
    "御帰りあそばされる",
    "寛永二十一年",
]
HARD_OLD_KANA_PATTERNS = [
    "思ひ",
    "言ふ",
    "思ふ",
    "やう",
    "さう",
    "だらう",
    "つて",
    "つた",
    "靈",
    "國",
    "樂",
    "實",
    "氣",
]

REVIEW_PATTERNS: list[tuple[str, str]] = [
    ("若しくは/モシクワ", "Known orthographic reading issue: モシクワ should be モシクハ."),
    ("古/コ 本屋/ホンヤ", "Wrong split/reading for 古本屋; should be treated as a repair case."),
    ("給料/キュウリョウ 日直/ニッチョク 後/ゴ", "Wrong split/reading for 給料日直後."),
    ("均/ヒトシ", "Wrong reading in 百均 context."),
    ("様々/サマザマ な/ナ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("多く/オオク の/ノ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("子供/コドモ （/（ 小学生/ショウガクセイ まで/マデ ）/） の/ノ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("大好き/ダイスキ な/ナ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("ご利用/ゴリヨウ 予定/ヨテイ の/ノ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("名/メイ の/ノ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("人/ニン の/ノ 方/ホウ", "Likely person-sense 方; should be カタ."),
    ("お/オ 持ち/モチ の/ノ 方/ホウ", "Likely person-sense 方; should be カタ."),
]

SUSPICIOUS_OK_PATTERNS = [
    pattern for pattern, _ in REVIEW_PATTERNS
] + [
    "モシクワ",
    "ミジカ",
    "ガン/ガン ビア/ビア",
    "マジ/マジ シャン/シャン",
    "/ /",
]

SYNTHETIC_REVIEW_REPLACEMENTS: list[tuple[str, str, str]] = [
    ("学校/ガッコウ", "学校/ガクコウ", "Synthetic wrong on-yomi-like reading for 学校."),
    ("今日/キョウ", "今日/コンニチ", "Synthetic contextually wrong reading for 今日."),
    ("人/ヒト", "人/ジン", "Synthetic wrong reading for standalone 人."),
    ("時/トキ", "時/ジ", "Synthetic wrong reading for standalone 時."),
    ("中/ナカ", "中/チュウ", "Synthetic wrong reading for standalone 中."),
    ("上/ウエ", "上/ジョウ", "Synthetic wrong reading for standalone 上."),
    ("下/シタ", "下/カ", "Synthetic wrong reading for standalone 下."),
    ("家/イエ", "家/ケ", "Synthetic wrong reading for standalone 家."),
    ("行っ/イッ", "行っ/オコナッ", "Synthetic wrong reading for 行く."),
    ("入っ/ハイッ", "入っ/ニュウッ", "Synthetic wrong reading for 入る."),
    ("出/デ", "出/シュツ", "Synthetic wrong reading for 出る."),
    ("大き/オオキ", "大き/ダイキ", "Synthetic wrong reading for 大きい."),
    ("小さ/チイサ", "小さ/ショウサ", "Synthetic wrong reading for 小さい."),
    ("見る/ミル", "見る/ケンル", "Synthetic wrong on-yomi-like reading for 見る."),
    ("聞く/キク", "聞く/ブンル", "Synthetic wrong on-yomi-like reading for 聞く."),
]

AMBIGUITY_EXAMPLES: list[dict[str, str]] = [
    {
        "unit_id": "targeted_ambiguity:unresolved:0001",
        "text": "ものすごく辛かったんじゃないかな。",
        "rendered": "ものすごく/モノスゴク 辛かっ/ツラカッ た/タ ん/ン じゃ/ジャ ない/ナイ か/カ な/ナ 。/。",
        "expected_status": "Review",
        "label_source": "targeted_unresolved_context_ambiguity",
        "note": "Review-needed ambiguity: surrounding context is substantial enough to be nontrivial, but does not safely decide ツライ vs カライ.",
    },
    {
        "unit_id": "targeted_ambiguity:unresolved:0002",
        "text": "思っていたより辛いですね。",
        "rendered": "思っ/オモッ て/テ い/イ た/タ より/ヨリ 辛い/ツライ です/デス ね/ネ 。/。",
        "expected_status": "Review",
        "label_source": "targeted_unresolved_context_ambiguity",
        "note": "Review-needed ambiguity: the sentence gives comparison context, but not enough to decide ツライ vs カライ.",
    },
    {
        "unit_id": "targeted_ambiguity:unresolved:0003",
        "text": "昨日のあれは本当に辛かったと思う。",
        "rendered": "昨日/キノウ の/ノ あれ/アレ は/ハ 本当/ホントウ に/ニ 辛かっ/ツラカッ た/タ と/ト 思う/オモウ 。/。",
        "expected_status": "Review",
        "label_source": "targeted_unresolved_context_ambiguity",
        "note": "Review-needed ambiguity: deictic context makes the reading hard; the unit should remain reviewable.",
    },
    {
        "unit_id": "targeted_ambiguity:resolved:0001",
        "text": "あとから聞くと、本人も辛かったんじゃないかな。",
        "rendered": "あと/アト から/カラ 聞く/キク と/ト 、/、 本人/ホンニン も/モ 辛かっ/ツラカッ た/タ ん/ン じゃ/ジャ ない/ナイ か/カ な/ナ 。/。",
        "expected_status": "OK",
        "label_source": "targeted_context_resolved_ambiguity_ok",
        "note": "Context strongly supports ツライ; カライ is only contrived, so the current reading should be accepted.",
    },
    {
        "unit_id": "targeted_ambiguity:resolved:0002",
        "text": "本人はかなり辛い思いをしたはずです。",
        "rendered": "本人/ホンニン は/ハ かなり/カナリ 辛い/ツライ 思い/オモイ を/ヲ し/シ た/タ はず/ハズ です/デス 。/。",
        "expected_status": "OK",
        "label_source": "targeted_context_resolved_ambiguity_ok",
        "note": "Context resolves 辛い as ツライ through 辛い思い; the current reading should be accepted.",
    },
    {
        "unit_id": "targeted_ambiguity:acceptable_variant:0001",
        "text": "日本代表として素晴らしい結果を残しました。",
        "rendered": "日本/ニッポン 代表/ダイヒョウ と/ト し/シ て/テ 素晴らしい/スバラシイ 結果/ケッカ を/ヲ 残し/ノコシ まし/マシ た/タ 。/。",
        "expected_status": "OK",
        "label_source": "targeted_inherently_acceptable_variant",
        "note": "Acceptable variant: 日本/ニッポン should not be treated as a repair target solely because ニホン is also possible.",
    },
    {
        "unit_id": "targeted_ambiguity:acceptable_variant:0002",
        "text": "私は知りませんでした。",
        "rendered": "私/ワタクシ は/ハ 知り/シリ ませ/マセ ん/ン でし/デシ た/タ 。/。",
        "expected_status": "OK",
        "label_source": "targeted_inherently_acceptable_variant",
        "note": "Acceptable variant: 私/ワタクシ should not be nitpicked at triage even when ワタシ is common.",
    },
    {
        "unit_id": "targeted_ambiguity:acceptable_variant:0003",
        "text": "私と旦那くんは同じ会社です。",
        "rendered": "私/ワタクシ と/ト 旦那/ダンナ くん/クン は/ハ 同じ/オナジ 会社/カイシャ です/デス 。/。",
        "expected_status": "OK",
        "label_source": "targeted_inherently_acceptable_variant",
        "note": "Acceptable variant in a casual sentence: the triage model should not force 私 to ワタシ.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a draft OK/Review/Skip yomi triage eval set from early corpus output."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--ok-count", type=int, default=200)
    parser.add_argument("--synthetic-review-count", type=int, default=50)
    parser.add_argument("--skip-source-dir", type=Path, default=DEFAULT_SKIP_SOURCE_DIR)
    parser.add_argument("--skip-count-per-source", type=int, default=20)
    parser.add_argument("--config", default="config/yomi/default.toml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_jsonl(args.source)

    review_rows = collect_review_rows(rows, args.source)
    review_ids = {row["unit_id"] for row in review_rows}
    ok_rows = collect_ok_rows(rows, args.source, args.ok_count, review_ids)
    synthetic_review_rows = collect_synthetic_review_rows(
        rows,
        args.source,
        args.synthetic_review_count,
        {row["unit_id"] for row in ok_rows} | {row["unit_id"] for row in review_rows},
    )
    skip_rows = collect_skip_rows(
        args.skip_source_dir,
        args.skip_count_per_source,
        args.config,
    )
    ambiguity_rows = collect_ambiguity_rows()
    output_rows = ok_rows + review_rows + synthetic_review_rows + skip_rows + ambiguity_rows

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "source": str(args.source),
        "output": str(args.output),
        "row_count": len(output_rows),
        "ok_count": len(ok_rows),
        "review_count": len(review_rows) + len(synthetic_review_rows),
        "natural_review_count": len(review_rows),
        "synthetic_review_count": len(synthetic_review_rows),
        "skip_count": len(skip_rows),
        "targeted_ambiguity_count": len(ambiguity_rows),
        "status_counts": count_by(output_rows, "expected_status"),
        "label_source_counts": count_by(output_rows, "label_source"),
        "notes": [
            "Draft dataset for yomi_triage prompt optimization.",
            "OK/Review rows are drawn from early corpus output.",
            "Skip rows are sampled from raw skip-source text files with a preference for hard mixed cases.",
            "Review rows are known or high-confidence suspected cases requiring review, not a final human-reviewed gold set.",
            "Synthetic Review rows use real early-corpus text with one injected bad yomi annotation.",
            "Targeted ambiguity rows test review-needed ambiguity versus acceptable unresolved variants.",
        ],
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def collect_review_rows(rows: list[dict[str, Any]], source: Path) -> list[dict[str, Any]]:
    notes_by_unit: dict[str, list[str]] = defaultdict(list)
    rows_by_unit: dict[str, dict[str, Any]] = {}
    for row in rows:
        rendered = yomi(row).get("rendered", "")
        for pattern, note in REVIEW_PATTERNS:
            if pattern in rendered:
                unit_id = row["unit_id"]
                rows_by_unit[unit_id] = row
                notes_by_unit[unit_id].append(note)

    eval_rows: list[dict[str, Any]] = []
    for unit_id in sorted(rows_by_unit, key=source_sort_key(rows_by_unit)):
        eval_rows.append(
            build_eval_row(
                rows_by_unit[unit_id],
                source,
                expected_status="Review",
                label_source="known_or_suspected_mechanical_error",
                note=" ".join(dict.fromkeys(notes_by_unit[unit_id])),
            )
        )
    return eval_rows


def collect_ok_rows(
    rows: list[dict[str, Any]],
    source: Path,
    ok_count: int,
    excluded_unit_ids: set[str],
) -> list[dict[str, Any]]:
    ok_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["unit_id"] in excluded_unit_ids:
            continue
        text = row.get("text", "")
        rendered = yomi(row).get("rendered", "")
        signals = set(yomi(row).get("signals", []))
        sudachi_rendered = (yomi(row).get("sudachi") or {}).get("rendered")
        if len(text) < 8 or len(text) > 90:
            continue
        if LATIN_RE.search(text) or DIGIT_RE.search(text):
            continue
        if any(pattern in rendered for pattern in SUSPICIOUS_OK_PATTERNS):
            continue
        if "sudachi_decoder_exact_token_agreement" not in signals:
            continue
        if rendered != sudachi_rendered:
            continue
        ok_rows.append(
            build_eval_row(
                row,
                source,
                expected_status="OK",
                label_source="heuristic_ok_sudachi_decoder_exact_agreement",
                note=(
                    "Draft OK: Sudachi and n-gram decoder agree exactly; "
                    "no Latin/digit text and no known suspicious yomi pattern."
                ),
            )
        )
        if len(ok_rows) >= ok_count:
            break
    return ok_rows


def collect_synthetic_review_rows(
    rows: list[dict[str, Any]],
    source: Path,
    synthetic_review_count: int,
    excluded_unit_ids: set[str],
) -> list[dict[str, Any]]:
    eval_rows: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    for row in rows:
        unit_id = row["unit_id"]
        if unit_id in excluded_unit_ids:
            continue
        text = row.get("text", "")
        if text in seen_texts:
            continue
        if len(text) < 8 or len(text) > 110:
            continue
        if LATIN_RE.search(text) or DIGIT_RE.search(text):
            continue
        yomi_data = yomi(row)
        rendered = yomi_data.get("rendered", "")
        signals = set(yomi_data.get("signals", []))
        sudachi_rendered = (yomi_data.get("sudachi") or {}).get("rendered")
        if "sudachi_decoder_exact_token_agreement" not in signals:
            continue
        if rendered != sudachi_rendered:
            continue
        if any(pattern in rendered for pattern in SUSPICIOUS_OK_PATTERNS):
            continue
        for original, replacement, replacement_note in SYNTHETIC_REVIEW_REPLACEMENTS:
            if original not in rendered:
                continue
            mutated = rendered.replace(original, replacement, 1)
            synthetic = build_eval_row(
                row,
                source,
                expected_status="Review",
                label_source="synthetic_bad_reading_injected",
                note=(
                    f"{replacement_note} Original annotation had {original}; "
                    f"the eval row intentionally changes it to {replacement}."
                ),
            )
            synthetic["unit_id"] = f"{unit_id}:synthetic_review:{len(eval_rows) + 1:03d}"
            synthetic["rendered"] = mutated
            synthetic["synthetic_source_unit_id"] = unit_id
            synthetic["synthetic_original"] = original
            synthetic["synthetic_replacement"] = replacement
            eval_rows.append(synthetic)
            seen_texts.add(text)
            break
        if len(eval_rows) >= synthetic_review_count:
            break
    return eval_rows


def collect_ambiguity_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, example in enumerate(AMBIGUITY_EXAMPLES, start=1):
        rows.append(
            {
                "unit_id": example["unit_id"],
                "doc_id": "targeted_ambiguity",
                "source_line_no": None,
                "unit_seq": index,
                "text": example["text"],
                "rendered": example["rendered"],
                "expected_status": example["expected_status"],
                "label_source": example["label_source"],
                "note": example["note"],
                "source_artifact": "manually curated corpus-inspired targeted ambiguity examples",
            }
        )
    return rows


def collect_skip_rows(
    skip_source_dir: Path,
    skip_count_per_source: int,
    config_path: str,
) -> list[dict[str, Any]]:
    if not skip_source_dir.exists():
        return []
    config = load_yomi_generation_config(config_path)
    rows: list[dict[str, Any]] = []
    for source_path in sorted(skip_source_dir.glob("*.txt")):
        source_name = source_path.stem
        snippets = sample_skip_snippets(source_path, source_name, skip_count_per_source)
        for index, snippet in enumerate(snippets, start=1):
            rendered = generate_mechanical_yomi(
                snippet.text,
                config=config,
                strategy_name=config.default_strategy,
            ).rendered
            rows.append(
                {
                    "unit_id": f"skip:{source_name}:{index:04d}",
                    "doc_id": f"skip:{source_name}",
                    "source_line_no": snippet.source_line_no,
                    "unit_seq": index,
                    "text": snippet.text,
                    "rendered": rendered,
                    "expected_status": "Skip",
                    "label_source": "hard_raw_skip_source_sample",
                    "note": (
                        f"Draft hard Skip candidate sampled from {source_path}; "
                        "selected to avoid trivial mechanical-only cues where possible. "
                        "Review before final gold."
                    ),
                    "source_artifact": str(source_path),
                    "source_kind": source_name,
                }
            )
    return rows


class SkipSnippet:
    def __init__(self, *, text: str, source_line_no: int, score: int) -> None:
        self.text = text
        self.source_line_no = source_line_no
        self.score = score


def sample_skip_snippets(path: Path, source_name: str, limit: int) -> list[SkipSnippet]:
    candidates: list[SkipSnippet] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = TRAILING_COUNT_RE.sub("", line.strip())
            if not text:
                continue
            for chunk in split_candidate_text(text):
                score = score_skip_candidate(chunk, source_name)
                if score <= 0:
                    continue
                candidates.append(
                    SkipSnippet(text=chunk, source_line_no=line_no, score=score)
                )
    candidates.sort(key=lambda item: (-item.score, item.source_line_no, len(item.text), item.text))
    selected: list[SkipSnippet] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.text
        if key in seen:
            continue
        selected.append(candidate)
        seen.add(key)
        if len(selected) >= limit:
            break
    return selected


def split_candidate_text(text: str) -> list[str]:
    chunks: list[str] = []
    for part in SENTENCE_SPLIT_RE.split(text):
        part = normalize_space(part)
        if not part:
            continue
        if 24 <= len(part) <= 180:
            chunks.append(part)
        elif len(part) > 180:
            chunks.extend(window_text(part, size=150, step=110))
    return chunks


def window_text(text: str, *, size: int, step: int) -> list[str]:
    chunks: list[str] = []
    for start in range(0, len(text), step):
        part = normalize_space(text[start:start + size])
        if len(part) >= 24:
            chunks.append(part)
        if start + size >= len(text):
            break
    return chunks


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def score_skip_candidate(text: str, source_name: str) -> int:
    if any(pattern in text for pattern in MODERN_INCIDENTAL_NON_TARGET_SUBSTRINGS):
        return -1
    if source_name == "kana":
        if any(char in text for char in OLD_KANA_HINTS):
            return -1
        pattern_score = sum(3 for pattern in HARD_OLD_KANA_PATTERNS if pattern in text)
        return pattern_score if pattern_score else -1
    if source_name == "zh":
        kana_count = sum(1 for char in text if "\u3040" <= char <= "\u30ff")
        chinese_hint_score = sum(3 for char in text if char in CHINESE_ONLY_HINTS)
        if kana_count < 3 or chinese_hint_score < 12:
            return -1
        return chinese_hint_score + min(kana_count, 20)
    if source_name == "kanji":
        if text.count(" ") >= 5 or text.count("/") >= 3:
            return -1
        kanji_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        kana_count = sum(1 for char in text if "\u3040" <= char <= "\u30ff")
        hint_score = sum(5 for char in text if char in KANBUN_HINTS)
        chinese_hint_score = sum(4 for char in text if char in CHINESE_ONLY_HINTS)
        old_kana_score = sum(4 for pattern in HARD_OLD_KANA_PATTERNS if pattern in text)
        old_kanji_score = sum(2 for char in text if char in OLD_KANJI_HINTS)
        modern_frame_score = sum(6 for pattern in MODERN_FRAME_PATTERNS if pattern in text)
        if modern_frame_score and (old_kana_score + hint_score + chinese_hint_score) < modern_frame_score:
            return -1
        if hint_score == 0 or kana_count == 0:
            return -1
        return (
            hint_score
            + chinese_hint_score
            + old_kana_score
            + old_kanji_score
            + min(kanji_count, 80)
            + min(kana_count, 20)
            - modern_frame_score
        )
    return 1


def build_eval_row(
    row: dict[str, Any],
    source: Path,
    *,
    expected_status: str,
    label_source: str,
    note: str,
) -> dict[str, Any]:
    yomi_data = yomi(row)
    return {
        "unit_id": row["unit_id"],
        "doc_id": row.get("doc_id"),
        "source_line_no": row.get("source_line_no"),
        "unit_seq": row.get("unit_seq"),
        "text": row.get("text", ""),
        "rendered": yomi_data.get("rendered", ""),
        "expected_status": expected_status,
        "label_source": label_source,
        "note": note,
        "source_artifact": str(source),
    }


def yomi(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("analysis", {}).get("mechanical", {}).get("yomi", {})


def source_sort_key(rows_by_unit: dict[str, dict[str, Any]]):
    def key(unit_id: str) -> tuple[int, int, str]:
        row = rows_by_unit[unit_id]
        return (
            int(row.get("source_line_no") or 0),
            int(row.get("unit_seq") or 0),
            unit_id,
        )

    return key


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, ""))
        counts[value] = counts.get(value, 0) + 1
    return counts


if __name__ == "__main__":
    main()
