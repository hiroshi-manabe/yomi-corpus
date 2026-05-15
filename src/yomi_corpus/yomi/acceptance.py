from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.ngram_diagnostics import (
    DEFAULT_DECODER_LEXICON_PATH,
    DEFAULT_RAW_SUDACHI_DICT_DIR,
    StableTwoKanjiChecker,
    analyze_hybrid_stable_two_kanji_row,
)

AUTO_ACCEPT_RULE = "sudachi_decoder_agree_repeated_ngram_or_stable_two_kanji_support_v2"
AUTO_ACCEPT_PROFILE_OFF = "off"
AUTO_ACCEPT_PROFILE_STRICT = "strict"
AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI = "stable_two_kanji"
AUTO_ACCEPT_PROFILES = frozenset(
    {
        AUTO_ACCEPT_PROFILE_OFF,
        AUTO_ACCEPT_PROFILE_STRICT,
        AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI,
    }
)


@dataclass(frozen=True)
class YomiAutoAcceptance:
    value: bool
    rule: str
    signals: list[str]


@dataclass(frozen=True)
class YomiAutoAcceptSummary:
    read: int
    written: int
    accepted: int
    rejected: int
    output_jsonl: str
    summary_json: str
    rule: str
    auto_accept_profile: str
    stable_two_kanji_enabled: bool


def judge_yomi_auto_accept(
    unit: dict[str, Any],
    *,
    auto_accept_profile: str = AUTO_ACCEPT_PROFILE_STRICT,
    stable_two_kanji_checker: StableTwoKanjiChecker | None = None,
) -> YomiAutoAcceptance:
    if auto_accept_profile not in AUTO_ACCEPT_PROFILES:
        raise ValueError(f"Unsupported yomi auto-accept profile: {auto_accept_profile}")
    if auto_accept_profile == AUTO_ACCEPT_PROFILE_OFF:
        return YomiAutoAcceptance(
            value=False,
            rule=AUTO_ACCEPT_RULE,
            signals=["auto_accept_profile_off"],
        )

    yomi = (
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
    )
    rendered = yomi.get("rendered")
    signals: list[str] = []

    if not isinstance(rendered, str) or not rendered:
        return YomiAutoAcceptance(
            value=False,
            rule=AUTO_ACCEPT_RULE,
            signals=["missing_yomi"],
        )

    unresolved = unresolved_non_numeric_reading_surfaces(rendered)
    if unresolved:
        signals.append("has_unresolved_non_numeric_reading")
    else:
        signals.append("no_unresolved_non_numeric_reading")

    sudachi_rendered = yomi.get("sudachi", {}).get("rendered")
    top_candidate = first_decoder_candidate(yomi)
    decoder_rendered = None if top_candidate is None else top_candidate.get("rendered")
    if not isinstance(sudachi_rendered, str) or not sudachi_rendered:
        signals.append("missing_sudachi_rendered")
    elif not isinstance(decoder_rendered, str) or not decoder_rendered:
        signals.append("missing_decoder_rendered")
    elif sudachi_rendered == decoder_rendered:
        signals.append("sudachi_decoder_agree")
    else:
        signals.append("sudachi_decoder_disagree")

    has_full_support = False
    if top_candidate is None:
        signals.append("missing_decoder_candidate")
    elif decoder_candidate_has_full_repeated_ngram_support(top_candidate):
        signals.append("decoder_full_repeated_ngram_support")
        has_full_support = True
    else:
        signals.append("decoder_lacks_full_repeated_ngram_support")

    has_stable_two_kanji_support = False
    if auto_accept_profile != AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI:
        signals.append("stable_two_kanji_relaxation_disabled")
    elif stable_two_kanji_checker is None:
        signals.append("stable_two_kanji_relaxation_disabled")
    elif top_candidate is None:
        signals.append("stable_two_kanji_relaxation_unavailable")
    else:
        stable_result = analyze_hybrid_stable_two_kanji_row(
            unit,
            stable_checker=stable_two_kanji_checker,
        )
        stable_spans = stable_result.get("spans", [])
        if not stable_spans:
            signals.append("stable_two_kanji_relaxation_no_spans")
        elif all(span.get("relaxed_pass") for span in stable_spans):
            has_stable_two_kanji_support = True
            signals.append("decoder_full_support_with_stable_two_kanji_relaxation")
            if any(span.get("newly_pass") for span in stable_spans):
                signals.append("stable_two_kanji_relaxation_used")
        else:
            signals.append("stable_two_kanji_relaxation_failed")

    accepted = all(
        signal in signals
        for signal in [
            "no_unresolved_non_numeric_reading",
            "sudachi_decoder_agree",
        ]
    ) and (has_full_support or has_stable_two_kanji_support)
    return YomiAutoAcceptance(
        value=accepted,
        rule=AUTO_ACCEPT_RULE,
        signals=signals,
    )


def unresolved_non_numeric_reading_surfaces(rendered: str) -> list[str]:
    unresolved: list[str] = []
    for pair in rendered.split():
        if "/" not in pair:
            unresolved.append(pair)
            continue
        surface, reading = pair.rsplit("/", 1)
        if reading:
            continue
        if surface and surface.isdecimal():
            continue
        unresolved.append(surface)
    return unresolved


def first_decoder_candidate(yomi: dict[str, Any]) -> dict[str, Any] | None:
    candidates = yomi.get("ngram_decoder", {}).get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return None
    return candidate


def decoder_candidate_has_full_repeated_ngram_support(candidate: dict[str, Any]) -> bool:
    entries = candidate.get("entries", [])
    if not isinstance(entries, list) or not entries:
        return False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return False
        if index == 0:
            if int(entry.get("final_order", 1)) < 2:
                return False
            continue
        piece_orders = entry.get("piece_orders", [])
        if not isinstance(piece_orders, list) or not piece_orders:
            return False
        if int(piece_orders[0]) < 2:
            return False
    return True


def apply_yomi_auto_acceptance_file(
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    auto_accept_profile: str = AUTO_ACCEPT_PROFILE_STRICT,
    enable_stable_two_kanji: bool = False,
    raw_sudachi_dict_dir: Path = DEFAULT_RAW_SUDACHI_DICT_DIR,
) -> YomiAutoAcceptSummary:
    if auto_accept_profile not in AUTO_ACCEPT_PROFILES:
        raise ValueError(f"Unsupported yomi auto-accept profile: {auto_accept_profile}")
    if auto_accept_profile == AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI:
        enable_stable_two_kanji = True
    elif auto_accept_profile == AUTO_ACCEPT_PROFILE_OFF:
        enable_stable_two_kanji = False
    elif enable_stable_two_kanji:
        auto_accept_profile = AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI
    elif auto_accept_profile == AUTO_ACCEPT_PROFILE_STRICT:
        enable_stable_two_kanji = False

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    read = 0
    written = 0
    accepted = 0
    rejected = 0
    stable_checker = (
        StableTwoKanjiChecker(
            rows=[],
            decoder_lexicon_path=DEFAULT_DECODER_LEXICON_PATH,
            raw_sudachi_dict_dir=raw_sudachi_dict_dir,
        )
        if enable_stable_two_kanji
        else None
    )
    with input_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read += 1
            unit = json.loads(line)
            judgment = judge_yomi_auto_accept(
                unit,
                auto_accept_profile=auto_accept_profile,
                stable_two_kanji_checker=stable_checker,
            )
            unit["analysis"]["mechanical"]["yomi"]["auto_accept"] = asdict(judgment)
            if judgment.value:
                accepted += 1
            else:
                rejected += 1
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written += 1

    summary = YomiAutoAcceptSummary(
        read=read,
        written=written,
        accepted=accepted,
        rejected=rejected,
        output_jsonl=str(output_jsonl),
        summary_json=str(summary_json),
        rule=AUTO_ACCEPT_RULE,
        auto_accept_profile=auto_accept_profile,
        stable_two_kanji_enabled=enable_stable_two_kanji,
    )
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
