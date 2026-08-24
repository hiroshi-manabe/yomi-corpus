from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.repairs import normalize_canonical_compound_tokens
from yomi_corpus.yomi.token_codec import (
    set_canonical_yomi_tokens,
    yomi_tokens_from_mapping,
)


MIGRATION_ID = "canonical_compound_tokens_v1"
AUTHORITATIVE_FILENAMES = (
    "units.yomi.final.jsonl",
    "units.yomi.skipped.jsonl",
)


def migrate_canonical_compound_tokens(
    *,
    root: Path,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    paths = sorted(
        path
        for filename in AUTHORITATIVE_FILENAMES
        for path in (root / "data" / "units").glob(f"*/{filename}")
    )
    report: dict[str, Any] = {
        "migration_id": MIGRATION_ID,
        "mode": "apply" if apply else "dry_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
        "anomalies": [],
    }
    staged: list[tuple[Path, Path]] = []
    for path in paths:
        file_report, temp_path = prepare_file(path, apply=apply)
        report["files"].append(file_report)
        report["anomalies"].extend(file_report["anomalies"])
        if temp_path is not None:
            staged.append((path, temp_path))

    report["file_count"] = len(paths)
    report["changed_file_count"] = sum(
        1 for row in report["files"] if int(row["changed_unit_count"]) > 0
    )
    report["unit_count"] = sum(int(row["unit_count"]) for row in report["files"])
    report["changed_unit_count"] = sum(
        int(row["changed_unit_count"]) for row in report["files"]
    )
    report["merged_occurrence_count"] = sum(
        int(row["merged_occurrence_count"]) for row in report["files"]
    )
    report["anomaly_count"] = len(report["anomalies"])

    if report["anomalies"]:
        remove_staged_files(staged)
        report["applied"] = False
    elif apply:
        if backup_root is None:
            raise ValueError("backup_root is required in apply mode")
        for path, _temp_path in staged:
            backup_path = backup_root / path.relative_to(root)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(path, backup_path)
        for path, temp_path in staged:
            temp_path.replace(path)
        report["applied"] = True
        report["backup_root"] = str(backup_root)
    else:
        remove_staged_files(staged)
        report["applied"] = False

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def prepare_file(path: Path, *, apply: bool) -> tuple[dict[str, Any], Path | None]:
    source_hash = hashlib.sha256()
    output_hash = hashlib.sha256()
    output_lines: list[str] = []
    unit_count = 0
    changed_unit_count = 0
    merged_occurrence_count = 0
    anomalies: list[dict[str, Any]] = []

    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            source_hash.update(line.encode("utf-8"))
            if not line.strip():
                continue
            unit_count += 1
            row: Any = None
            try:
                row = json.loads(line)
                changed, merged = normalize_row(row)
                changed_unit_count += int(changed)
                merged_occurrence_count += merged
                output_line = json.dumps(row, ensure_ascii=False) + "\n"
                output_hash.update(output_line.encode("utf-8"))
                output_lines.append(output_line)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                anomalies.append(
                    {
                        "path": str(path),
                        "line_number": line_number,
                        "unit_id": row.get("unit_id") if isinstance(row, dict) else None,
                        "error": str(exc),
                    }
                )

    temp_path = None
    if apply and changed_unit_count and not anomalies:
        temp_path = path.with_suffix(path.suffix + f".{MIGRATION_ID}.tmp")
        temp_path.write_text("".join(output_lines), encoding="utf-8")
    return (
        {
            "path": str(path),
            "unit_count": unit_count,
            "changed_unit_count": changed_unit_count,
            "merged_occurrence_count": merged_occurrence_count,
            "source_sha256": source_hash.hexdigest(),
            "output_sha256": output_hash.hexdigest(),
            "anomalies": anomalies,
        },
        temp_path,
    )


def normalize_row(row: dict[str, Any]) -> tuple[bool, int]:
    yomi = (
        row.get("analysis", {})
        .get("mechanical", {})
        .get("yomi")
    )
    if not isinstance(yomi, dict):
        return False, 0
    tokens = yomi_tokens_from_mapping(yomi, text=str(row.get("text") or ""))
    normalized = normalize_canonical_compound_tokens(tokens)
    merged = len(tokens) - len(normalized)
    if merged <= 0:
        return False, 0
    set_canonical_yomi_tokens(yomi, normalized)
    return True, merged


def remove_staged_files(staged: list[tuple[Path, Path]]) -> None:
    for _path, temp_path in staged:
        temp_path.unlink(missing_ok=True)
