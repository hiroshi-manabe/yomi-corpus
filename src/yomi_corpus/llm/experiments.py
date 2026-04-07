from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
from time import time
from typing import Any

from yomi_corpus.llm.backend import OpenAIResponsesBackend
from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.experiment_scoring import score_output, summarize_scores
from yomi_corpus.llm.pricing import DEFAULT_PRICING_CONFIG_PATH
from yomi_corpus.llm.runner import write_results_jsonl
from yomi_corpus.llm.tasks import build_prompt_items, load_jsonl_rows
from yomi_corpus.llm.usage_report import summarize_results_jsonl
from yomi_corpus.paths import resolve_repo_path

ITEMS_FILENAME = "items.jsonl"
RAW_RESULTS_FILENAME = "results.raw.jsonl"
PARSED_RESULTS_FILENAME = "results.parsed.jsonl"
SCORED_RESULTS_FILENAME = "scored.jsonl"
SUMMARY_FILENAME = "summary.json"
MANIFEST_FILENAME = "manifest.json"
PROMPT_SNAPSHOT_FILENAME = "prompt_template_snapshot.txt"
COMPARISON_FILENAME = "comparison.json"


def run_prompt_experiment(
    *,
    task_config_path: str,
    eval_jsonl_path: str,
    run_dir: str,
    api_key_file: str | None = None,
    prompt_template: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    max_output_tokens: int | None = None,
    processing_tier: str = "standard",
    pricing_config_path: str = DEFAULT_PRICING_CONFIG_PATH,
    backend: OpenAIResponsesBackend | None = None,
) -> dict[str, Any]:
    task_config = load_llm_task_config(task_config_path)
    task_config = _override_task_config(
        task_config,
        prompt_template=prompt_template,
        model=model,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        max_output_tokens=max_output_tokens,
    )

    eval_rows = load_jsonl_rows(eval_jsonl_path)
    items = build_prompt_items(task_config, eval_rows)
    item_by_id = {item.item_id: item for item in items}
    eval_row_by_id = {item.item_id: row for item, row in zip(items, eval_rows, strict=True)}

    run_path = resolve_repo_path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    _write_items_jsonl(run_path / ITEMS_FILENAME, items, eval_row_by_id)
    _snapshot_prompt_template(task_config.prompt_template, run_path / PROMPT_SNAPSHOT_FILENAME)

    active_backend = backend or OpenAIResponsesBackend(api_key_file=api_key_file)
    results = active_backend.run_sync(task_config, items)

    raw_results_path = run_path / RAW_RESULTS_FILENAME
    parsed_results_path = run_path / PARSED_RESULTS_FILENAME
    scored_results_path = run_path / SCORED_RESULTS_FILENAME
    write_results_jsonl(str(raw_results_path), results)
    write_results_jsonl(str(parsed_results_path), results)

    scored_rows: list[dict[str, Any]] = []
    with scored_results_path.open("w", encoding="utf-8") as handle:
        for result in results:
            eval_row = eval_row_by_id[result.item_id]
            score = score_output(
                task_name=task_config.task_name,
                eval_row=eval_row,
                parsed=result.parsed,
                parse_error=result.parse_error,
            )
            scored_row = {
                "item_id": result.item_id,
                "prompt": item_by_id[result.item_id].prompt,
                "eval_row": eval_row,
                "raw_text": result.raw_text,
                "parsed": result.parsed,
                "usage": result.usage,
                **score,
            }
            scored_rows.append(scored_row)
            handle.write(json.dumps(scored_row, ensure_ascii=False) + "\n")

    score_summary = summarize_scores(scored_rows)
    usage_summary = summarize_results_jsonl(
        str(parsed_results_path),
        model=task_config.model,
        processing_tier=processing_tier,
        pricing_config_path=pricing_config_path,
    )

    summary = {
        "task_name": task_config.task_name,
        "task_config_path": str(task_config_path),
        "eval_jsonl_path": str(eval_jsonl_path),
        "run_dir": str(run_path),
        "effective_config": asdict(task_config),
        "processing_tier": processing_tier,
        "git_commit": _git_commit(),
        "api_key_source": getattr(active_backend, "api_key_source", None),
        "created_at_epoch": int(time()),
        "score": score_summary,
        "usage": usage_summary["usage"],
        "estimated_total_cost_usd": usage_summary["estimated_total_cost_usd"],
        "fail_item_ids": [row["item_id"] for row in scored_rows if not row["passed"]],
    }
    _write_json(run_path / SUMMARY_FILENAME, summary)
    _write_json(
        run_path / MANIFEST_FILENAME,
        {
            "run_schema": 1,
            "task_name": task_config.task_name,
            "task_config_path": str(task_config_path),
            "eval_jsonl_path": str(eval_jsonl_path),
            "effective_config": asdict(task_config),
            "processing_tier": processing_tier,
            "pricing_config_path": pricing_config_path,
            "created_at_epoch": int(time()),
        },
    )
    return summary


def compare_prompt_experiments(base_run_dir: str, candidate_run_dir: str) -> dict[str, Any]:
    base_path = resolve_repo_path(base_run_dir)
    candidate_path = resolve_repo_path(candidate_run_dir)

    base_summary = _load_json(base_path / SUMMARY_FILENAME)
    candidate_summary = _load_json(candidate_path / SUMMARY_FILENAME)
    base_rows = _load_scored_rows(base_path / SCORED_RESULTS_FILENAME)
    candidate_rows = _load_scored_rows(candidate_path / SCORED_RESULTS_FILENAME)

    base_by_id = {row["item_id"]: row for row in base_rows}
    candidate_by_id = {row["item_id"]: row for row in candidate_rows}
    changed_cases: list[dict[str, Any]] = []
    for item_id in sorted(set(base_by_id) | set(candidate_by_id)):
        base_row = base_by_id.get(item_id)
        candidate_row = candidate_by_id.get(item_id)
        if not base_row or not candidate_row:
            changed_cases.append(
                {
                    "item_id": item_id,
                    "change_type": "missing_case",
                    "base_present": bool(base_row),
                    "candidate_present": bool(candidate_row),
                }
            )
            continue

        base_passed = bool(base_row.get("passed"))
        candidate_passed = bool(candidate_row.get("passed"))
        base_actual = base_row.get("actual")
        candidate_actual = candidate_row.get("actual")
        if base_passed != candidate_passed or base_actual != candidate_actual:
            changed_cases.append(
                {
                    "item_id": item_id,
                    "change_type": _classify_change(base_passed, candidate_passed),
                    "expected": candidate_row.get("expected"),
                    "base_actual": base_actual,
                    "candidate_actual": candidate_actual,
                    "base_passed": base_passed,
                    "candidate_passed": candidate_passed,
                }
            )

    comparison = {
        "task_name": candidate_summary.get("task_name"),
        "base_run_dir": str(base_path),
        "candidate_run_dir": str(candidate_path),
        "base_score": base_summary.get("score"),
        "candidate_score": candidate_summary.get("score"),
        "score_delta": _score_delta(base_summary.get("score") or {}, candidate_summary.get("score") or {}),
        "base_usage": base_summary.get("usage"),
        "candidate_usage": candidate_summary.get("usage"),
        "estimated_cost_delta_usd": (candidate_summary.get("estimated_total_cost_usd") or 0.0)
        - (base_summary.get("estimated_total_cost_usd") or 0.0),
        "changed_case_count": len(changed_cases),
        "changed_cases": changed_cases,
    }
    _write_json(candidate_path / COMPARISON_FILENAME, comparison)
    return comparison


def _override_task_config(
    task_config,
    *,
    prompt_template: str | None,
    model: str | None,
    reasoning_effort: str | None,
    verbosity: str | None,
    max_output_tokens: int | None,
):
    return replace(
        task_config,
        prompt_template=prompt_template or task_config.prompt_template,
        model=model or task_config.model,
        reasoning_effort=reasoning_effort if reasoning_effort is not None else task_config.reasoning_effort,
        verbosity=verbosity if verbosity is not None else task_config.verbosity,
        max_output_tokens=max_output_tokens if max_output_tokens is not None else task_config.max_output_tokens,
    )


def _write_items_jsonl(path: Path, items, eval_row_by_id: dict[str, dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(
                json.dumps(
                    {
                        "item_id": item.item_id,
                        "prompt": item.prompt,
                        "eval_row": eval_row_by_id[item.item_id],
                        "metadata": item.metadata,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _snapshot_prompt_template(prompt_template_path: str, output_path: Path) -> None:
    prompt_path = resolve_repo_path(prompt_template_path)
    output_path.write_text(prompt_path.read_text(encoding="utf-8"), encoding="utf-8")


def _load_scored_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def _score_delta(base_score: dict[str, Any], candidate_score: dict[str, Any]) -> dict[str, Any]:
    return {
        "accuracy": (candidate_score.get("accuracy") or 0.0) - (base_score.get("accuracy") or 0.0),
        "pass_count": int(candidate_score.get("pass_count") or 0) - int(base_score.get("pass_count") or 0),
        "fail_count": int(candidate_score.get("fail_count") or 0) - int(base_score.get("fail_count") or 0),
        "parse_error_count": int(candidate_score.get("parse_error_count") or 0)
        - int(base_score.get("parse_error_count") or 0),
    }


def _classify_change(base_passed: bool, candidate_passed: bool) -> str:
    if not base_passed and candidate_passed:
        return "fixed"
    if base_passed and not candidate_passed:
        return "regressed"
    return "changed_prediction"


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return None
    return completed.stdout.strip() or None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
