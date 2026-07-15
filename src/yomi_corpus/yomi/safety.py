from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.corpus_frequency import SurfaceReadingStats
from yomi_corpus.yomi.llm_readings import build_yomi_llm_reading_items, is_standalone_laughter_w
from yomi_corpus.yomi.ngram_diagnostics import (
    DEFAULT_DECODER_LEXICON_PATH,
    DEFAULT_RAW_SUDACHI_DICT_DIR,
    StableTwoKanjiChecker,
)

SAFETY_RULE = "per_target_pre_llm_safety_v1"
DEFAULT_YOMI_CONFIG_PATH = "config/yomi/default.toml"
MODEL_FREQUENCY_STATS_FILENAME = "surface_reading_stats.tsv"


@dataclass(frozen=True)
class YomiSafetySummary:
    read_units: int
    written_units: int
    target_count: int
    safe_targets: int
    unresolved_targets: int
    stable_two_kanji_safe: int
    corpus_frequency_safe: int
    unit_auto_accept_safe: int
    output_jsonl: str
    summary_json: str
    corpus_frequency_stats_artifact: str | None
    corpus_frequency_source_version: str | None
    rule: str = SAFETY_RULE


def apply_yomi_safety_pre_llm_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    yomi_config_path: str | Path = DEFAULT_YOMI_CONFIG_PATH,
    enable_stable_two_kanji: bool = True,
    enable_corpus_frequency: bool = True,
    raw_sudachi_dict_dir: Path = DEFAULT_RAW_SUDACHI_DICT_DIR,
    decoder_model_dir: str | Path | None = None,
) -> YomiSafetySummary:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    yomi_config = load_yomi_generation_config(yomi_config_path)
    stable_checker = (
        StableTwoKanjiChecker(
            rows=[],
            decoder_lexicon_path=DEFAULT_DECODER_LEXICON_PATH,
            raw_sudachi_dict_dir=raw_sudachi_dict_dir,
        )
        if enable_stable_two_kanji
        else None
    )
    corpus_stats_path = resolve_corpus_frequency_stats_artifact(
        configured_path=yomi_config.corpus_frequency_stats_artifact,
        decoder_model_dir=decoder_model_dir,
    )
    corpus_stats = load_corpus_stats(corpus_stats_path) if enable_corpus_frequency else None

    read_units = 0
    written_units = 0
    target_count = 0
    safe_targets = 0
    stable_two_kanji_safe = 0
    corpus_frequency_safe = 0
    unit_auto_accept_safe = 0

    with input_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            records = build_pre_llm_safety_records(
                unit,
                stable_checker=stable_checker,
                corpus_stats=corpus_stats,
                corpus_frequency_min_count=yomi_config.corpus_frequency_min_count,
                corpus_frequency_min_share=yomi_config.corpus_frequency_min_share,
            )
            for record in records:
                target_count += 1
                if record["is_safe"]:
                    safe_targets += 1
                if "safe_by_stable_dictionary" in record["accepted_signal_names"]:
                    stable_two_kanji_safe += 1
                if "safe_by_corpus_frequency" in record["accepted_signal_names"]:
                    corpus_frequency_safe += 1
                if "safe_by_unit_auto_accept" in record["accepted_signal_names"]:
                    unit_auto_accept_safe += 1
            set_yomi_safety_records(unit, records)
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1

    summary = YomiSafetySummary(
        read_units=read_units,
        written_units=written_units,
        target_count=target_count,
        safe_targets=safe_targets,
        unresolved_targets=target_count - safe_targets,
        stable_two_kanji_safe=stable_two_kanji_safe,
        corpus_frequency_safe=corpus_frequency_safe,
        unit_auto_accept_safe=unit_auto_accept_safe,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
        corpus_frequency_stats_artifact=None if corpus_stats is None else corpus_stats.artifact_path,
        corpus_frequency_source_version=None
        if corpus_stats is None
        else corpus_stats.source_corpus_version,
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def resolve_corpus_frequency_stats_artifact(
    *,
    configured_path: str | Path | None,
    decoder_model_dir: str | Path | None,
) -> Path | None:
    if decoder_model_dir:
        model_path = Path(decoder_model_dir) / MODEL_FREQUENCY_STATS_FILENAME
        if model_path.exists():
            return model_path
    return Path(configured_path) if configured_path else None


def build_pre_llm_safety_records(
    unit: dict[str, Any],
    *,
    stable_checker: StableTwoKanjiChecker | None = None,
    corpus_stats: SurfaceReadingStats | None = None,
    corpus_frequency_min_count: int = 5,
    corpus_frequency_min_share: float = 0.995,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    unit_auto_accept = yomi_auto_accept_payload(unit)
    for item in build_yomi_llm_reading_items(unit, stable_checker=None):
        signals: list[dict[str, Any]] = []
        accepted_signal_names: list[str] = []

        if unit_auto_accept is not None:
            signals.append(
                {
                    "name": "safe_by_unit_auto_accept",
                    "accepted": True,
                    "rule": unit_auto_accept.get("rule"),
                    "reason": "whole_unit_yomi_auto_accept",
                }
            )
            accepted_signal_names.append("safe_by_unit_auto_accept")

        if is_standalone_laughter_w(str(item["surface"])):
            signals.append(
                {
                    "name": "safe_by_no_ruby_laughter_w",
                    "accepted": True,
                    "reason": "standalone_lowercase_w_laughter_marker",
                    "preferred_choice_source": "none",
                }
            )
            accepted_signal_names.append("safe_by_no_ruby_laughter_w")

        if stable_checker is not None:
            stable = stable_checker.judge(str(item["surface"]), str(item["current_reading"]))
            signals.append(
                {
                    "name": "safe_by_stable_dictionary",
                    "accepted": stable.value,
                    "reason": stable.reason,
                }
            )
            if stable.value:
                accepted_signal_names.append("safe_by_stable_dictionary")

        if corpus_stats is not None:
            dominant = corpus_stats.dominant_reading(
                str(item["surface"]),
                min_count=corpus_frequency_min_count,
                min_share=corpus_frequency_min_share,
            )
            accepted = dominant is not None and dominant.reading == item["current_reading"]
            signals.append(
                {
                    "name": "safe_by_corpus_frequency",
                    "accepted": accepted,
                    "min_count": corpus_frequency_min_count,
                    "min_share": corpus_frequency_min_share,
                    "dominant": None
                    if dominant is None
                    else {
                        "reading": dominant.reading,
                        "count": dominant.count,
                        "surface_total_count": dominant.surface_total_count,
                        "share": dominant.share,
                        "source_corpus_version": dominant.source_corpus_version,
                    },
                    "artifact_path": corpus_stats.artifact_path,
                    "source_corpus_version": corpus_stats.source_corpus_version,
                }
            )
            if accepted:
                accepted_signal_names.append("safe_by_corpus_frequency")

        is_safe = bool(accepted_signal_names)
        records.append(
            {
                "rule": SAFETY_RULE,
                "item_id": item["item_id"],
                "unit_id": item["unit_id"],
                "token_index": item["token_index"],
                "chunk_index": item["chunk_index"],
                "surface": item["surface"],
                "token_surface": item["token_surface"],
                "current_reading": item["current_reading"],
                "current_reading_hiragana": item["current_reading_hiragana"],
                "target_start": item["target_start"],
                "target_end": item["target_end"],
                "is_safe": is_safe,
                "review_status": "safe" if is_safe else "unresolved",
                "highlight_level": "none" if is_safe else "target",
                "accepted_signal_names": accepted_signal_names,
                "signals": signals,
                "status_reason": "accepted_pre_llm_signal" if is_safe else "no_accepted_safety_signal",
            }
        )
    return records


def yomi_auto_accept_payload(unit: dict[str, Any]) -> dict[str, Any] | None:
    auto_accept = (
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
        .get("auto_accept")
    )
    if isinstance(auto_accept, dict) and auto_accept.get("value"):
        return auto_accept
    return None


def set_yomi_safety_records(unit: dict[str, Any], records: list[dict[str, Any]]) -> None:
    safety = unit.setdefault("analysis", {}).setdefault("safety", {}).setdefault("yomi", {})
    safety["rule"] = SAFETY_RULE
    safety["targets"] = records
    safety["summary"] = {
        "target_count": len(records),
        "safe_count": sum(1 for record in records if record["is_safe"]),
        "unresolved_count": sum(1 for record in records if not record["is_safe"]),
        "all_targets_safe": bool(records) and all(record["is_safe"] for record in records),
    }


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


def load_corpus_stats(path: str | Path | None) -> SurfaceReadingStats | None:
    if not path:
        return None
    stats_path = Path(path)
    if not stats_path.exists():
        return None
    return SurfaceReadingStats.load_tsv(stats_path)
