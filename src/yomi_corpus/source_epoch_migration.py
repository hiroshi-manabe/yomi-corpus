from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import struct
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from yomi_corpus.models import empty_analysis
from yomi_corpus.recovery_documents import source_record_id
from yomi_corpus.splitter import split_text_into_units


MIGRATION_SCHEMA_VERSION = 1
DEFAULT_EPOCH = "home_tag_v1_corrected_20260902"
DEFAULT_DATASET_NAME = "ja_cc_level2_corrected_v1"
REPROCESS_SOURCE_IDS = frozenset({"506147bb-4436-47ec-8558-8bf10d8dc802"})
FINAL_FILENAMES = (
    "units.yomi.final.jsonl",
    "units.yomi.skipped.jsonl",
    "units.yomi.excluded.jsonl",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_source_prefix(path: Path, count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for source_line_no, line in enumerate(handle, start=1):
            if source_line_no > count:
                break
            payload = json.loads(line)
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"Blank source document at line {source_line_no} in {path}")
            rows.append(
                {
                    "source_record_id": source_record_id(payload),
                    "source_line_no": source_line_no,
                    "text": text,
                    "source_file": str(payload.get("source_file") or ""),
                }
            )
    if len(rows) != count:
        raise ValueError(f"Expected {count} source records in {path}, found {len(rows)}")
    return rows


def build_prefix_mapping(
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    *,
    old_dataset_name: str,
    new_dataset_name: str,
) -> dict[str, Any]:
    old_by_id = {str(row["source_record_id"]): row for row in old_rows}
    new_by_id = {str(row["source_record_id"]): row for row in new_rows}
    if len(old_by_id) != len(old_rows) or len(new_by_id) != len(new_rows):
        raise ValueError("Duplicate stable source identity in migration prefix")

    shared = set(old_by_id) & set(new_by_id)
    old_shared_order = [str(row["source_record_id"]) for row in old_rows if row["source_record_id"] in shared]
    new_shared_order = [str(row["source_record_id"]) for row in new_rows if row["source_record_id"] in shared]
    if old_shared_order != new_shared_order:
        raise ValueError("Shared source identities changed relative order")

    mapping: list[dict[str, Any]] = []
    for row in new_rows:
        stable_id = str(row["source_record_id"])
        old = old_by_id.get(stable_id)
        new_line = int(row["source_line_no"])
        mapping.append(
            {
                "disposition": "carried" if old is not None else "incoming",
                "source_record_id": stable_id,
                "old_source_line_no": int(old["source_line_no"]) if old else None,
                "old_doc_id": (
                    f"{old_dataset_name}:{int(old['source_line_no']):010d}" if old else None
                ),
                "old_track_doc_seq": int(old["source_line_no"]) if old else None,
                "new_source_line_no": new_line,
                "new_doc_id": f"{new_dataset_name}:{new_line:010d}",
                "new_track_doc_seq": new_line,
            }
        )
    removed = [
        {
            "disposition": "removed",
            "source_record_id": str(row["source_record_id"]),
            "old_source_line_no": int(row["source_line_no"]),
            "old_doc_id": f"{old_dataset_name}:{int(row['source_line_no']):010d}",
            "old_track_doc_seq": int(row["source_line_no"]),
        }
        for row in old_rows
        if row["source_record_id"] not in new_by_id
    ]
    return {
        "mapping": mapping,
        "removed": removed,
        "counts": {
            "old": len(old_rows),
            "new": len(new_rows),
            "carried": len(shared),
            "removed": len(removed),
            "incoming": len(new_rows) - len(shared),
        },
    }


def rewrite_identity(
    value: Any,
    *,
    old_doc_id: str,
    new_doc_id: str,
    new_track_doc_seq: int,
    new_source_line_no: int,
    new_dataset_name: str,
    new_source_path: Path,
) -> Any:
    if isinstance(value, list):
        return [
            rewrite_identity(
                item,
                old_doc_id=old_doc_id,
                new_doc_id=new_doc_id,
                new_track_doc_seq=new_track_doc_seq,
                new_source_line_no=new_source_line_no,
                new_dataset_name=new_dataset_name,
                new_source_path=new_source_path,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        if isinstance(value, str) and value.startswith(old_doc_id):
            return new_doc_id + value[len(old_doc_id) :]
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "doc_id" and item == old_doc_id:
            result[key] = new_doc_id
        elif key == "track_doc_seq":
            result[key] = new_track_doc_seq
        elif key == "source_line_no":
            result[key] = new_source_line_no
        elif key == "dataset_name":
            result[key] = new_dataset_name
        elif key == "dataset_source_path":
            result[key] = str(new_source_path)
        else:
            result[key] = rewrite_identity(
                item,
                old_doc_id=old_doc_id,
                new_doc_id=new_doc_id,
                new_track_doc_seq=new_track_doc_seq,
                new_source_line_no=new_source_line_no,
                new_dataset_name=new_dataset_name,
                new_source_path=new_source_path,
            )
    return result


def replace_string_prefix(value: Any, old_prefix: str, new_prefix: str) -> Any:
    if isinstance(value, list):
        return [replace_string_prefix(item, old_prefix, new_prefix) for item in value]
    if isinstance(value, dict):
        return {
            key: replace_string_prefix(item, old_prefix, new_prefix)
            for key, item in value.items()
        }
    if isinstance(value, str) and value.startswith(old_prefix):
        return new_prefix + value[len(old_prefix) :]
    return value


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _jsonl_write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _batch_number(batch_name: str) -> int:
    match = re.fullmatch(r"dev_batch_(\d+)", batch_name)
    return int(match.group(1)) if match else -1


def build_plan(
    *,
    root: Path,
    old_source: Path,
    new_source: Path,
    build_manifest: Path,
    output_dir: Path,
    old_prefix_count: int = 1670,
    new_prefix_count: int = 1681,
    epoch: str = DEFAULT_EPOCH,
    new_dataset_name: str = DEFAULT_DATASET_NAME,
) -> dict[str, Any]:
    build = _load_json(build_manifest)
    if build.get("status") != "complete":
        raise ValueError("Corrected source build is not complete")
    kept = build.get("artifacts", {}).get("kept", {})
    if Path(str(kept.get("path") or "")).resolve() != new_source.resolve():
        raise ValueError("Build manifest kept artifact does not match --new-source")
    if kept.get("sha256") != _sha256(new_source):
        raise ValueError("Corrected source checksum does not match build manifest")

    old_rows = read_source_prefix(old_source, old_prefix_count)
    new_rows = read_source_prefix(new_source, new_prefix_count)
    old_dataset_name = "ja_cc_level2"
    result = build_prefix_mapping(
        old_rows,
        new_rows,
        old_dataset_name=old_dataset_name,
        new_dataset_name=new_dataset_name,
    )
    expected = {"old": 1670, "new": 1681, "carried": 1659, "removed": 11, "incoming": 22}
    if result["counts"] != expected:
        raise ValueError(f"Prefix mapping differs from reviewed expectation: {result['counts']}")
    for row in result["mapping"]:
        row["canonical_action"] = (
            "reprocess"
            if row["source_record_id"] in REPROCESS_SOURCE_IDS
            else row["disposition"]
        )

    ledger_path = root / "data/pipeline/document_ledger/dev.json"
    ledger = _load_json(ledger_path)
    ledger_by_seq = {
        int(row["track_doc_seq"]): row
        for row in ledger.get("documents", [])
        if int(row.get("track_doc_seq") or 0) <= old_prefix_count
    }
    if set(ledger_by_seq) != set(range(1, old_prefix_count + 1)):
        raise ValueError("Dev ledger does not contain the complete old prefix")
    for row in result["mapping"]:
        old_seq = row.get("old_track_doc_seq")
        if old_seq is None:
            continue
        ledger_row = ledger_by_seq[int(old_seq)]
        if str(ledger_row.get("doc_id")) != row["old_doc_id"]:
            raise ValueError(f"Ledger identity mismatch at old slot {old_seq}")

    plan = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": epoch,
        "status": "planned",
        "created_at": now_iso(),
        "old_dataset_name": old_dataset_name,
        "new_dataset_name": new_dataset_name,
        "old_source": str(old_source.resolve()),
        "new_source": str(new_source.resolve()),
        "old_source_sha256": _sha256(old_source),
        "new_source_sha256": kept["sha256"],
        "build_manifest": str(build_manifest.resolve()),
        "build_manifest_sha256": _sha256(build_manifest),
        "old_prefix_count": old_prefix_count,
        "new_prefix_count": new_prefix_count,
        "counts": result["counts"],
        "action_counts": {
            "carried": sum(row["canonical_action"] == "carried" for row in result["mapping"]),
            "reprocess": sum(row["canonical_action"] == "reprocess" for row in result["mapping"]),
            "incoming": sum(row["canonical_action"] == "incoming" for row in result["mapping"]),
            "removed": len(result["removed"]),
        },
        "reprocess_source_record_ids": sorted(REPROCESS_SOURCE_IDS),
        "mapping": result["mapping"],
        "removed": result["removed"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(output_dir / "plan.json", plan)
    _jsonl_write(output_dir / "prefix_mapping.jsonl", result["mapping"])
    _jsonl_write(output_dir / "removed.jsonl", result["removed"])
    _jsonl_write(
        output_dir / "incoming.jsonl",
        (row for row in result["mapping"] if row["disposition"] == "incoming"),
    )
    _jsonl_write(
        output_dir / "reprocess.jsonl",
        (row for row in result["mapping"] if row["canonical_action"] == "reprocess"),
    )
    return plan


def _source_document_count_from_filter_log(build_manifest: dict[str, Any]) -> int:
    stages = build_manifest.get("stages", [])
    filter_stage = next((row for row in stages if row.get("name") == "filter"), None)
    if not isinstance(filter_stage, dict):
        raise ValueError("Build manifest has no filter stage")
    log_path = Path(str(filter_stage.get("log") or ""))
    text = log_path.read_text(encoding="utf-8")
    match = re.search(r"^kept_docs=(\d+)$", text, re.MULTILINE)
    if match is None:
        raise ValueError("Filter log does not record kept_docs")
    return int(match.group(1))


def _find_finalized_sources(root: Path) -> dict[str, list[tuple[str, Path]]]:
    sources: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for state_path in sorted((root / "data/pipeline/batches").glob("dev_batch_*.json")):
        state = _load_json(state_path)
        if state.get("current_stage") != "yomi_finalized":
            continue
        batch_name = str(state.get("batch_name") or state_path.stem)
        batch_dir = root / "data/units" / batch_name
        for filename in FINAL_FILENAMES:
            path = batch_dir / filename
            if path.exists():
                sources[filename].append((batch_name, path))
    return sources


def _finalized_state(
    *,
    batch_name: str,
    dataset_name: str,
    source_path: Path,
    doc_count: int,
    unit_count: int,
    policies: dict[str, Any],
) -> dict[str, Any]:
    batch_dir = Path("data/units") / batch_name
    return {
        "batch_name": batch_name,
        "track_name": "dev",
        "batch_kind": "dev",
        "pipeline_profile": "dev",
        "dataset_name": dataset_name,
        "dataset_config_path": "config/datasets/ja_cc_level2.toml",
        "dataset_source_path": str(source_path),
        "target_documents": doc_count,
        "docs_written": doc_count,
        "units_written": unit_count,
        "current_stage": "yomi_finalized",
        "yomi_policy": policies["yomi_policy"],
        "llm_policy": policies["llm_policy"],
        "llm_execution_policy": policies["llm_execution_policy"],
        "blocking_reason": None,
        "skipped_review_gates": [],
        "artifacts": {"units_yomi_final_jsonl": str((batch_dir / FINAL_FILENAMES[0]).resolve())},
        "updated_at": now_iso(),
        "source_sequence_epoch": DEFAULT_EPOCH,
    }


def _document_state(batch_name: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "batch_name": batch_name,
        "track_name": "dev",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "documents": docs,
        "summary": {
            "document_count": len(docs),
            "state_counts": {"complete": len(docs)},
            "queue_counts": {"resolved": len(docs)},
            "pool_counts": {"resolved": len(docs)},
        },
    }


def stage_migration(*, root: Path, plan_dir: Path) -> dict[str, Any]:
    plan = _load_json(plan_dir / "plan.json")
    if plan.get("status") != "planned":
        raise ValueError("Migration plan is not in planned state")
    new_source = Path(str(plan["new_source"]))
    build_manifest = _load_json(Path(str(plan["build_manifest"])))
    stage_root = plan_dir / "staging"
    if stage_root.exists():
        raise ValueError(f"Staging directory already exists: {stage_root}")
    units_root = stage_root / "data/units"
    batches_root = stage_root / "data/pipeline/batches"
    states_root = stage_root / "data/pipeline/document_states"
    units_root.mkdir(parents=True)
    batches_root.mkdir(parents=True)
    states_root.mkdir(parents=True)

    current_state = _load_json(root / "data/pipeline/batches/dev_batch_0192.json")
    policies = {
        key: current_state[key]
        for key in ("yomi_policy", "llm_policy", "llm_execution_policy")
    }
    mapping_by_old = {
        str(row["old_doc_id"]): row
        for row in plan["mapping"]
        if row["canonical_action"] == "carried"
    }
    new_source_rows = read_source_prefix(new_source, int(plan["new_prefix_count"]))
    new_text_by_line = {int(row["source_line_no"]): str(row["text"]) for row in new_source_rows}
    new_source_file_by_line = {
        int(row["source_line_no"]): str(row["source_file"]) for row in new_source_rows
    }

    carried_batch_for_seq = {
        seq: f"dev_batch_{1001 + (seq - 1) // 100:04d}"
        for seq in range(1, int(plan["new_prefix_count"]) + 1)
    }
    incoming_rows = [
        row for row in plan["mapping"] if row["canonical_action"] in {"incoming", "reprocess"}
    ]
    incoming_batch_names = ["dev_batch_1018", "dev_batch_1019", "dev_batch_1020"]
    for offset, row in enumerate(incoming_rows):
        row["incoming_batch_name"] = incoming_batch_names[offset // 10]

    counts_by_batch: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"units": 0, "docs": set(), "skipped": defaultdict(int), "excluded": defaultdict(int)}
    )
    text_by_doc: dict[str, list[tuple[int, str]]] = defaultdict(list)
    seen_units: set[str] = set()
    finalized_sources = _find_finalized_sources(root)
    with ExitStack() as stack:
        outputs: dict[tuple[str, str], Any] = {}
        for filename, sources in finalized_sources.items():
            for _old_batch, source_path in sources:
                with source_path.open(encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        old_doc_id = str(row.get("doc_id") or "")
                        target = mapping_by_old.get(old_doc_id)
                        if target is None:
                            continue
                        new_seq = int(target["new_track_doc_seq"])
                        new_doc_id = str(target["new_doc_id"])
                        old_unit_id = str(row.get("unit_id") or "")
                        if old_unit_id in seen_units:
                            raise ValueError(f"Duplicate finalized unit ID: {old_unit_id}")
                        seen_units.add(old_unit_id)
                        rewritten = rewrite_identity(
                            row,
                            old_doc_id=old_doc_id,
                            new_doc_id=new_doc_id,
                            new_track_doc_seq=new_seq,
                            new_source_line_no=new_seq,
                            new_dataset_name=str(plan["new_dataset_name"]),
                            new_source_path=new_source,
                        )
                        new_unit_id = f"{new_doc_id}:u{int(row.get('unit_seq') or 0):04d}"
                        rewritten = replace_string_prefix(
                            rewritten, old_unit_id, new_unit_id
                        )
                        rewritten["unit_id"] = new_unit_id
                        rewritten["source_sequence_epoch"] = str(plan["migration_id"])
                        batch_name = carried_batch_for_seq[new_seq]
                        key = (batch_name, filename)
                        if key not in outputs:
                            path = units_root / batch_name / filename
                            path.parent.mkdir(parents=True, exist_ok=True)
                            outputs[key] = stack.enter_context(path.open("w", encoding="utf-8"))
                        outputs[key].write(json.dumps(rewritten, ensure_ascii=False) + "\n")
                        stats = counts_by_batch[batch_name]
                        stats["units"] += 1
                        stats["docs"].add(new_doc_id)
                        if filename.endswith("skipped.jsonl"):
                            stats["skipped"][new_doc_id] += 1
                        if filename.endswith("excluded.jsonl"):
                            stats["excluded"][new_doc_id] += 1
                        text_by_doc[new_doc_id].append(
                            (int(rewritten.get("unit_seq") or 0), str(rewritten.get("text") or ""))
                        )

    carried = [row for row in plan["mapping"] if row["canonical_action"] == "carried"]
    missing = sorted(str(row["new_doc_id"]) for row in carried if row["new_doc_id"] not in text_by_doc)
    if missing:
        raise ValueError(f"Carried documents lack finalized units: {missing[:10]}")
    mismatches: list[str] = []
    for row in carried:
        doc_id = str(row["new_doc_id"])
        seq = int(row["new_track_doc_seq"])
        parts = sorted(text_by_doc[doc_id])
        joined = "".join(text for _, text in parts)
        if joined != new_text_by_line[seq]:
            # Excluded units intentionally erase source text from the canonical corpus.
            batch_name = carried_batch_for_seq[seq]
            if not counts_by_batch[batch_name]["excluded"].get(doc_id):
                mismatches.append(doc_id)
    if mismatches:
        raise ValueError(f"Carried finalized text differs from corrected source: {mismatches[:10]}")

    ledger_rows: list[dict[str, Any]] = []
    docs_by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan["mapping"]:
        seq = int(row["new_track_doc_seq"])
        doc_id = str(row["new_doc_id"])
        batch_name = (
            carried_batch_for_seq[seq]
            if row["canonical_action"] == "carried"
            else str(row["incoming_batch_name"])
        )
        ledger_rows.append(
            {
                "doc_id": doc_id,
                "track_doc_seq": seq,
                "dataset_name": plan["new_dataset_name"],
                "dataset_source_path": str(new_source),
                "source_line_no": seq,
                "source_record_id": row["source_record_id"],
                "source_sequence_epoch": plan["migration_id"],
                "first_batch_name": batch_name,
                "created_at": now_iso(),
            }
        )
        if row["canonical_action"] == "carried":
            stats = counts_by_batch[batch_name]
            docs_by_batch[batch_name].append(
                {
                    "doc_id": doc_id,
                    "doc_seq": len(docs_by_batch[batch_name]) + 1,
                    "track_doc_seq": seq,
                    "state": "complete",
                    "unit_count": sum(1 for unit_seq, _ in text_by_doc[doc_id]),
                    "reviewed_unit_count": sum(1 for unit_seq, _ in text_by_doc[doc_id]),
                    "skipped_unit_count": int(stats["skipped"].get(doc_id, 0)),
                    "excluded_unit_count": int(stats["excluded"].get(doc_id, 0)),
                    "strong_repair_item_count": 0,
                    "source_sequence_epoch": plan["migration_id"],
                    "updated_at": now_iso(),
                }
            )

    for batch_name, docs in sorted(docs_by_batch.items()):
        stats = counts_by_batch[batch_name]
        for filename in FINAL_FILENAMES:
            path = units_root / batch_name / filename
            if not path.exists():
                _jsonl_write(path, [])
        manifest = {
            "batch_name": batch_name,
            "track_name": "dev",
            "batch_kind": "dev",
            "pipeline_profile": "dev",
            "dataset_name": plan["new_dataset_name"],
            "dataset_config_path": "config/datasets/ja_cc_level2.toml",
            "dataset_source_path": str(new_source),
            "target_documents": len(docs),
            "docs_written": len(docs),
            "units_written": int(stats["units"]),
            "unit_schema_version": 1,
            "source_sequence_epoch": plan["migration_id"],
            **policies,
        }
        _json_dump(units_root / batch_name / "manifest.json", manifest)
        state = _finalized_state(
            batch_name=batch_name,
            dataset_name=str(plan["new_dataset_name"]),
            source_path=new_source,
            doc_count=len(docs),
            unit_count=int(stats["units"]),
            policies=policies,
        )
        state["artifacts"]["units_yomi_final_jsonl"] = str(
            (root / "data/units" / batch_name / FINAL_FILENAMES[0]).resolve()
        )
        _json_dump(batches_root / f"{batch_name}.json", state)
        _json_dump(states_root / f"{batch_name}.json", _document_state(batch_name, docs))

    incoming_by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in incoming_rows:
        incoming_by_batch[str(row["incoming_batch_name"])].append(row)
    for batch_name, rows in sorted(incoming_by_batch.items()):
        units: list[dict[str, Any]] = []
        for doc_seq, row in enumerate(rows, start=1):
            seq = int(row["new_track_doc_seq"])
            doc_id = str(row["new_doc_id"])
            text = new_text_by_line[seq]
            for unit_seq, span in enumerate(split_text_into_units(text), start=1):
                units.append(
                    {
                        "doc_id": doc_id,
                        "unit_id": f"{doc_id}:u{unit_seq:04d}",
                        "unit_seq": unit_seq,
                        "track_doc_seq": seq,
                        "char_start": span.start,
                        "char_end": span.end,
                        "text": span.text,
                        "source_file": new_source_file_by_line[seq],
                        "source_line_no": seq,
                        "source_sequence_epoch": plan["migration_id"],
                        "analysis": asdict(empty_analysis()),
                    }
                )
        _jsonl_write(units_root / batch_name / "units.jsonl", units)
        assignments = [
            {"processing_slot": int(row["new_track_doc_seq"]), "source_line_no": int(row["new_source_line_no"])}
            for row in rows
        ]
        manifest = {
            "batch_name": batch_name,
            "track_name": "dev",
            "batch_kind": "dev",
            "pipeline_profile": "dev",
            "dataset_name": plan["new_dataset_name"],
            "dataset_config_path": "config/datasets/ja_cc_level2.toml",
            "dataset_source_path": str(new_source),
            "target_documents": len(rows),
            "docs_written": len(rows),
            "units_written": len(units),
            "source_start_line_no": min(int(row["new_source_line_no"]) for row in rows),
            "source_end_line_no": max(int(row["new_source_line_no"]) for row in rows),
            "processing_order_generation": 1,
            "processing_slot_start": min(int(row["new_track_doc_seq"]) for row in rows),
            "processing_slot_end": max(int(row["new_track_doc_seq"]) for row in rows),
            "processing_order_assignments": assignments,
            "unit_schema_version": 1,
            "mechanical_analysis_initialized": True,
            "source_sequence_epoch": plan["migration_id"],
            **policies,
        }
        _json_dump(units_root / batch_name / "manifest.json", manifest)
        state = {
            **_finalized_state(
                batch_name=batch_name,
                dataset_name=str(plan["new_dataset_name"]),
                source_path=new_source,
                doc_count=len(rows),
                unit_count=len(units),
                policies=policies,
            ),
            "current_stage": "prepared",
            "artifacts": {"units_jsonl": str((root / "data/units" / batch_name / "units.jsonl").resolve()), "manifest": str((root / "data/units" / batch_name / "manifest.json").resolve())},
        }
        _json_dump(batches_root / f"{batch_name}.json", state)

    ledger = {
        "schema_version": 1,
        "track_name": "dev",
        "source_sequence_epoch": plan["migration_id"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "documents": ledger_rows,
    }
    _json_dump(stage_root / "data/pipeline/document_ledger/dev.json", ledger)

    source_count = _source_document_count_from_filter_log(build_manifest)
    order_dir = stage_root / "data/pipeline/processing_order"
    order_dir.mkdir(parents=True)
    with (order_dir / "dev.u32").open("wb") as output:
        for start in range(1, source_count + 1, 65536):
            end = min(source_count + 1, start + 65536)
            output.write(struct.pack(f"<{end - start}I", *range(start, end)))
    stat = new_source.stat()
    order_manifest = {
        "schema_version": 1,
        "track_name": "dev",
        "dataset_name": plan["new_dataset_name"],
        "source_path": str(new_source),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "source_content_sha256": plan["new_source_sha256"],
        "source_hash_kind": "compressed-file-sha256",
        "source_sequence_epoch": plan["migration_id"],
        "document_count": source_count,
        "cursor": int(plan["new_prefix_count"]) + 1,
        "order_generation": 1,
        "entry_format": "uint32-le",
        "reservation": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    _json_dump(order_dir / "dev.json", order_manifest)
    _jsonl_write(
        order_dir / "dev.events.jsonl",
        [{"event": "source_sequence_epoch_created", "at": now_iso(), "source_sequence_epoch": plan["migration_id"], "cursor": order_manifest["cursor"], "document_count": source_count}],
    )
    _json_dump(
        stage_root / "data/pipeline/tracks/dev.json",
        {"track_name": "dev", "current_batch_name": incoming_batch_names[0], "updated_at": now_iso(), "decoder_model_dir": current_state.get("decoder_model_dir"), "source_sequence_epoch": plan["migration_id"]},
    )
    stage_summary = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": plan["migration_id"],
        "status": "staged",
        "staged_at": now_iso(),
        "counts": plan["counts"],
        "action_counts": plan["action_counts"],
        "carried_batches": len(docs_by_batch),
        "incoming_batches": sorted(incoming_by_batch),
        "source_document_count": source_count,
        "next_track_doc_seq": int(plan["new_prefix_count"]) + 1,
    }
    _json_dump(plan_dir / "stage_summary.json", stage_summary)
    return stage_summary


def validate_staged_migration(*, root: Path, plan_dir: Path) -> dict[str, Any]:
    plan = _load_json(plan_dir / "plan.json")
    stage_summary = _load_json(plan_dir / "stage_summary.json")
    stage_root = plan_dir / "staging"
    ledger = _load_json(stage_root / "data/pipeline/document_ledger/dev.json")
    documents = list(ledger.get("documents", []))
    expected_count = int(plan["new_prefix_count"])
    sequences = [int(row.get("track_doc_seq") or 0) for row in documents]
    doc_ids = [str(row.get("doc_id") or "") for row in documents]
    source_ids = [str(row.get("source_record_id") or "") for row in documents]
    if sequences != list(range(1, expected_count + 1)):
        raise ValueError("Staged ledger sequence is not contiguous")
    if len(set(doc_ids)) != expected_count or len(set(source_ids)) != expected_count:
        raise ValueError("Staged ledger document identities are not unique")
    expected_prefix = f"{plan['new_dataset_name']}:"
    if any(not doc_id.startswith(expected_prefix) for doc_id in doc_ids):
        raise ValueError("Staged ledger contains an old-epoch document ID")

    batch_states = {
        path.stem: _load_json(path)
        for path in sorted((stage_root / "data/pipeline/batches").glob("*.json"))
    }
    finalized_docs: set[str] = set()
    review_docs: set[str] = set()
    unit_ids: set[str] = set()
    for batch_name, state in batch_states.items():
        batch_dir = stage_root / "data/units" / batch_name
        if state.get("current_stage") == "yomi_finalized":
            for filename in FINAL_FILENAMES:
                path = batch_dir / filename
                if not path.exists():
                    raise ValueError(f"Missing staged finalized artifact: {path}")
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        doc_id = str(row.get("doc_id") or "")
                        unit_id = str(row.get("unit_id") or "")
                        if not unit_id.startswith(doc_id + ":u"):
                            raise ValueError(f"Noncanonical staged unit identity: {unit_id}")
                        if unit_id in unit_ids:
                            raise ValueError(f"Duplicate staged unit identity: {unit_id}")
                        unit_ids.add(unit_id)
                        finalized_docs.add(doc_id)
        elif state.get("current_stage") == "prepared":
            path = batch_dir / "units.jsonl"
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    doc_id = str(row.get("doc_id") or "")
                    unit_id = str(row.get("unit_id") or "")
                    if not unit_id.startswith(doc_id + ":u"):
                        raise ValueError(f"Noncanonical incoming unit identity: {unit_id}")
                    if unit_id in unit_ids:
                        raise ValueError(f"Duplicate staged unit identity: {unit_id}")
                    unit_ids.add(unit_id)
                    review_docs.add(doc_id)
        else:
            raise ValueError(f"Unexpected staged batch state for {batch_name}")

    if finalized_docs & review_docs:
        raise ValueError("A staged document is both finalized and pending review")
    if finalized_docs | review_docs != set(doc_ids):
        raise ValueError("Staged batch coverage does not match the document ledger")
    if len(finalized_docs) != int(plan["action_counts"]["carried"]):
        raise ValueError("Staged carried-document count is incorrect")
    expected_review = int(plan["action_counts"]["incoming"]) + int(
        plan["action_counts"]["reprocess"]
    )
    if len(review_docs) != expected_review:
        raise ValueError("Staged review-required document count is incorrect")

    order_manifest = _load_json(stage_root / "data/pipeline/processing_order/dev.json")
    order_path = stage_root / "data/pipeline/processing_order/dev.u32"
    if order_path.stat().st_size != int(order_manifest["document_count"]) * 4:
        raise ValueError("Staged processing-order size is incorrect")
    if int(order_manifest["cursor"]) != expected_count + 1:
        raise ValueError("Staged processing-order cursor is incorrect")
    with order_path.open("rb") as handle:
        first = struct.unpack("<I", handle.read(4))[0]
        handle.seek(-4, os.SEEK_END)
        last = struct.unpack("<I", handle.read(4))[0]
    if first != 1 or last != int(order_manifest["document_count"]):
        raise ValueError("Staged processing order is not the expected identity order")

    result = {
        **stage_summary,
        "status": "validated",
        "validated_at": now_iso(),
        "ledger_documents": len(documents),
        "finalized_documents": len(finalized_docs),
        "review_documents": len(review_docs),
        "unit_count": len(unit_ids),
        "batch_count": len(batch_states),
        "processing_order_bytes": order_path.stat().st_size,
    }
    _json_dump(plan_dir / "validation_summary.json", result)
    return result


def apply_staged_migration(*, root: Path, plan_dir: Path) -> dict[str, Any]:
    plan = _load_json(plan_dir / "plan.json")
    stage_summary = _load_json(plan_dir / "stage_summary.json")
    validation = _load_json(plan_dir / "validation_summary.json")
    stage_root = plan_dir / "staging"
    if (
        stage_summary.get("status") != "staged"
        or validation.get("status") != "validated"
        or not stage_root.exists()
    ):
        raise ValueError("Migration is not staged and validated")
    old_order = _load_json(root / "data/pipeline/processing_order/dev.json")
    if old_order.get("reservation") is not None:
        raise ValueError("Processing order has an active reservation")

    archive_root = plan_dir / "legacy_epoch"
    if archive_root.exists():
        raise ValueError(f"Legacy archive already exists: {archive_root}")
    archive_root.mkdir(parents=True)
    moves = [
        (root / "data/units", archive_root / "data/units"),
        (root / "data/pipeline/batches", archive_root / "data/pipeline/batches"),
        (root / "data/pipeline/document_states", archive_root / "data/pipeline/document_states"),
        (root / "data/pipeline/document_ledger/dev.json", archive_root / "data/pipeline/document_ledger/dev.json"),
        (root / "data/pipeline/processing_order/dev.json", archive_root / "data/pipeline/processing_order/dev.json"),
        (root / "data/pipeline/processing_order/dev.u32", archive_root / "data/pipeline/processing_order/dev.u32"),
        (root / "data/pipeline/processing_order/dev.events.jsonl", archive_root / "data/pipeline/processing_order/dev.events.jsonl"),
        (root / "data/pipeline/tracks/dev.json", archive_root / "data/pipeline/tracks/dev.json"),
        (root / "config/datasets/ja_cc_level2.toml", archive_root / "config/datasets/ja_cc_level2.toml"),
        (root / "data/review_packs", archive_root / "data/review_packs"),
        (root / "data/review_submissions", archive_root / "data/review_submissions"),
        (root / "data/recovery/home_tag_v1", archive_root / "data/recovery/home_tag_v1"),
        (root / "docs/review", archive_root / "docs/review"),
    ]
    completed_moves: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for source, destination in moves:
            if not source.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            completed_moves.append((source, destination))
        for relative in (
            Path("data/units"),
            Path("data/pipeline/batches"),
            Path("data/pipeline/document_states"),
            Path("data/pipeline/document_ledger"),
            Path("data/pipeline/processing_order"),
            Path("data/pipeline/tracks"),
        ):
            source = stage_root / relative
            destination = root / relative
            if destination.exists():
                for child in source.iterdir():
                    os.replace(child, destination / child.name)
                source.rmdir()
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
            installed.append(destination)
        (root / "data/review_packs").mkdir(parents=True, exist_ok=True)
        (root / "data/review_submissions").mkdir(parents=True, exist_ok=True)
        (root / "docs/review").mkdir(parents=True, exist_ok=True)
        config_path = root / "config/datasets/ja_cc_level2.toml"
        config_path.write_text(
            f'name = "{plan["new_dataset_name"]}"\nsource_path = "{plan["new_source"]}"\nsource_sequence_epoch = "{plan["migration_id"]}"\n',
            encoding="utf-8",
        )
    except BaseException:
        for source, destination in reversed(completed_moves):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
        raise

    applied = {
        **stage_summary,
        "status": "applied",
        "applied_at": now_iso(),
        "legacy_archive": str(archive_root),
        "active_source": plan["new_source"],
    }
    _json_dump(plan_dir / "application_summary.json", applied)
    plan["status"] = "applied"
    plan["applied_at"] = applied["applied_at"]
    _json_dump(plan_dir / "plan.json", plan)
    return applied
