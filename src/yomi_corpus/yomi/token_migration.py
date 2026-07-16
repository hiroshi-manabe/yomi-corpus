from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.final_review import canonicalize_finalized_unit_yomi


MIGRATION_ID = "finalized_yomi_tokens_v1"


def migrate_finalized_yomi_tokens(
    *,
    root: Path,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    paths = sorted((root / "data" / "units").glob("*/units.yomi.final.jsonl"))
    report: dict[str, Any] = {
        "migration_id": MIGRATION_ID,
        "mode": "apply" if apply else "dry_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
        "anomalies": [],
    }
    staged: list[tuple[Path, Path]] = []
    for path in paths:
        file_report, temp_path = prepare_migrated_file(path, apply=apply)
        report["files"].append(file_report)
        report["anomalies"].extend(file_report["anomalies"])
        if temp_path is not None:
            staged.append((path, temp_path))

    report["file_count"] = len(paths)
    report["unit_count"] = sum(int(row["unit_count"]) for row in report["files"])
    report["changed_unit_count"] = sum(int(row["changed_unit_count"]) for row in report["files"])
    report["anomaly_count"] = len(report["anomalies"])

    if report["anomalies"]:
        for _path, temp_path in staged:
            temp_path.unlink(missing_ok=True)
        report["applied"] = False
    elif apply:
        if backup_root is None:
            raise ValueError("backup_root is required in apply mode")
        for path, _temp_path in staged:
            relative = path.relative_to(root)
            backup_path = backup_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(path, backup_path)
        for path, temp_path in staged:
            temp_path.replace(path)
        report["applied"] = True
        report["backup_root"] = str(backup_root)
    else:
        report["applied"] = False

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def prepare_migrated_file(path: Path, *, apply: bool) -> tuple[dict[str, Any], Path | None]:
    temp_path = path.with_suffix(path.suffix + ".tokens-v1.tmp") if apply else None
    source_hash = hashlib.sha256()
    output_hash = hashlib.sha256()
    unit_count = 0
    changed_unit_count = 0
    anomalies: list[dict[str, Any]] = []
    output = temp_path.open("w", encoding="utf-8") if temp_path is not None else None
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                source_hash.update(line.encode("utf-8"))
                if not line.strip():
                    continue
                unit_count += 1
                row: Any = None
                try:
                    row = json.loads(line)
                    before = json.dumps(row, ensure_ascii=False, sort_keys=True)
                    canonicalize_finalized_unit_yomi(row)
                    after = json.dumps(row, ensure_ascii=False, sort_keys=True)
                    changed_unit_count += int(before != after)
                    output_line = json.dumps(row, ensure_ascii=False) + "\n"
                    output_hash.update(output_line.encode("utf-8"))
                    if output is not None:
                        output.write(output_line)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    anomalies.append(
                        {
                            "path": str(path),
                            "line_number": line_number,
                            "unit_id": row.get("unit_id") if isinstance(row, dict) else None,
                            "error": str(exc),
                        }
                    )
    finally:
        if output is not None:
            output.close()
    return (
        {
            "path": str(path),
            "unit_count": unit_count,
            "changed_unit_count": changed_unit_count,
            "source_sha256": source_hash.hexdigest(),
            "output_sha256": output_hash.hexdigest(),
            "anomalies": anomalies,
        },
        temp_path,
    )
