from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import random
from pathlib import Path
import sqlite3
import statistics

from yomi_corpus.historical_register import classify_document
from yomi_corpus.selection_quality import (
    DEFAULT_MAX_GLOSSES_PER_SENTENCE,
    annotation_heavy_sentences,
)
from yomi_corpus.vocabulary_campaign import extract_source_texts, index_metadata


EXPERIMENT_SCHEMA_VERSION = 2
HISTORICAL_MIN_SENTENCES = 3
HISTORICAL_MIN_SENTENCE_RATIO = 0.10
HISTORICAL_MIN_CHARACTER_RATIO = 0.15


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    label: str
    description: str
    marginal_rewards: tuple[float, ...]
    length_mode: str


STRATEGIES = (
    Strategy(
        "baseline",
        "現行方式",
        "同じ語の3文書目までを同価値とし、長さを平方根で割り引きます。",
        (1.0, 1.0, 1.0),
        "sqrt",
    ),
    Strategy(
        "novelty",
        "異語優先",
        "最初の文書を強く評価し、同じ語の追加例は急速に割り引きます。",
        (1.0, 0.35, 0.10),
        "sqrt",
    ),
    Strategy(
        "density",
        "文字効率優先",
        "異語を優先し、固定の作業開始コストを加えた文字数で利得を割ります。",
        (1.0, 0.35, 0.10),
        "linear",
    ),
    Strategy(
        "balanced",
        "バランス型",
        "異語への逓減報酬と、平方根より強い文字数ペナルティを組み合わせます。",
        (1.0, 0.35, 0.10),
        "power_075",
    ),
)


def build_selection_experiment(
    *,
    plan_path: Path,
    index_path: Path,
    output_path: Path,
    character_budgets: tuple[int, ...] = (50_000, 100_000, 250_000),
    preview_budget: int = 100_000,
    density_thresholds: tuple[float, ...] | None = None,
    random_seed: int = 20260907,
) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    selection = list(plan.get("selection") or [])
    if not selection:
        raise ValueError("Selection plan contains no documents.")
    budgets = tuple(sorted(set(character_budgets)))
    if preview_budget not in budgets:
        raise ValueError("Preview budget must be one of the experiment budgets.")

    lines = {int(row["source_line_no"]) for row in selection}
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    try:
        metadata = index_metadata(connection)
        target_rows = {
            str(row["lemma"]): (int(row["target_id"]), int(row["bccwj_document_count"]))
            for row in connection.execute("SELECT target_id, lemma, bccwj_document_count FROM targets")
        }
        availability = {
            int(target_id): int(count)
            for target_id, count in connection.execute(
                "SELECT target_id, COUNT(*) FROM hits GROUP BY target_id"
            )
        }
    finally:
        connection.close()

    target_names: dict[int, str] = {}
    target_weights: dict[int, float] = {}
    docs: dict[int, dict] = {}
    for row in selection:
        source_line_no = int(row["source_line_no"])
        target_ids = []
        for name in row.get("targets") or []:
            target = target_rows.get(str(name))
            if target is None:
                continue
            target_id, bccwj_document_count = target
            target_ids.append(target_id)
            target_names[target_id] = str(name)
            target_weights[target_id] = math.log1p(bccwj_document_count) / math.sqrt(
                max(1, availability.get(target_id, 1))
            )
        docs[source_line_no] = {
            **row,
            "target_ids": tuple(sorted(set(target_ids))),
        }

    texts = extract_source_texts(Path(metadata["source_path"]), lines)
    historical_excluded: dict[int, dict] = {}
    annotation_excluded: dict[int, dict] = {}
    for source_line_no, text in texts.items():
        result = classify_document(text)
        if _historical_gate_matches(result):
            historical_excluded[source_line_no] = {
                "flagged_sentence_count": result.flagged_sentence_count,
                "sentence_count": result.sentence_count,
                "flagged_sentence_ratio": result.flagged_sentence_ratio,
                "flagged_character_ratio": result.flagged_character_ratio,
                "category_sentence_counts": result.category_sentence_counts,
            }
        heavy_sentences = annotation_heavy_sentences(text)
        if heavy_sentences:
            annotation_excluded[source_line_no] = {
                "matching_sentence_count": len(heavy_sentences),
                "maximum_gloss_count": max(len(sentence.matches) for sentence in heavy_sentences),
                "examples": [
                    {
                        "text": sentence.text,
                        "glosses": [
                            {"surface": match.surface, "reading": match.reading}
                            for match in sentence.matches
                        ],
                    }
                    for sentence in heavy_sentences[:3]
                ],
            }

    excluded = set(historical_excluded) | set(annotation_excluded)

    strategy_rows = []
    preview_lines: set[int] = set()
    strategies = STRATEGIES if density_thresholds is None else tuple(
        Strategy(f"random_{threshold:g}", f"無作為・{threshold:g}語/千字以上",
                 "対象語の異なり数による最低密度を満たす文書から、共通の乱数順で抽出します。",
                 (), str(threshold))
        for threshold in density_thresholds
    )
    for strategy in strategies:
        runs = []
        for budget in budgets:
            chosen = _select_random_documents(
                docs, excluded=excluded, character_budget=budget,
                threshold=float(strategy.length_mode), seed=random_seed,
            ) if density_thresholds is not None else _select_documents(
                docs,
                excluded=excluded,
                target_weights=target_weights,
                strategy=strategy,
                character_budget=budget,
            )
            metrics = _selection_metrics(chosen, docs, target_names, budget)
            runs.append(metrics)
            if budget == preview_budget:
                preview_lines.update(chosen)
        preview_run = next(run for run in runs if run["character_budget"] == preview_budget)
        strategy_rows.append(
            {
                "strategy_id": strategy.strategy_id,
                "label": strategy.label,
                "description": strategy.description,
                "runs": runs,
                "preview_source_line_nos": preview_run["selected_source_line_nos"],
            }
        )

    preview_documents = []
    for source_line_no in sorted(preview_lines):
        row = docs[source_line_no]
        preview_documents.append(
            {
                "source_line_no": source_line_no,
                "source_record_id": row["source_record_id"],
                "current_slot": row["current_slot"],
                "text_length": row["text_length"],
                "matched_targets": row.get("targets") or [],
                "text": texts[source_line_no],
            }
        )

    identity = {
        "plan_id": plan["plan_id"],
        "budgets": budgets,
        "preview_budget": preview_budget,
        "strategies": [strategy.strategy_id for strategy in strategies],
        "random_seed": random_seed if density_thresholds is not None else None,
        "historical_gate": _historical_policy(),
        "parenthetical_reading_gate": {
            "max_glosses_per_sentence": DEFAULT_MAX_GLOSSES_PER_SENTENCE,
        },
    }
    experiment_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    artifact = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "artifact_type": "vocabulary-selection-experiment",
        "experiment_id": experiment_id,
        "plan_id": plan["plan_id"],
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "installation_status": "not_installed",
        "candidate_document_count": len(selection),
        "character_budgets": list(budgets),
        "preview_character_budget": preview_budget,
        "historical_gate": {
            **_historical_policy(),
            "excluded_document_count": len(historical_excluded),
            "excluded_documents": [
                {"source_line_no": line, **details}
                for line, details in sorted(historical_excluded.items())
            ],
        },
        "parenthetical_reading_gate": {
            "max_glosses_per_sentence": DEFAULT_MAX_GLOSSES_PER_SENTENCE,
            "excluded_document_count": len(annotation_excluded),
            "excluded_documents": [
                {"source_line_no": line, **details}
                for line, details in sorted(annotation_excluded.items())
            ],
        },
        "total_excluded_document_count": len(excluded),
        "strategies": strategy_rows,
        "documents": preview_documents,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def _select_random_documents(docs, *, excluded, character_budget, threshold, seed):
    ordered = sorted(docs)
    random.Random(seed).shuffle(ordered)
    selected = []
    remaining = character_budget
    for line in ordered:
        row = docs[line]
        length = int(row["text_length"])
        if line in excluded or length <= 0 or length > remaining:
            continue
        if len(row["target_ids"]) * 1000 / length < threshold:
            continue
        selected.append(line)
        remaining -= length
    return selected


def _historical_gate_matches(result) -> bool:
    return (
        result.flagged_sentence_count >= HISTORICAL_MIN_SENTENCES
        and result.flagged_sentence_ratio >= HISTORICAL_MIN_SENTENCE_RATIO
        and result.flagged_character_ratio >= HISTORICAL_MIN_CHARACTER_RATIO
    )


def _historical_policy() -> dict:
    return {
        "classifier": "historical-register-v1",
        "min_flagged_sentences": HISTORICAL_MIN_SENTENCES,
        "min_flagged_sentence_ratio": HISTORICAL_MIN_SENTENCE_RATIO,
        "min_flagged_character_ratio": HISTORICAL_MIN_CHARACTER_RATIO,
        "requires_all": True,
    }


def _select_documents(
    docs: dict[int, dict],
    *,
    excluded: set[int],
    target_weights: dict[int, float],
    strategy: Strategy,
    character_budget: int,
) -> list[int]:
    selected: list[int] = []
    used: set[int] = set()
    coverage: Counter[int] = Counter()
    remaining = character_budget
    while True:
        best_line = None
        best_score = 0.0
        for source_line_no, row in docs.items():
            if source_line_no in used or source_line_no in excluded:
                continue
            text_length = int(row["text_length"])
            if text_length > remaining:
                continue
            gain = sum(
                target_weights.get(target_id, 0.0)
                * _marginal_reward(strategy.marginal_rewards, coverage[target_id])
                for target_id in row["target_ids"]
            )
            score = gain / _length_cost(text_length, strategy.length_mode)
            if score > best_score or (
                math.isclose(score, best_score) and best_line is not None and source_line_no < best_line
            ):
                best_line = source_line_no
                best_score = score
        if best_line is None or best_score <= 0:
            break
        used.add(best_line)
        selected.append(best_line)
        remaining -= int(docs[best_line]["text_length"])
        coverage.update(docs[best_line]["target_ids"])
    return selected


def _marginal_reward(rewards: tuple[float, ...], current_count: int) -> float:
    return rewards[current_count] if current_count < len(rewards) else 0.0


def _length_cost(text_length: int, mode: str) -> float:
    if mode == "sqrt":
        return math.sqrt(max(1.0, text_length / 5_000.0))
    normalized = (text_length + 750.0) / 5_000.0
    if mode == "linear":
        return normalized
    if mode == "power_075":
        return normalized**0.75
    raise ValueError(f"Unknown length mode: {mode}")


def _selection_metrics(
    selected: list[int], docs: dict[int, dict], target_names: dict[int, str], budget: int
) -> dict:
    coverage: Counter[int] = Counter()
    total_characters = 0
    for source_line_no in selected:
        row = docs[source_line_no]
        total_characters += int(row["text_length"])
        coverage.update(row["target_ids"])
    distinct = len(coverage)
    lengths = [int(docs[line]["text_length"]) for line in selected]
    return {
        "character_budget": budget,
        "selected_document_count": len(selected),
        "selected_character_count": total_characters,
        "budget_utilization": round(total_characters / budget, 6),
        "distinct_target_count": distinct,
        "targets_with_two_examples": sum(count >= 2 for count in coverage.values()),
        "targets_with_three_examples": sum(count >= 3 for count in coverage.values()),
        "target_document_hits": sum(coverage.values()),
        "median_document_characters": statistics.median(lengths) if lengths else 0,
        "maximum_document_characters": max(lengths, default=0),
        "target_document_hits_per_1000_characters": round(
            sum(coverage.values()) * 1000 / max(1, total_characters), 6
        ),
        "distinct_targets_per_10000_characters": round(
            distinct * 10_000 / max(1, total_characters), 6
        ),
        "duplicate_hit_share": round(
            (sum(coverage.values()) - distinct) / max(1, sum(coverage.values())), 6
        ),
        "selected_source_line_nos": selected,
        "covered_targets": [target_names[target_id] for target_id in sorted(coverage)],
    }
