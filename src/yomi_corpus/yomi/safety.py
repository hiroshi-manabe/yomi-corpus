from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.corpus_frequency import (
    EVIDENCE_SCOPE_TRAILING_KANA_STEM,
    TRAILING_KANA_NORMALIZATION_RULE,
    SurfaceReadingStats,
    trailing_kana_stem_pair,
)
from yomi_corpus.yomi.furigana import has_greek
from yomi_corpus.yomi.llm_readings import (
    build_yomi_llm_reading_items,
    is_standalone_laughter_w,
    is_valid_yomi_reading,
)
from yomi_corpus.yomi.ngram_diagnostics import (
    DEFAULT_RAW_SUDACHI_DICT_DIR,
    StableTwoKanjiChecker,
)
from yomi_corpus.yomi.numeric_surfaces import (
    allows_optional_japanese_numeral_reading,
    is_numeric_only_surface,
)
from yomi_corpus.yomi.stable_surface_lexicon import (
    StableSurfaceReadingLexicon,
    resolve_stable_surface_lexicon_artifact,
)
from yomi_corpus.yomi.token_codec import YomiTokenError, yomi_tokens_from_mapping

SAFETY_RULE = "per_target_pre_llm_safety_v5"
DEFAULT_YOMI_CONFIG_PATH = "config/yomi/default.toml"
MODEL_FREQUENCY_STATS_FILENAME = "surface_reading_stats.tsv"
LOCAL_STABLE_SPAN_MIN_TOKENS = 2
LOCAL_STABLE_SPAN_MAX_TOKENS = 6


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
    stable_surface_lexicon_safe: int = 0
    stable_surface_lexicon_artifact: str | None = None
    stable_surface_source_version: str | None = None
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
    stable_surface_path = resolve_stable_surface_lexicon_artifact(decoder_model_dir)
    stable_checker = (
        StableSurfaceReadingLexicon.load_tsv(stable_surface_path)
        if enable_stable_two_kanji and stable_surface_path is not None
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
    stable_surface_lexicon_safe = 0
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
                if "safe_by_stable_surface_lexicon" in record["accepted_signal_names"]:
                    stable_surface_lexicon_safe += 1
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
        stable_surface_lexicon_safe=stable_surface_lexicon_safe,
        stable_surface_lexicon_artifact=None
        if stable_checker is None
        else stable_checker.artifact_path,
        stable_surface_source_version=None
        if stable_checker is None
        else stable_checker.source_corpus_version,
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
    stable_checker: StableTwoKanjiChecker | StableSurfaceReadingLexicon | None = None,
    corpus_stats: SurfaceReadingStats | None = None,
    corpus_frequency_min_count: int = 5,
    corpus_frequency_min_share: float = 0.95,
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

        surface = str(item["surface"])
        if has_greek(surface):
            current_reading = str(item.get("current_reading") or "")
            sudachi_reading = str(item.get("token_sudachi_reading") or "")
            accepted = (
                bool(sudachi_reading)
                and current_reading == sudachi_reading
                and is_valid_yomi_reading(current_reading)
            )
            signals.append(
                {
                    "name": "safe_by_sudachi_greek",
                    "accepted": accepted,
                    "reading": current_reading,
                    "sudachi_reading": sudachi_reading,
                    "reason": (
                        "valid_greek_token_reading_matches_sudachi"
                        if accepted
                        else "greek_token_reading_missing_or_differs_from_sudachi"
                    ),
                }
            )
            if accepted:
                accepted_signal_names.append("safe_by_sudachi_greek")

        numeral_pos = "数詞" in str(item.get("pos") or "")
        if is_numeric_only_surface(surface) and (
            not allows_optional_japanese_numeral_reading(surface) or numeral_pos
        ):
            signals.append(
                {
                    "name": "safe_by_no_ruby_numeric_surface",
                    "accepted": True,
                    "reason": "numeric_surface_uses_separate_reading_layer",
                    "preferred_choice_source": "none",
                }
            )
            accepted_signal_names.append("safe_by_no_ruby_numeric_surface")

        if stable_checker is not None:
            stable = stable_checker.judge(str(item["surface"]), str(item["current_reading"]))
            stable_signal_name = (
                "safe_by_stable_surface_lexicon"
                if isinstance(stable_checker, StableSurfaceReadingLexicon)
                else "safe_by_stable_dictionary"
            )
            evidence = getattr(stable, "evidence", None)
            signals.append(
                {
                    "name": stable_signal_name,
                    "accepted": stable.value,
                    "reason": stable.reason,
                    **(
                        {}
                        if evidence is None
                        else {
                            "count": evidence.count,
                            "surface_total_count": evidence.surface_total_count,
                            "share": evidence.share,
                            "source_corpus_version": evidence.source_corpus_version,
                            "artifact_path": stable_checker.artifact_path,
                        }
                    ),
                }
            )
            if stable.value:
                accepted_signal_names.append(stable_signal_name)

        if corpus_stats is not None:
            token_surface = str(item.get("token_surface") or item["surface"])
            token_reading = str(item.get("token_current_reading") or item["current_reading"])
            target_surface = str(item["surface"])
            target_reading = str(item["current_reading"])
            evidence_scope = "target"
            evidence_surface = target_surface
            evidence_reading = target_reading
            normalization_rule = None
            dominant = None
            if token_surface != target_surface:
                dominant = corpus_stats.dominant_reading(
                    token_surface,
                    min_count=corpus_frequency_min_count,
                    min_share=corpus_frequency_min_share,
                )
                evidence_scope = "token"
                evidence_surface = token_surface
                evidence_reading = token_reading
            if dominant is None:
                normalized = trailing_kana_stem_pair(token_surface, token_reading)
                if normalized is not None:
                    evidence_surface, evidence_reading = normalized
                    dominant = corpus_stats.dominant_reading(
                        evidence_surface,
                        min_count=corpus_frequency_min_count,
                        min_share=corpus_frequency_min_share,
                        evidence_scope=EVIDENCE_SCOPE_TRAILING_KANA_STEM,
                    )
                    evidence_scope = EVIDENCE_SCOPE_TRAILING_KANA_STEM
                    normalization_rule = TRAILING_KANA_NORMALIZATION_RULE
            if dominant is None:
                dominant = corpus_stats.dominant_reading(
                    target_surface,
                    min_count=corpus_frequency_min_count,
                    min_share=corpus_frequency_min_share,
                )
                evidence_scope = "target"
                evidence_surface = target_surface
                evidence_reading = target_reading
                normalization_rule = None
            accepted = dominant is not None and dominant.reading == evidence_reading
            pooled_surface_guard = None
            if (
                accepted
                and evidence_scope in {"target", "token"}
                and isinstance(stable_checker, StableSurfaceReadingLexicon)
            ):
                pooled = stable_checker.judge(evidence_surface, evidence_reading)
                pooled_surface_guard = {
                    "accepted": pooled.value,
                    "reason": pooled.reason,
                    "artifact_path": stable_checker.artifact_path,
                    "source_corpus_version": stable_checker.source_corpus_version,
                }
                # Exact-token counts can hide competing readings recorded under
                # another segmentation, such as 一日 versus 一|日.
                accepted = pooled.value
            signals.append(
                {
                    "name": "safe_by_corpus_frequency",
                    "accepted": accepted,
                    "evidence_scope": evidence_scope,
                    "evidence_surface": evidence_surface,
                    "evidence_reading": evidence_reading,
                    "normalization_rule": normalization_rule,
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
                    "pooled_surface_guard": pooled_surface_guard,
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
    if isinstance(stable_checker, StableSurfaceReadingLexicon):
        apply_local_stable_span_safety(unit, records, stable_checker=stable_checker)
    return records


def apply_local_stable_span_safety(
    unit: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    stable_checker: StableSurfaceReadingLexicon,
) -> None:
    text = str(unit.get("text") or "")
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    if not text or not isinstance(yomi, dict):
        return
    try:
        pairs = yomi_tokens_from_mapping(yomi, text=text)
    except YomiTokenError:
        return
    pair_spans: list[tuple[int, int, str, str]] = []
    cursor = 0
    for surface, reading in pairs:
        end = cursor + len(surface)
        pair_spans.append((cursor, end, surface, reading))
        cursor = end
    if cursor != len(text):
        return

    evidence_by_item: dict[str, list[dict[str, Any]]] = {}
    for start_index in range(len(pair_spans)):
        for token_count in range(
            LOCAL_STABLE_SPAN_MIN_TOKENS,
            LOCAL_STABLE_SPAN_MAX_TOKENS + 1,
        ):
            end_index = start_index + token_count
            if end_index > len(pair_spans):
                break
            window = pair_spans[start_index:end_index]
            if any(not reading or surface.isspace() for _start, _end, surface, reading in window):
                continue
            surface = "".join(row[2] for row in window)
            reading = "".join(row[3] for row in window)
            segmentation = tuple(row[2] for row in window)
            judgment = stable_checker.judge(
                surface,
                reading,
                segmentation=segmentation,
            )
            if not judgment.value or judgment.evidence is None:
                continue
            span_start = window[0][0]
            span_end = window[-1][1]
            evidence = judgment.evidence
            signal = {
                "name": "safe_by_local_stable_span",
                "accepted": True,
                "reason": judgment.reason,
                "evidence_scope": "local_token_window",
                "evidence_surface": surface,
                "evidence_reading": reading,
                "evidence_segmentation": list(segmentation),
                "token_start_index": start_index,
                "token_end_index": end_index,
                "token_count": token_count,
                "target_start": span_start,
                "target_end": span_end,
                "count": evidence.count,
                "surface_total_count": evidence.surface_total_count,
                "share": evidence.share,
                "source_corpus_version": evidence.source_corpus_version,
                "artifact_path": stable_checker.artifact_path,
            }
            for record in records:
                target_start = record.get("target_start")
                target_end = record.get("target_end")
                if (
                    isinstance(target_start, int)
                    and isinstance(target_end, int)
                    and span_start <= target_start
                    and target_end <= span_end
                ):
                    evidence_by_item.setdefault(str(record.get("item_id") or ""), []).append(signal)

    for record in records:
        matches = evidence_by_item.get(str(record.get("item_id") or ""), [])
        if not matches:
            continue
        # Prefer stronger corpus evidence, then the smaller local context.
        signal = min(
            matches,
            key=lambda row: (-int(row["count"]), int(row["token_count"])),
        )
        record["signals"].append(signal)
        if "safe_by_local_stable_span" not in record["accepted_signal_names"]:
            record["accepted_signal_names"].append("safe_by_local_stable_span")
        record["is_safe"] = True
        record["review_status"] = "safe"
        record["highlight_level"] = "none"
        record["status_reason"] = "accepted_pre_llm_signal"


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
