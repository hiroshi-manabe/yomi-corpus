from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from yomi_corpus.models import MechanicalYomi
from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.final_review import (
    load_json,
    load_review_submissions,
    replay_review_submissions,
)
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


MIGRATION_NAME = "skipped_hybrid_yomi_v1"


def backfill_skipped_hybrid_yomi(
    *,
    root: Path,
    track_name: str = "dev",
    apply: bool = False,
    batch_names: set[str] | None = None,
    generator: Callable[..., MechanicalYomi] = generate_mechanical_yomi,
) -> dict[str, Any]:
    prefix = "dev_batch_" if track_name == "dev" else "batch_"
    state_root = root / "data" / "pipeline" / "batches"
    migration_root = root / "data" / "state" / "migrations" / MIGRATION_NAME
    generated_at = datetime.now(timezone.utc).isoformat()
    batches: list[dict[str, Any]] = []
    total_scope_skips = 0
    total_restored_scope_skips = 0
    total_human_skips = 0
    total_generated = 0
    total_archived = 0
    total_archived_human = 0
    total_failures = 0

    for state_path in sorted(state_root.glob(f"{prefix}*.json")):
        state = load_json_object(state_path)
        batch_name = str(state.get("batch_name") or state_path.stem)
        if batch_names is not None and batch_name not in batch_names:
            continue
        batch_dir = root / "data" / "units" / batch_name
        scope_path = batch_dir / "units.scope_triaged.jsonl"
        if not scope_path.exists():
            continue
        scope_rows = load_jsonl(scope_path)
        scope_skips = [row for row in scope_rows if is_scope_skip(row)]

        aligned_path = batch_dir / "units.yomi.aligned_hybrid.jsonl"
        reviewed_path = batch_dir / "units.yomi.reviewed.jsonl"
        skipped_path = batch_dir / "units.yomi.skipped.jsonl"
        final_path = batch_dir / "units.yomi.final.jsonl"
        aligned_rows = load_jsonl(aligned_path) if aligned_path.exists() else []
        reviewed_rows = load_jsonl(reviewed_path) if reviewed_path.exists() else []
        skipped_rows = load_jsonl(skipped_path) if skipped_path.exists() else []
        final_rows = load_jsonl(final_path) if final_path.exists() else []
        aligned_by_id = rows_by_unit_id(aligned_rows, artifact=aligned_path)
        reviewed_by_id = rows_by_unit_id(reviewed_rows, artifact=reviewed_path)
        skipped_by_id = rows_by_unit_id(skipped_rows, artifact=skipped_path)
        final_ids = set(rows_by_unit_id(final_rows, artifact=final_path))
        finalized = str(state.get("current_stage") or "") == "yomi_finalized"
        human_skip_ids = effective_human_skip_ids(
            root=root,
            state=state,
            batch_name=batch_name,
        ) if finalized else set()
        if not scope_skips and not human_skip_ids:
            continue
        scope_skips_by_id = {
            str(row.get("unit_id") or ""): row for row in scope_skips
        }
        restored_scope_skip_ids = (set(scope_skips_by_id) & final_ids) - human_skip_ids
        candidate_ids = (set(scope_skips_by_id) - restored_scope_skip_ids) | human_skip_ids
        decoder_model_dir = str(
            state.get("decoder_model_dir")
            or state.get("artifacts", {}).get("decoder_model_dir")
            or ""
        )
        config = load_yomi_generation_config(root / "config" / "yomi" / "default.toml")
        if decoder_model_dir:
            config = replace(config, decoder_model_dir=decoder_model_dir)

        generated_rows: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        dispositions: list[dict[str, str]] = []
        for unit_id in sorted(candidate_ids):
            source_row = reviewed_by_id.get(unit_id) or scope_skips_by_id.get(unit_id)
            if source_row is None:
                source_row = aligned_by_id.get(unit_id)
            if source_row is None:
                failures.append({"unit_id": unit_id, "error": "missing_source_unit"})
                continue
            if not unit_id:
                failures.append({"unit_id": "", "error": "missing_unit_id"})
                continue
            existing = aligned_by_id.get(unit_id)
            if existing is not None:
                if str(existing.get("text") or "") != str(source_row.get("text") or ""):
                    failures.append({"unit_id": unit_id, "error": "aligned_text_conflict"})
                    continue
                generated_rows[unit_id] = existing
                dispositions.append(
                    {
                        "unit_id": unit_id,
                        "status": (
                            "existing_reviewed_human_skip"
                            if unit_id in human_skip_ids and unit_id in reviewed_by_id
                            else "existing_hybrid"
                        ),
                    }
                )
                continue
            try:
                generated = deepcopy(source_row)
                generated.setdefault("analysis", {}).setdefault("mechanical", {})["yomi"] = asdict(
                    generator(
                        str(source_row.get("text") or ""),
                        config=config,
                        strategy_name="aligned_hybrid_v1",
                    )
                )
                generated.setdefault("analysis", {}).setdefault("pipeline", {})[
                    "yomi_processing"
                ] = {
                    "status": "backfilled",
                    "migration": MIGRATION_NAME,
                    "strategy_name": "aligned_hybrid_v1",
                    "decoder_model_dir": decoder_model_dir or None,
                    "generated_at": generated_at,
                }
                generated_rows[unit_id] = generated
                dispositions.append({"unit_id": unit_id, "status": "generated_hybrid"})
            except Exception as exc:  # Keep independent historical failures auditable.
                failures.append({"unit_id": unit_id, "error": f"{type(exc).__name__}: {exc}"})

        archived_count = 0
        archived_human_count = 0
        if finalized:
            for unit_id in sorted(candidate_ids):
                generated = generated_rows.get(unit_id)
                if generated is None:
                    continue
                if unit_id in final_ids:
                    failures.append({"unit_id": unit_id, "error": "already_in_final_corpus"})
                    continue
                existing_skip = skipped_by_id.get(unit_id)
                if existing_skip is not None:
                    if str(existing_skip.get("text") or "") != str(generated.get("text") or ""):
                        failures.append({"unit_id": unit_id, "error": "skipped_text_conflict"})
                    continue
                if unit_id in human_skip_ids and unit_id in reviewed_by_id:
                    skipped_by_id[unit_id] = deepcopy(reviewed_by_id[unit_id])
                    archived_human_count += 1
                else:
                    skipped_by_id[unit_id] = confirmed_legacy_skip(
                        generated,
                        generated_at=generated_at,
                    )
                archived_count += 1

        if not failures and apply:
            backup_artifacts(
                migration_root=migration_root,
                batch_name=batch_name,
                paths=[aligned_path, skipped_path],
            )
            merged_aligned = merge_in_scope_order(scope_rows, aligned_by_id | generated_rows)
            write_jsonl_atomic(aligned_path, merged_aligned)
            if finalized:
                write_jsonl_atomic(
                    skipped_path,
                    sorted(
                        skipped_by_id.values(),
                        key=lambda row: (int(row.get("track_doc_seq") or 0), int(row.get("unit_seq") or 0)),
                    ),
                )

        batch_summary = {
            "batch_name": batch_name,
            "current_stage": state.get("current_stage"),
            "finalized": finalized,
            "scope_skip_count": len(scope_skips),
            "restored_scope_skip_count": len(restored_scope_skip_ids),
            "human_review_skip_count": len(human_skip_ids),
            "generated_hybrid_count": sum(
                row["status"] == "generated_hybrid" for row in dispositions
            ),
            "existing_hybrid_count": sum(
                row["status"] == "existing_hybrid" for row in dispositions
            ),
            "archived_skip_count": archived_count,
            "archived_human_skip_count": archived_human_count,
            "failure_count": len(failures),
            "decoder_model_dir": decoder_model_dir or None,
            "dispositions": dispositions,
            "failures": failures,
        }
        batches.append(batch_summary)
        total_scope_skips += len(scope_skips)
        total_restored_scope_skips += len(restored_scope_skip_ids)
        total_human_skips += len(human_skip_ids)
        total_generated += int(batch_summary["generated_hybrid_count"])
        total_archived += archived_count
        total_archived_human += archived_human_count
        total_failures += len(failures)

    summary = {
        "schema_version": 1,
        "migration": MIGRATION_NAME,
        "track_name": track_name,
        "generated_at": generated_at,
        "apply": apply,
        "batch_count": len(batches),
        "scope_skip_count": total_scope_skips,
        "restored_scope_skip_count": total_restored_scope_skips,
        "human_review_skip_count": total_human_skips,
        "generated_hybrid_count": total_generated,
        "archived_skip_count": total_archived,
        "archived_human_skip_count": total_archived_human,
        "failure_count": total_failures,
        "stage_complete": total_failures == 0,
        "batches": batches,
    }
    if apply:
        migration_root.mkdir(parents=True, exist_ok=True)
        manifest_path = migration_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary["manifest"] = str(manifest_path)
    return summary


def effective_human_skip_ids(
    *,
    root: Path,
    state: dict[str, Any],
    batch_name: str,
) -> set[str]:
    pack_path = final_review_pack_path(root=root, state=state, batch_name=batch_name)
    if pack_path is None:
        return set()
    pack = load_json(pack_path)
    submissions = load_review_submissions(
        root / "data" / "review_submissions" / "yomi_final",
        pack_id=str(pack.get("pack_id") or ""),
    )
    if not submissions:
        return set()
    effective = replay_review_submissions(pack, submissions)
    return {
        unit_id
        for unit_id, decision in effective.items()
        if bool(decision.get("skip"))
    }


def final_review_pack_path(
    *,
    root: Path,
    state: dict[str, Any],
    batch_name: str,
) -> Path | None:
    configured = state.get("artifacts", {}).get("final_review_pack_json")
    candidates = [
        Path(str(configured)) if configured else None,
        root / "data" / "units" / batch_name / "final_review_pack.json",
        root / "data" / "review_packs" / "yomi_final" / f"yomi_final_{batch_name}_v1.json",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.exists():
            return candidate
    return None


def confirmed_legacy_skip(row: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    result = deepcopy(row)
    human_review = result.setdefault("analysis", {}).setdefault("human_review", {})
    human_review["yomi_final"] = {
        "reviewed": True,
        "skip": True,
        "source": "legacy_scope_triage_backfill",
        "submission_id": f"{MIGRATION_NAME}:{result.get('unit_id', '')}",
        "generated_at": generated_at,
    }
    return result


def merge_in_scope_order(
    scope_rows: list[dict[str, Any]], rows_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in scope_rows:
        unit_id = str(source.get("unit_id") or "")
        row = rows_by_id.get(unit_id)
        if row is not None:
            merged.append(row)
            seen.add(unit_id)
    merged.extend(row for unit_id, row in rows_by_id.items() if unit_id not in seen)
    return merged


def is_scope_skip(row: dict[str, Any]) -> bool:
    return (
        row.get("analysis", {})
        .get("llm", {})
        .get("scope_triage", {})
        .get("status")
        == "Skip"
    )


def rows_by_unit_id(rows: list[dict[str, Any]], *, artifact: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        unit_id = str(row.get("unit_id") or "")
        if not unit_id or unit_id in result:
            raise ValueError(f"Invalid or duplicate unit_id {unit_id!r} in {artifact}")
        result[unit_id] = row
    return result


def backup_artifacts(*, migration_root: Path, batch_name: str, paths: list[Path]) -> None:
    backup_dir = migration_root / "backups" / batch_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.exists():
            continue
        destination = backup_dir / path.name
        if not destination.exists():
            shutil.copy2(path, destination)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)
