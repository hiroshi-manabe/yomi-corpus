from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.adapters import collapse_empty_surface_sudachi_tokens
from yomi_corpus.yomi.source_mapping import SourceSurfaceMappingError, SourceTextMapping
from yomi_corpus.yomi.token_codec import (
    canonicalize_whitespace_readings,
    normalize_yomi_tokens,
    validate_yomi_token_surfaces,
)
from yomi_corpus.yomi.types import SudachiToken


MIGRATION_ID = "source_surface_preservation_v1"


def migrate_source_surfaces(
    *,
    root: Path,
    apply: bool,
    report_json: Path,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    units_root = root / "data" / "units"
    paths = sorted(
        path
        for name in (
            "units.yomi.aligned_hybrid.jsonl",
            "units.yomi.reviewed.jsonl",
            "units.yomi.final.jsonl",
        )
        for path in units_root.glob(f"*/{name}")
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
        file_report, temp_path = prepare_source_surface_file(path, apply=apply)
        report["files"].append(file_report)
        report["anomalies"].extend(file_report["anomalies"])
        if temp_path is not None:
            staged.append((path, temp_path))

    report["file_count"] = len(paths)
    report["unit_count"] = sum(int(row["unit_count"]) for row in report["files"])
    report["changed_unit_count"] = sum(
        int(row["changed_unit_count"]) for row in report["files"]
    )
    report["empty_sudachi_token_count"] = sum(
        int(row["empty_sudachi_token_count"]) for row in report["files"]
    )
    report["restored_surface_count"] = sum(
        int(row["restored_surface_count"]) for row in report["files"]
    )
    report["cleared_whitespace_reading_count"] = sum(
        int(row["cleared_whitespace_reading_count"]) for row in report["files"]
    )
    report["anomaly_count"] = len(report["anomalies"])

    if report["anomalies"]:
        remove_staged_files(staged)
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
        remove_staged_files(staged)
        report["applied"] = False

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def prepare_source_surface_file(
    path: Path,
    *,
    apply: bool,
) -> tuple[dict[str, Any], Path | None]:
    temp_path = path.with_suffix(path.suffix + ".source-surfaces-v1.tmp") if apply else None
    source_hash = hashlib.sha256()
    output_hash = hashlib.sha256()
    counters = {
        "unit_count": 0,
        "changed_unit_count": 0,
        "empty_sudachi_token_count": 0,
        "restored_surface_count": 0,
        "cleared_whitespace_reading_count": 0,
    }
    anomalies: list[dict[str, Any]] = []
    output = temp_path.open("w", encoding="utf-8") if temp_path is not None else None
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                source_hash.update(line.encode("utf-8"))
                if not line.strip():
                    continue
                counters["unit_count"] += 1
                row: Any = None
                try:
                    row = json.loads(line)
                    changed, row_counts = migrate_source_surface_row(row)
                    counters["changed_unit_count"] += int(changed)
                    for key, value in row_counts.items():
                        counters[key] += value
                    if output is not None:
                        output_line = json.dumps(row, ensure_ascii=False) + "\n"
                        output_hash.update(output_line.encode("utf-8"))
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
            **counters,
            "source_sha256": source_hash.hexdigest(),
            "output_sha256": output_hash.hexdigest(),
            "anomalies": anomalies,
        },
        temp_path,
    )


def migrate_source_surface_row(row: dict[str, Any]) -> tuple[bool, dict[str, int]]:
    counts = {
        "empty_sudachi_token_count": 0,
        "restored_surface_count": 0,
        "cleared_whitespace_reading_count": 0,
    }
    text = str(row.get("text") or "")
    yomi = (
        row.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
    )
    if not isinstance(yomi, dict):
        return False, counts

    compact = yomi.get("tokens")
    if compact is not None:
        tokens = normalize_yomi_tokens(compact)
        validate_yomi_token_surfaces(tokens, text=text)
        canonical = canonicalize_whitespace_readings(tokens)
        counts["cleared_whitespace_reading_count"] += sum(
            1
            for (surface, old_reading), (_same_surface, new_reading) in zip(
                tokens, canonical, strict=True
            )
            if surface.isspace() and old_reading != new_reading
        )
        yomi["tokens"] = canonical

    sudachi = yomi.get("sudachi")
    if isinstance(sudachi, dict) and isinstance(sudachi.get("tokens"), list):
        raw_tokens = [sudachi_token_from_mapping(token) for token in sudachi["tokens"]]
        counts["empty_sudachi_token_count"] += sum(
            1 for token in raw_tokens if not token.surface
        )
        collapsed = collapse_empty_surface_sudachi_tokens(raw_tokens)
        restored, restored_count = restore_partition_surfaces(
            [token.surface for token in collapsed],
            source_text=text,
            stage="stored Sudachi output",
        )
        counts["restored_surface_count"] += restored_count
        sudachi["tokens"] = [
            {
                **asdict(token),
                "surface": surface,
            }
            for token, surface in zip(collapsed, restored, strict=True)
        ]

    decoder = yomi.get("ngram_decoder")
    candidates = decoder.get("candidates") if isinstance(decoder, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("entries"), list):
                continue
            entries = candidate["entries"]
            surfaces = [str(entry.get("surface") or "") for entry in entries]
            restored, restored_count = restore_partition_surfaces(
                surfaces,
                source_text=text,
                stage=f"stored decoder candidate {candidate.get('rank')}",
            )
            counts["restored_surface_count"] += restored_count
            for entry, surface in zip(entries, restored, strict=True):
                entry["surface"] = surface

    return any(counts.values()), counts


def restore_partition_surfaces(
    surfaces: list[str],
    *,
    source_text: str,
    stage: str,
) -> tuple[list[str], int]:
    analysis_text = "".join(surfaces)
    mapping = SourceTextMapping(source_text=source_text, analysis_text=analysis_text)
    restored = mapping.restore_partition(surfaces, stage=stage)
    return restored, sum(left != right for left, right in zip(surfaces, restored, strict=True))


def sudachi_token_from_mapping(value: Any) -> SudachiToken:
    if not isinstance(value, dict):
        raise TypeError("stored Sudachi token must be an object")
    return SudachiToken(
        surface=str(value.get("surface") or ""),
        pos=str(value.get("pos") or ""),
        dictionary_form=str(value.get("dictionary_form") or ""),
        normalized_form=str(value.get("normalized_form") or ""),
        reading=str(value.get("reading") or ""),
        normalization_locked=bool(value.get("normalization_locked", False)),
    )


def remove_staged_files(staged: list[tuple[Path, Path]]) -> None:
    for _path, temp_path in staged:
        temp_path.unlink(missing_ok=True)
