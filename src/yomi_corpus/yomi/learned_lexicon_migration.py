from __future__ import annotations

import csv
import io
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.final_review import (
    harvest_learned_yomi_readings,
    harvest_manual_yomi_rewrites,
    learned_yomi_reading_fields,
    load_jsonl,
    load_strong_repair_queue_by_item_id,
)


MIGRATION_ID = "learned_yomi_lexicon_v1"


def rebuild_learned_yomi_lexicons(
    *,
    root: Path,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    batch_reports: list[dict[str, Any]] = []
    all_rewrites: list[dict[str, Any]] = []
    all_readings: list[dict[str, str]] = []
    writes: dict[Path, str] = {}
    for final_path in sorted((root / "data" / "units").glob("*/units.yomi.final.jsonl")):
        batch_dir = final_path.parent
        batch_name = batch_dir.name
        track_name = batch_name.split("_batch_", 1)[0]
        units = load_jsonl(final_path)
        queue_by_item_id = load_strong_repair_queue_by_item_id(
            batch_dir / "yomi_strong_repair_queue.jsonl"
        )
        rewrites = harvest_manual_yomi_rewrites(
            units,
            batch_name=batch_name,
            track_name=track_name,
            queue_by_item_id=queue_by_item_id,
        )
        readings = harvest_learned_yomi_readings(
            units,
            batch_name=batch_name,
            track_name=track_name,
        )
        writes[batch_dir / "manual_yomi_rewrites.jsonl"] = render_jsonl(rewrites)
        writes[batch_dir / "learned_yomi_readings.tsv"] = render_tsv(
            readings,
            learned_yomi_reading_fields(),
        )
        all_rewrites.extend(rewrites)
        all_readings.extend(readings)
        batch_reports.append(
            {
                "batch_name": batch_name,
                "final_unit_count": len(units),
                "queue_item_count": len(queue_by_item_id),
                "exact_rewrite_evidence_count": len(rewrites),
                "learned_reading_evidence_count": len(readings),
            }
        )

    canonical_rewrites, conflicts = consolidate_exact_rewrites(all_rewrites)
    canonical_readings = sorted(
        all_readings,
        key=lambda row: (
            row["surface"],
            row["reading"],
            row["source_batch"],
            row["source_unit_id"],
            row["source_item_id"],
        ),
    )
    writes[root / "data" / "lexicon" / "manual_yomi_rewrites.jsonl"] = render_jsonl(
        canonical_rewrites
    )
    writes[root / "data" / "lexicon" / "learned_yomi_readings.tsv"] = render_tsv(
        canonical_readings,
        learned_yomi_reading_fields(),
    )

    report: dict[str, Any] = {
        "migration_id": MIGRATION_ID,
        "mode": "apply" if apply else "dry_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_count": len(batch_reports),
        "batches": batch_reports,
        "exact_rewrite_evidence_count": len(all_rewrites),
        "exact_rewrite_count": len(canonical_rewrites),
        "exact_rewrite_conflict_count": len(conflicts),
        "exact_rewrite_conflicts": conflicts,
        "learned_reading_evidence_count": len(canonical_readings),
        "learned_surface_reading_count": len(
            {(row["surface"], row["reading"]) for row in canonical_readings}
        ),
        "applied": False,
    }
    if apply:
        if backup_root is None:
            raise ValueError("backup_root is required in apply mode")
        for path in sorted(writes):
            if path.exists():
                backup_path = backup_root / path.relative_to(root)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
        for path, content in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(path.suffix + ".learned-v1.tmp")
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
        report["applied"] = True
        report["backup_root"] = str(backup_root)

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def consolidate_exact_rewrites(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[str(row["original_surface"])][str(row["replacement_rendered"])].append(row)
    output: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for original_surface in sorted(grouped):
        variants = grouped[original_surface]
        if len(variants) != 1:
            conflicts.append(
                {
                    "original_surface": original_surface,
                    "variants": [
                        {
                            "replacement_rendered": replacement,
                            "evidence_count": len(evidence),
                            "sources": [source_reference(row) for row in evidence],
                        }
                        for replacement, evidence in sorted(variants.items())
                    ],
                }
            )
            continue
        replacement_rendered, evidence = next(iter(variants.items()))
        representative = sorted(
            evidence,
            key=lambda row: (
                str(row.get("source_batch") or ""),
                str(row.get("source_unit_id") or ""),
                str(row.get("source_item_id") or ""),
            ),
        )[0]
        output.append(
            {
                **representative,
                "replacement_rendered": replacement_rendered,
                "evidence_count": len(evidence),
                "evidence": [source_reference(row) for row in evidence],
            }
        )
    return output, conflicts


def source_reference(row: dict[str, Any]) -> dict[str, str]:
    return {
        "source": str(row.get("source") or ""),
        "source_batch": str(row.get("source_batch") or ""),
        "source_track": str(row.get("source_track") or ""),
        "source_unit_id": str(row.get("source_unit_id") or ""),
        "source_item_id": str(row.get("source_item_id") or ""),
    }


def render_jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def render_tsv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    return output.getvalue()
