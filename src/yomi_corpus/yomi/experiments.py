from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from time import time

from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


def load_eval_items(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def run_yomi_experiment(
    *,
    eval_items: list[dict],
    config: YomiGenerationConfig,
    strategy_name: str,
    run_dir: str | Path,
) -> dict:
    output_dir = Path(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    predictions = []
    scored = []
    exact_match_count = 0
    for item in eval_items:
        mechanical_yomi = generate_mechanical_yomi(
            item["text"],
            config=config,
            strategy_name=strategy_name,
        )
        prediction = {
            "item_id": item["item_id"],
            "text": item["text"],
            "predicted_rendered": mechanical_yomi.rendered,
            "certain": mechanical_yomi.certain,
            "signals": mechanical_yomi.signals,
            "mechanical_yomi": asdict(mechanical_yomi),
        }
        predictions.append(prediction)

        expected = item.get("expected_rendered")
        matched = expected == mechanical_yomi.rendered if isinstance(expected, str) else None
        if matched is True:
            exact_match_count += 1
        scored.append(
            {
                "item_id": item["item_id"],
                "text": item["text"],
                "expected_rendered": expected,
                "predicted_rendered": mechanical_yomi.rendered,
                "exact_match": matched,
                "signals": mechanical_yomi.signals,
            }
        )

    comparable_count = sum(1 for row in scored if row["exact_match"] is not None)
    summary = {
        "strategy_name": strategy_name,
        "item_count": len(eval_items),
        "comparable_count": comparable_count,
        "exact_match_count": exact_match_count,
        "exact_match_accuracy": (
            exact_match_count / comparable_count if comparable_count else None
        ),
        "metric_note": "Current metric is strict rendered-string exact match; real acceptance should tolerate over-segmentation when readings are still correct.",
        "generated_at_epoch": int(time()),
    }

    write_jsonl(output_dir / "items.jsonl", eval_items)
    write_jsonl(output_dir / "predictions.jsonl", predictions)
    write_jsonl(output_dir / "scored.jsonl", scored)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def compare_yomi_experiments(
    *,
    base_run_dir: str | Path,
    candidate_run_dir: str | Path,
) -> dict:
    base_summary = json.loads((Path(base_run_dir) / "summary.json").read_text(encoding="utf-8"))
    candidate_summary = json.loads((Path(candidate_run_dir) / "summary.json").read_text(encoding="utf-8"))
    base_scored = {row["item_id"]: row for row in load_eval_items(Path(base_run_dir) / "scored.jsonl")}
    candidate_scored = {
        row["item_id"]: row for row in load_eval_items(Path(candidate_run_dir) / "scored.jsonl")
    }
    changed = []
    for item_id in sorted(set(base_scored) | set(candidate_scored)):
        base_row = base_scored.get(item_id)
        candidate_row = candidate_scored.get(item_id)
        if (
            base_row
            and candidate_row
            and base_row.get("exact_match") == candidate_row.get("exact_match")
            and base_row.get("predicted_rendered") == candidate_row.get("predicted_rendered")
        ):
            continue
        if base_row is None and candidate_row is None:
            continue
        changed.append(
            {
                "item_id": item_id,
                "base_exact_match": None if not base_row else base_row.get("exact_match"),
                "candidate_exact_match": None if not candidate_row else candidate_row.get("exact_match"),
                "base_predicted_rendered": None if not base_row else base_row.get("predicted_rendered"),
                "candidate_predicted_rendered": None if not candidate_row else candidate_row.get("predicted_rendered"),
            }
        )
    return {
        "base_summary": base_summary,
        "candidate_summary": candidate_summary,
        "changed_items": changed,
    }


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    output_path = Path(path)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
