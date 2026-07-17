from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from yomi_corpus.llm.parsers import parse_output
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
LAUGHTER_W_RE = re.compile(r"[wｗ]+")


@dataclass(frozen=True)
class YomiLLMReadingQueueSummary:
    read_units: int
    queued_items: int
    skipped_items: int
    stable_two_kanji_skipped: int
    safety_skipped: int
    output_jsonl: str
    summary_json: str


@dataclass(frozen=True)
class YomiLLMReadingRetryQueueSummary:
    read_items: int
    retry_items: int
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
    safety_skipped = 0
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
            safe_item_ids = safe_yomi_item_ids(unit)
            for item in build_yomi_llm_reading_items(
                unit,
                stable_checker=stable_checker,
            ):
                if item["item_id"] in safe_item_ids:
                    skipped_items += 1
                    safety_skipped += 1
                    continue
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
        safety_skipped=safety_skipped,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def build_yomi_llm_reading_retry_queue_file(
    *,
    queue_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    attempt: int = 2,
) -> YomiLLMReadingRetryQueueSummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    queue_items = load_queue_items(queue_jsonl)
    results = load_results(results_jsonl)

    read_items = 0
    retry_items = 0
    with output_jsonl.open("w", encoding="utf-8") as dst:
        for item_id, item in queue_items.items():
            read_items += 1
            judgment = build_item_judgment(item, results.get(item_id))
            if judgment.get("status") != "parse_error":
                continue
            retry_item = {
                **item,
                "retry_of": item_id,
                "attempt": attempt,
                "retry_reason": judgment.get("parse_error", ""),
                "retry_raw_text": judgment.get("raw_text"),
            }
            dst.write(json.dumps(retry_item, ensure_ascii=False) + "\n")
            retry_items += 1

    summary = YomiLLMReadingRetryQueueSummary(
        read_items=read_items,
        retry_items=retry_items,
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


def safe_yomi_item_ids(unit: dict[str, Any]) -> set[str]:
    targets = (
        unit.get("analysis", {})
        .get("safety", {})
        .get("yomi", {})
        .get("targets", [])
    )
    if not isinstance(targets, list):
        return set()
    return {
        str(record["item_id"])
        for record in targets
        if isinstance(record, dict) and record.get("is_safe") and record.get("item_id")
    }


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

    rendered_readings = rendered_readings_by_token_index(yomi, tokens)
    items: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        token = span.token
        if not is_llm_reading_target(token.surface):
            continue
        current_reading = rendered_readings.get(index, token.reading)
        if not current_reading:
            continue
        targets = target_reading_chunks(token.surface, current_reading)
        for chunk_index, target in enumerate(targets, start=1):
            item_id = f"{unit.get('unit_id', '')}:r{index + 1:04d}c{chunk_index:02d}"
            base_item = {
                "unit_id": str(unit.get("unit_id", "")),
                "item_id": item_id,
                "token_index": index,
                "chunk_index": chunk_index - 1,
                "surface": target["surface"],
                "token_surface": token.surface,
                "token_current_reading": hira_to_katakana(current_reading),
                "current_reading": hira_to_katakana(target["reading"]),
                "current_reading_hiragana": target["reading"],
                "text": text,
                "marked_text": mark_span(
                    text,
                    span.start + int(target["surface_start"]),
                    span.start + int(target["surface_end"]),
                ),
                "marked_furigana_text": marked_furigana_context(
                    tokens,
                    index,
                    chunk_index - 1,
                    token_readings=rendered_readings,
                ),
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


def rendered_readings_by_token_index(yomi: dict[str, Any], tokens: list[Any]) -> dict[int, str]:
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return {}
    pairs = parse_rendered_pairs(rendered)
    if len(pairs) != len(tokens):
        return {}
    readings: dict[int, str] = {}
    for index, ((surface, reading), token) in enumerate(zip(pairs, tokens, strict=True)):
        if surface == token.surface and reading:
            readings[index] = reading
    return readings


def parse_rendered_pairs(rendered: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for token in rendered.split():
        if "/" not in token:
            pairs.append((token, ""))
            continue
        surface, reading = token.rsplit("/", 1)
        pairs.append((surface, reading))
    return pairs


def apply_yomi_llm_reading_results_file(
    *,
    units_jsonl: Path,
    queue_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    retry_results_jsonl: Path | None = None,
    retry_results_jsonls: list[Path] | None = None,
) -> YomiLLMReadingApplySummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    queue_items = load_queue_items(queue_jsonl)
    results = load_results(results_jsonl)
    if retry_results_jsonl is not None:
        results.update(load_results(retry_results_jsonl))
    for retry_path in retry_results_jsonls or []:
        results.update(load_results(retry_path))
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
            merge_yomi_reading_judgments_into_safety(unit, judgments)
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
        "unit_id": item["unit_id"],
        "item_id": item["item_id"],
        "token_index": item["token_index"],
        "chunk_index": item["chunk_index"],
        "surface": item["surface"],
        "token_surface": item["token_surface"],
        "current_reading": item["current_reading"],
        "current_reading_hiragana": item["current_reading_hiragana"],
        "marked_text": item["marked_text"],
        "target_start": item["target_start"],
        "target_end": item["target_end"],
    }
    if result is None:
        return {**base, "status": "missing_result", "llm_reading": None, "raw_text": None}
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        try:
            parsed = parse_output(
                str(result.get("raw_text") or ""),
                "yomi_reading_completion_json",
                metadata={"surface": item["surface"], "source_row": item},
            )
        except Exception as exc:  # noqa: BLE001
            return {
                **base,
                "status": "parse_error",
                "llm_reading": None,
                "raw_text": result.get("raw_text"),
                "parse_error": result.get("parse_error") or str(exc),
            }
    expected_key = str(item["surface"])
    keys = set(parsed)
    if expected_key not in keys:
        return {
            **base,
            "status": "parse_error",
            "llm_reading": None,
            "raw_text": result.get("raw_text"),
            "parse_error": (
                f"Expected JSON key {expected_key!r}; "
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
    if not is_valid_yomi_reading(llm_reading):
        return {
            **base,
            "status": "parse_error",
            "llm_reading": None,
            "raw_text": result.get("raw_text"),
            "parse_error": (
                f"Reading for surface key {item['surface']!r} is not kana: "
                f"{raw_reading!r}."
            ),
        }
    current = normalize_hiragana_reading(str(item["current_reading_hiragana"]))
    return {
        **base,
        "status": "matched" if llm_reading == current else "mismatched",
        "llm_reading": llm_reading,
        "raw_text": result.get("raw_text"),
        "extra_json_keys": sorted(str(key) for key in keys - {expected_key}),
    }


def merge_yomi_reading_judgments_into_safety(
    unit: dict[str, Any],
    judgments: list[dict[str, Any]],
) -> None:
    if not judgments:
        refresh_yomi_safety_summary(unit)
        return

    safety = unit.setdefault("analysis", {}).setdefault("safety", {}).setdefault("yomi", {})
    targets = safety.setdefault("targets", [])
    if not isinstance(targets, list):
        targets = []
        safety["targets"] = targets
    by_item_id = {
        str(record.get("item_id")): record
        for record in targets
        if isinstance(record, dict) and record.get("item_id")
    }

    for judgment in judgments:
        item_id = str(judgment.get("item_id", ""))
        if not item_id:
            continue
        record = by_item_id.get(item_id)
        if record is None:
            record = safety_record_from_judgment(judgment)
            targets.append(record)
            by_item_id[item_id] = record
        apply_llm_judgment_to_safety_record(record, judgment)
    refresh_yomi_safety_summary(unit)


def safety_record_from_judgment(judgment: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule": "per_target_pre_llm_safety_v1",
        "item_id": judgment.get("item_id"),
        "unit_id": judgment.get("unit_id"),
        "token_index": judgment.get("token_index"),
        "chunk_index": judgment.get("chunk_index"),
        "surface": judgment.get("surface"),
        "token_surface": judgment.get("token_surface"),
        "current_reading": judgment.get("current_reading"),
        "current_reading_hiragana": judgment.get("current_reading_hiragana"),
        "target_start": judgment.get("target_start"),
        "target_end": judgment.get("target_end"),
        "is_safe": False,
        "review_status": "unresolved",
        "highlight_level": "target",
        "accepted_signal_names": [],
        "signals": [],
        "status_reason": "no_accepted_safety_signal",
    }


def apply_llm_judgment_to_safety_record(
    record: dict[str, Any],
    judgment: dict[str, Any],
) -> None:
    status = str(judgment.get("status") or "")
    accepted = status == "matched"
    signal = {
        "name": "safe_by_llm_match",
        "accepted": accepted,
        "status": status,
        "llm_reading": judgment.get("llm_reading"),
        "current_reading_hiragana": judgment.get("current_reading_hiragana"),
    }
    if judgment.get("parse_error"):
        signal["parse_error"] = judgment.get("parse_error")
    replace_signal(record, signal)

    accepted_signal_names = record.setdefault("accepted_signal_names", [])
    if not isinstance(accepted_signal_names, list):
        accepted_signal_names = []
        record["accepted_signal_names"] = accepted_signal_names
    if accepted:
        if "safe_by_llm_match" not in accepted_signal_names:
            accepted_signal_names.append("safe_by_llm_match")
        record["is_safe"] = True
        record["review_status"] = "safe"
        record["highlight_level"] = "none"
        record["status_reason"] = "accepted_llm_match"
        return

    record["accepted_signal_names"] = [
        name for name in accepted_signal_names if name != "safe_by_llm_match"
    ]
    if not record["accepted_signal_names"]:
        record["is_safe"] = False
        record["review_status"] = "unresolved"
        record["highlight_level"] = "target"
        record["status_reason"] = f"llm_reading_{status or 'unresolved'}"


def replace_signal(record: dict[str, Any], signal: dict[str, Any]) -> None:
    signals = record.setdefault("signals", [])
    if not isinstance(signals, list):
        signals = []
        record["signals"] = signals
    signals[:] = [
        existing
        for existing in signals
        if not (isinstance(existing, dict) and existing.get("name") == signal["name"])
    ]
    signals.append(signal)


def refresh_yomi_safety_summary(unit: dict[str, Any]) -> None:
    safety = unit.setdefault("analysis", {}).setdefault("safety", {}).setdefault("yomi", {})
    targets = safety.get("targets", [])
    if not isinstance(targets, list):
        targets = []
        safety["targets"] = targets
    safety.setdefault("rule", "per_target_pre_llm_safety_v1")
    safety["summary"] = {
        "target_count": len(targets),
        "safe_count": sum(
            1 for record in targets if isinstance(record, dict) and record.get("is_safe")
        ),
        "unresolved_count": sum(
            1 for record in targets if isinstance(record, dict) and not record.get("is_safe")
        ),
        "all_targets_safe": bool(targets)
        and all(isinstance(record, dict) and record.get("is_safe") for record in targets),
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


def is_standalone_laughter_w(surface: str) -> bool:
    return bool(LAUGHTER_W_RE.fullmatch(surface))


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


def marked_furigana_context(
    tokens: list[Any],
    target_index: int,
    target_chunk_index: int,
    *,
    token_readings: dict[int, str] | None = None,
) -> str:
    rendered: list[str] = []
    for index, token in enumerate(tokens):
        reading = token_readings.get(index, token.reading) if token_readings else token.reading
        if index == target_index:
            rendered.append(marked_furigana_token(token.surface, reading, target_chunk_index))
            continue
        rendered.append(furigana_no_space_token_for_llm(f"{token.surface}/{reading}"))
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


def is_valid_yomi_reading(text: str) -> bool:
    if not text:
        return False
    normalized = normalize_hiragana_reading(text)
    return all(is_yomi_reading_char(char) for char in normalized)


def is_yomi_reading_char(char: str) -> bool:
    return "\u3041" <= char <= "\u3096" or char == "ー"
