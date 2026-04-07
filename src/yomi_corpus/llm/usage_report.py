from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yomi_corpus.llm.pricing import estimate_cost_usd
from yomi_corpus.paths import resolve_repo_path


def summarize_results_jsonl(
    results_jsonl_path: str,
    *,
    model: str,
    processing_tier: str,
    pricing_config_path: str,
) -> dict[str, Any]:
    totals = _empty_usage_totals()
    item_count = 0
    priced_item_count = 0
    total_cost_usd = 0.0

    with resolve_repo_path(results_jsonl_path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            item_count += 1
            usage = row.get("usage")
            _accumulate_usage(totals, usage)
            estimate = estimate_cost_usd(
                usage,
                model=model,
                processing_tier=processing_tier,
                pricing_config_path=pricing_config_path,
            )
            if estimate:
                priced_item_count += 1
                total_cost_usd += estimate.estimated_total_cost_usd

    return {
        "scope": "results_jsonl",
        "model": model,
        "processing_tier": processing_tier,
        "item_count": item_count,
        "priced_item_count": priced_item_count,
        "usage": totals,
        "estimated_total_cost_usd": round(total_cost_usd, 10),
    }


def summarize_batch_job(
    job_dir: str,
    *,
    pricing_config_path: str,
) -> dict[str, Any]:
    job_path = resolve_repo_path(job_dir)
    manifest = _load_json(job_path / "manifest.json")
    status = _load_json(job_path / "status.json")
    parsed_results_path = job_path / "results.parsed.jsonl"

    summary = {
        "scope": "batch_job",
        "job_dir": str(job_path),
        "task_name": manifest.get("task_name"),
        "model": manifest.get("model"),
        "processing_tier": "batch",
        "state": status.get("state"),
        "remote_status": status.get("remote_status"),
        "batch_id": status.get("batch_id"),
        "api_batch_usage": status.get("remote_snapshot", {}).get("usage"),
    }

    if parsed_results_path.exists():
        parsed_summary = summarize_results_jsonl(
            str(parsed_results_path),
            model=str(manifest["model"]),
            processing_tier="batch",
            pricing_config_path=pricing_config_path,
        )
        summary.update(
            {
                "item_count": parsed_summary["item_count"],
                "priced_item_count": parsed_summary["priced_item_count"],
                "usage": parsed_summary["usage"],
                "estimated_total_cost_usd": parsed_summary["estimated_total_cost_usd"],
            }
        )
    else:
        summary.update(
            {
                "item_count": int(manifest.get("item_count", 0)),
                "priced_item_count": 0,
                "usage": _empty_usage_totals(),
                "estimated_total_cost_usd": 0.0,
            }
        )

    return summary


def _accumulate_usage(totals: dict[str, int], usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    for key in totals:
        totals[key] += int(usage.get(key) or 0)


def _empty_usage_totals() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
