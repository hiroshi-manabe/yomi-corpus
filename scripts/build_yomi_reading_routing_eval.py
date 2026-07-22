#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.numeric_compounds import numeric_compound_rule


DEFAULT_UNITS_ROOT = Path("data/units")
DEFAULT_OUTPUT = Path("data/evals/yomi_reading/routing_targets_v1.jsonl")
DEFAULT_SUMMARY = Path("data/evals/yomi_reading/routing_targets_v1.summary.json")
DEFAULT_TARGETS = ("方", "人", "日", "月", "行", "中", "何", "入", "思", "多")
SAMPLE_SIZE_PER_TARGET = 20
DETERMINISTIC_QUOTA_PER_TARGET = 4
SEED = "yomi-reading-routing-v1"
CURATED_EXPECTED_READING_OVERRIDES = {
    "ja_cc_level2:0000000026:u0021:r0003c01": "び",
}
CURATED_ACCEPTABLE_READING_OVERRIDES = {
    "ja_cc_level2:0000000025:u0108:r0016c01": ["い", "ゆ"],
    "ja_cc_level2:0000000016:u0036:r0003c01": ["ちゅう", "じゅう"],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a stratified human-finalized eval for per-surface model routing."
    )
    parser.add_argument("--units-root", type=Path, default=DEFAULT_UNITS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--targets", nargs="+", default=list(DEFAULT_TARGETS))
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE_PER_TARGET)
    parser.add_argument(
        "--deterministic-quota",
        type=int,
        default=DETERMINISTIC_QUOTA_PER_TARGET,
    )
    args = parser.parse_args()

    routed_item_ids = load_routed_item_ids(args.units_root)
    candidates = load_finalized_candidates(
        args.units_root,
        targets=set(args.targets),
        routed_item_ids=routed_item_ids,
    )
    selected: list[dict[str, Any]] = []
    target_summaries: dict[str, Any] = {}
    for surface in args.targets:
        rows = candidates.get(surface, [])
        deterministic = [row for row in rows if not row["was_llm_routed"]]
        llm_routed = [row for row in rows if row["was_llm_routed"]]
        deterministic_quota = min(args.deterministic_quota, len(deterministic))
        llm_quota = args.sample_size - deterministic_quota
        if len(llm_routed) < llm_quota:
            deterministic_quota = min(len(deterministic), args.sample_size - len(llm_routed))
            llm_quota = args.sample_size - deterministic_quota
        if len(llm_routed) < llm_quota or len(deterministic) < deterministic_quota:
            raise RuntimeError(
                f"Insufficient finalized candidates for {surface}: "
                f"llm={len(llm_routed)}, deterministic={len(deterministic)}"
            )
        chosen = stratified_pick(llm_routed, llm_quota, surface=surface, route="llm")
        chosen += stratified_pick(
            deterministic,
            deterministic_quota,
            surface=surface,
            route="deterministic",
        )
        chosen.sort(key=lambda row: stable_key(surface, str(row["item_id"])))
        for index, row in enumerate(chosen, start=1):
            selected.append({**row, "target_sample_seq": index})
        target_summaries[surface] = summarize_target(rows, chosen)

    selected.sort(key=lambda row: (args.targets.index(row["surface"]), row["target_sample_seq"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, selected)
    summary = {
        "schema_version": "yomi_reading_routing_eval_summary_v1",
        "output_jsonl": str(args.output),
        "source_glob": str(args.units_root / "dev_batch_*/units.yomi.final.jsonl"),
        "targets": list(args.targets),
        "sample_size_per_target": args.sample_size,
        "deterministic_quota_per_target": args.deterministic_quota,
        "selected_count": len(selected),
        "seed": SEED,
        "selection_policy": [
            "Use only human-reviewed, non-skipped finalized units.",
            "Exclude units with span overrides or strong-repair segmentation changes.",
            "Exclude targets absorbed into a configured numeric compound such as 1日 or 2人.",
            "Prefer LLM-routed examples; include up to four deterministic examples per target.",
            "Round-robin across finalized readings and minimize repeated documents.",
        ],
        "targets_summary": target_summaries,
    }
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def load_routed_item_ids(units_root: Path) -> set[str]:
    item_ids: set[str] = set()
    for path in sorted(units_root.glob("dev_batch_*/yomi_reading_input.jsonl")):
        for row in load_jsonl(path):
            item_ids.add(str(row["item_id"]))
    return item_ids


def load_finalized_candidates(
    units_root: Path,
    *,
    targets: set[str],
    routed_item_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(units_root.glob("dev_batch_*/units.yomi.final.jsonl")):
        batch_name = path.parent.name
        model = load_batch_model(path.parent)
        for unit in load_jsonl(path):
            review = (
                unit.get("analysis", {}).get("human_review", {}).get("yomi_final", {})
            )
            if not review.get("reviewed") or review.get("skip"):
                continue
            if review.get("span_overrides"):
                continue
            if unit.get("analysis", {}).get("human_review", {}).get("yomi_strong_repair"):
                continue
            overrides = {
                str(row.get("item_id")): row
                for row in review.get("target_overrides", [])
                if isinstance(row, dict) and row.get("item_id")
            }
            llm_items = {
                str(row.get("item_id")): row
                for row in (
                    unit.get("analysis", {})
                    .get("llm", {})
                    .get("yomi_readings", {})
                    .get("items", [])
                )
                if isinstance(row, dict) and row.get("item_id")
            }
            safety_targets = (
                unit.get("analysis", {})
                .get("safety", {})
                .get("yomi", {})
                .get("targets", [])
            )
            for target in safety_targets:
                if not isinstance(target, dict) or target.get("surface") not in targets:
                    continue
                item_id = str(target.get("item_id") or "")
                override = overrides.get(item_id)
                if override and override.get("choice_source") == "none":
                    continue
                expected = (
                    override.get("selected_reading") if override else None
                ) or target.get("current_reading_hiragana")
                if not isinstance(expected, str) or not expected:
                    continue
                curated_expected = CURATED_EXPECTED_READING_OVERRIDES.get(item_id)
                if curated_expected is not None:
                    expected = curated_expected
                acceptable_readings = CURATED_ACCEPTABLE_READING_OVERRIDES.get(item_id)
                llm_item = llm_items.get(item_id, {})
                start = int(target["target_start"])
                end = int(target["target_end"])
                text = str(unit.get("text") or "")
                if is_absorbed_numeric_compound_target(
                    text=text,
                    start=start,
                    end=end,
                    surface=str(target["surface"]),
                ):
                    continue
                candidate = {
                        "schema_version": "yomi_reading_routing_eval_v1",
                        "item_id": item_id,
                        "unit_id": str(unit.get("unit_id") or ""),
                        "doc_id": str(unit.get("doc_id") or ""),
                        "track_doc_seq": unit.get("track_doc_seq"),
                        "batch_name": batch_name,
                        "surface": str(target["surface"]),
                        "token_surface": str(target.get("token_surface") or ""),
                        "target_start": start,
                        "target_end": end,
                        "text": text,
                        "marked_text": text[:start] + "**" + text[start:end] + "**" + text[end:],
                        "expected_reading": expected,
                        "label_source": (
                            "curated_benchmark_correction"
                            if curated_expected is not None
                            else (
                                "final_review_target_override"
                                if override
                                else "final_review_accepted_current"
                            )
                        ),
                        "current_reading_hiragana": target.get(
                            "current_reading_hiragana"
                        ),
                        "was_llm_routed": item_id in routed_item_ids,
                        "routing_population": (
                            "llm_routed" if item_id in routed_item_ids else "deterministic"
                        ),
                        "accepted_signal_names": list(
                            target.get("accepted_signal_names") or []
                        ),
                        "original_llm_model": model if item_id in routed_item_ids else None,
                        "original_llm_status": llm_item.get("status"),
                        "original_llm_reading": llm_item.get("llm_reading"),
                        "original_llm_raw_text": llm_item.get("raw_text"),
                        "human_review_submission_id": review.get("submission_id"),
                    }
                if acceptable_readings is not None:
                    candidate["acceptable_readings"] = acceptable_readings
                    candidate["acceptable_readings_source"] = "curated_benchmark_variants"
                candidates[str(target["surface"])].append(candidate)
    return candidates


def is_absorbed_numeric_compound_target(
    *,
    text: str,
    start: int,
    end: int,
    surface: str,
) -> bool:
    if text[start:end] != surface:
        return False
    prefix = text[:start]
    match = re.search(r"[0-9０-９]+$", prefix)
    if match is None:
        return False
    return numeric_compound_rule(match.group() + surface) is not None


def load_batch_model(batch_dir: Path) -> str | None:
    path = batch_dir / "yomi_reading_usage_summary.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    model = payload.get("model")
    return str(model) if model else None


def stratified_pick(
    rows: list[dict[str, Any]],
    count: int,
    *,
    surface: str,
    route: str,
) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    by_reading: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_reading[str(row["expected_reading"])].append(row)
    for reading, group in by_reading.items():
        group.sort(key=lambda row: stable_key(surface, route, reading, str(row["item_id"])))

    chosen: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    used_contexts: set[str] = set()
    doc_counts: Counter[str] = Counter()
    readings = sorted(by_reading, key=lambda value: stable_key(surface, route, value))
    while len(chosen) < count:
        added = False
        for reading in readings:
            available = [
                row for row in by_reading[reading] if str(row["item_id"]) not in used_ids
            ]
            if not available:
                continue
            novel_contexts = [
                row for row in available if str(row["marked_text"]) not in used_contexts
            ]
            if not novel_contexts:
                continue
            available = novel_contexts
            row = min(
                available,
                key=lambda candidate: (
                    doc_counts[str(candidate["doc_id"])],
                    stable_key(surface, route, reading, str(candidate["item_id"])),
                ),
            )
            chosen.append(row)
            used_ids.add(str(row["item_id"]))
            used_contexts.add(str(row["marked_text"]))
            doc_counts[str(row["doc_id"])] += 1
            added = True
            if len(chosen) >= count:
                break
        if not added:
            raise RuntimeError(f"Could not select {count} rows for {surface}/{route}")
    return chosen


def summarize_target(
    population: list[dict[str, Any]], selected: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "finalized_candidate_count": len(population),
        "finalized_candidate_readings": dict(
            sorted(Counter(row["expected_reading"] for row in population).items())
        ),
        "finalized_candidate_routes": dict(
            sorted(Counter(row["routing_population"] for row in population).items())
        ),
        "selected_count": len(selected),
        "selected_readings": dict(
            sorted(Counter(row["expected_reading"] for row in selected).items())
        ),
        "selected_routes": dict(
            sorted(Counter(row["routing_population"] for row in selected).items())
        ),
        "selected_document_count": len({row["doc_id"] for row in selected}),
        "selected_batch_count": len({row["batch_name"] for row in selected}),
    }


def stable_key(*parts: str) -> str:
    return hashlib.sha256((SEED + "\0" + "\0".join(parts)).encode()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
