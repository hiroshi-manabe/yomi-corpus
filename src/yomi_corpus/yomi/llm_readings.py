from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from yomi_corpus.llm.rendering import (
    escape_source_parentheses,
    furigana_no_space_token_for_llm,
)
from yomi_corpus.yomi.ngram_diagnostics import (
    DEFAULT_DECODER_LEXICON_PATH,
    DEFAULT_RAW_SUDACHI_DICT_DIR,
    StableTwoKanjiChecker,
    sudachi_token_from_dict,
)
from yomi_corpus.yomi.strategies import span_sudachi_tokens
from yomi_corpus.yomi.furigana import FuriganaConverter, parse_annotated_chunks


KANJI_RE = re.compile(r"[\u3400-\u9fff々〆〻]")
LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")


@dataclass(frozen=True)
class YomiLLMReadingQueueSummary:
    read_units: int
    queued_items: int
    skipped_items: int
    stable_two_kanji_skipped: int
    output_jsonl: str
    summary_json: str


@dataclass(frozen=True)
class YomiLLMReadingApplySummary:
    read_units: int
    result_count: int
    checked_items: int
    matched_items: int
    mismatched_items: int
    parse_error_items: int
    missing_result_items: int
    output_jsonl: str
    summary_json: str


def build_yomi_llm_reading_queue_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    skip_stable_two_kanji: bool = True,
    skip_auto_accepted: bool = True,
    skip_scope_skipped: bool = True,
    raw_sudachi_dict_dir: Path = DEFAULT_RAW_SUDACHI_DICT_DIR,
) -> YomiLLMReadingQueueSummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    stable_checker = (
        StableTwoKanjiChecker(
            rows=[],
            decoder_lexicon_path=DEFAULT_DECODER_LEXICON_PATH,
            raw_sudachi_dict_dir=raw_sudachi_dict_dir,
        )
        if skip_stable_two_kanji
        else None
    )

    read_units = 0
    queued_items = 0
    skipped_items = 0
    stable_two_kanji_skipped = 0
    with input_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            if skip_scope_skipped and is_scope_skipped(unit):
                skipped_items += 1
                continue
            if skip_auto_accepted and is_yomi_auto_accepted(unit):
                skipped_items += 1
                continue
            for item in build_yomi_llm_reading_items(
                unit,
                stable_checker=stable_checker,
            ):
                if item.get("queue_status") == "queued":
                    dst.write(json.dumps(item, ensure_ascii=False) + "\n")
                    queued_items += 1
                else:
                    skipped_items += 1
                    if item.get("skip_reason") == "stable_two_kanji":
                        stable_two_kanji_skipped += 1

    summary = YomiLLMReadingQueueSummary(
        read_units=read_units,
        queued_items=queued_items,
        skipped_items=skipped_items,
        stable_two_kanji_skipped=stable_two_kanji_skipped,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def is_scope_skipped(unit: dict[str, Any]) -> bool:
    return (
        unit.get("analysis", {})
        .get("llm", {})
        .get("scope_triage", {})
        .get("status")
        == "Skip"
    )


def is_yomi_auto_accepted(unit: dict[str, Any]) -> bool:
    return bool(
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
        .get("auto_accept", {})
        .get("value")
    )


def build_yomi_llm_reading_items(
    unit: dict[str, Any],
    *,
    stable_checker: StableTwoKanjiChecker | None = None,
) -> list[dict[str, Any]]:
    text = str(unit.get("text", ""))
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    tokens = [
        sudachi_token_from_dict(token)
        for token in yomi.get("sudachi", {}).get("tokens", [])
    ]
    if not text or not tokens:
        return []

    try:
        spans = span_sudachi_tokens(text, tokens)
    except ValueError:
        return []

    items: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        token = span.token
        if not is_llm_reading_target(token.surface):
            continue
        if not token.reading:
            continue
        targets = target_reading_chunks(token.surface, token.reading)
        for chunk_index, target in enumerate(targets, start=1):
            item_id = f"{unit.get('unit_id', '')}:r{index + 1:04d}c{chunk_index:02d}"
            base_item = {
                "unit_id": str(unit.get("unit_id", "")),
                "item_id": item_id,
                "token_index": index,
                "chunk_index": chunk_index - 1,
                "surface": target["surface"],
                "token_surface": token.surface,
                "current_reading": hira_to_katakana(target["reading"]),
                "current_reading_hiragana": target["reading"],
                "text": text,
                "marked_text": mark_span(
                    text,
                    span.start + int(target["surface_start"]),
                    span.start + int(target["surface_end"]),
                ),
                "marked_furigana_text": marked_furigana_context(tokens, index, chunk_index - 1),
                "token_start": span.start,
                "token_end": span.end,
                "target_start": span.start + int(target["surface_start"]),
                "target_end": span.start + int(target["surface_end"]),
                "pos": token.pos,
                "dictionary_form": token.dictionary_form,
                "normalized_form": token.normalized_form,
            }
            if stable_checker is not None:
                stable = stable_checker.judge(base_item["surface"], base_item["current_reading"])
                if stable.value:
                    items.append(
                        {
                            **base_item,
                            "queue_status": "skipped",
                            "skip_reason": "stable_two_kanji",
                            "skip_detail": stable.reason,
                        }
                    )
                    continue
            items.append({**base_item, "queue_status": "queued"})
    return items


def apply_yomi_llm_reading_results_file(
    *,
    units_jsonl: Path,
    queue_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> YomiLLMReadingApplySummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    queue_items = load_queue_items(queue_jsonl)
    results = load_results(results_jsonl)
    by_unit: dict[str, list[dict[str, Any]]] = {}

    checked_items = 0
    matched_items = 0
    mismatched_items = 0
    parse_error_items = 0
    missing_result_items = 0
    for item_id, item in queue_items.items():
        result = results.get(item_id)
        judgment = build_item_judgment(item, result)
        checked_items += 1
        if judgment["status"] == "matched":
            matched_items += 1
        elif judgment["status"] == "mismatched":
            mismatched_items += 1
        elif judgment["status"] == "parse_error":
            parse_error_items += 1
        elif judgment["status"] == "missing_result":
            missing_result_items += 1
        by_unit.setdefault(str(item["unit_id"]), []).append(judgment)

    read_units = 0
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            judgments = sorted(
                by_unit.get(str(unit.get("unit_id", "")), []),
                key=lambda row: int(row.get("token_index", -1)),
            )
            unit.setdefault("analysis", {}).setdefault("llm", {})["yomi_readings"] = {
                "rule": "llm_reading_generation_sudachi_token_v1",
                "items": judgments,
                "all_matched": bool(judgments) and all(
                    judgment.get("status") == "matched" for judgment in judgments
                ),
                "checked_count": len(judgments),
            }
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")

    summary = YomiLLMReadingApplySummary(
        read_units=read_units,
        result_count=len(results),
        checked_items=checked_items,
        matched_items=matched_items,
        mismatched_items=mismatched_items,
        parse_error_items=parse_error_items,
        missing_result_items=missing_result_items,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_item_judgment(
    item: dict[str, Any],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "item_id": item["item_id"],
        "token_index": item["token_index"],
        "surface": item["surface"],
        "current_reading": item["current_reading"],
        "current_reading_hiragana": item["current_reading_hiragana"],
        "marked_text": item["marked_text"],
    }
    if result is None:
        return {**base, "status": "missing_result", "llm_reading": None, "raw_text": None}
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        return {
            **base,
            "status": "parse_error",
            "llm_reading": None,
            "raw_text": result.get("raw_text"),
            "parse_error": result.get("parse_error") or "Parsed result is not a JSON object.",
        }
    expected_key = str(item["surface"])
    keys = set(parsed)
    if keys != {expected_key}:
        return {
            **base,
            "status": "parse_error",
            "llm_reading": None,
            "raw_text": result.get("raw_text"),
            "parse_error": (
                f"Expected exactly one JSON key {expected_key!r}; "
                f"got {sorted(str(key) for key in keys)!r}."
            ),
        }
    raw_reading = parsed.get(str(item["surface"]))
    if not isinstance(raw_reading, str):
        return {
            **base,
            "status": "parse_error",
            "llm_reading": None,
            "raw_text": result.get("raw_text"),
            "parse_error": f"Reading for surface key {item['surface']!r} is not a string.",
        }
    llm_reading = normalize_hiragana_reading(raw_reading)
    current = normalize_hiragana_reading(str(item["current_reading_hiragana"]))
    return {
        **base,
        "status": "matched" if llm_reading == current else "mismatched",
        "llm_reading": llm_reading,
        "raw_text": result.get("raw_text"),
    }


def load_queue_items(path: Path) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return items
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = str(row.get("item_id", ""))
            if item_id:
                items[item_id] = row
    return items


def load_results(path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return results
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = str(row.get("item_id", ""))
            if item_id:
                results[item_id] = row
    return results


def is_llm_reading_target(surface: str) -> bool:
    return bool(KANJI_RE.search(surface) or LATIN_RE.search(surface))


def mark_span(text: str, start: int, end: int) -> str:
    return text[:start] + "**" + text[start:end] + "**" + text[end:]


def target_reading_chunks(surface: str, reading: str) -> list[dict[str, object]]:
    converter = FuriganaConverter()
    result = converter.convert(surface, reading)
    annotated = result.annotated_surface
    if not annotated:
        return [
            {
                "surface": surface,
                "reading": katakana_to_hiragana(reading),
                "surface_start": 0,
                "surface_end": len(surface),
            }
        ]
    chunks = []
    cursor = 0
    for chunk_surface, chunk_reading in parse_annotated_chunks(annotated):
        start = surface.find(chunk_surface, cursor)
        if start < 0:
            start = surface.find(chunk_surface)
        if start < 0:
            continue
        end = start + len(chunk_surface)
        chunks.append(
            {
                "surface": chunk_surface,
                "reading": katakana_to_hiragana(chunk_reading),
                "surface_start": start,
                "surface_end": end,
            }
        )
        cursor = end
    if chunks:
        return chunks
    return [
        {
            "surface": surface,
            "reading": katakana_to_hiragana(reading),
            "surface_start": 0,
            "surface_end": len(surface),
        }
    ]


def marked_furigana_context(tokens: list[Any], target_index: int, target_chunk_index: int) -> str:
    rendered: list[str] = []
    for index, token in enumerate(tokens):
        if index == target_index:
            rendered.append(marked_furigana_token(token.surface, token.reading, target_chunk_index))
            continue
        rendered.append(furigana_no_space_token_for_llm(f"{token.surface}/{token.reading}"))
    return "".join(rendered)


def marked_furigana_token(surface: str, reading: str, target_chunk_index: int) -> str:
    annotated = FuriganaConverter().convert(surface, reading).annotated_surface
    if not annotated:
        return f"**{escape_source_parentheses(surface)}**"
    output: list[str] = []
    cursor = 0
    chunk_index = 0
    pattern = re.compile(r"([^（）]+?)（([^（）]+)）")
    for match in pattern.finditer(annotated):
        prefix = annotated[cursor : match.start()]
        chunk_surface = trailing_kanji_run(match.group(1))
        if chunk_index == target_chunk_index and chunk_surface:
            non_kanji_prefix = match.group(1)[: -len(chunk_surface)]
            output.append(escape_source_parentheses(prefix + non_kanji_prefix))
            output.append(f"**{escape_source_parentheses(chunk_surface)}**")
        else:
            output.append(escape_source_parentheses(prefix))
            output.append(match.group(0))
        cursor = match.end()
        if chunk_surface:
            chunk_index += 1
    output.append(escape_source_parentheses(annotated[cursor:]))
    return "".join(output)


def trailing_kanji_run(text: str) -> str:
    match = re.search(r"[\u3400-\u9fff々〆〻]+$", text)
    return "" if match is None else match.group(0)


def katakana_to_hiragana(text: str) -> str:
    chars = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def hira_to_katakana(text: str) -> str:
    chars = []
    for char in text:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            chars.append(chr(code + 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def normalize_hiragana_reading(text: str) -> str:
    return katakana_to_hiragana(text.strip())
