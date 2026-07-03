from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.llm_readings import (
    is_standalone_laughter_w,
    is_valid_yomi_reading,
    normalize_hiragana_reading,
)
from yomi_corpus.yomi.furigana import FuriganaConverter, has_han, kata_to_hira


REVIEW_STAGE = "yomi_final_review"
STRONG_REPAIR_REVIEW_STAGE = "yomi_strong_repair_review"
QUEUE_ID_FINAL_REVIEW = "final_review"
QUEUE_ID_STRONG_REPAIR = "strong_repair"
SCHEMA_VERSION = 1
APPLY_RULE = "yomi_final_review_apply_v1"
STRONG_REPAIR_REVIEW_RULE = "yomi_strong_repair_review_v1"
SURFACE_READING_STATS_PATH = Path("data/generated/yomi_surface_reading_stats.tsv")
ANNOTATED_FORMS_PATH = Path("data/external/sudachi_annotated_forms/sudachi_20251022.tsv")
SUPPLEMENTAL_FURIGANA_PATH = Path("data/lexicon/supplemental_furigana.tsv")
READING_HINT_MIN_COUNT = 2
READING_HINT_MIN_SHARE = 0.995
MAX_READING_HINT_SURFACE_LENGTH = 12
DOCUMENT_STATE_FINAL_PENDING = "final_pending"
DOCUMENT_STATE_FINAL_IN_REVIEW = "final_in_review"
DOCUMENT_STATE_STRONG_PENDING = "strong_pending"
DOCUMENT_STATE_STRONG_IN_REVIEW = "strong_in_review"
DOCUMENT_STATE_COMPLETE = "complete"
FINAL_REVIEW_SELECTABLE_STATES = frozenset(
    {DOCUMENT_STATE_FINAL_PENDING, DOCUMENT_STATE_FINAL_IN_REVIEW}
)
STRONG_REPAIR_SELECTABLE_STATES = frozenset(
    {DOCUMENT_STATE_STRONG_PENDING, DOCUMENT_STATE_STRONG_IN_REVIEW}
)


@dataclass(frozen=True)
class YomiFinalReviewPackSummary:
    pack_id: str
    review_stage: str
    item_count: int
    unresolved_item_count: int
    unresolved_target_count: int
    provisional_skip_item_count: int
    output_json: str
    latest_json: str | None


@dataclass(frozen=True)
class YomiStrongRepairReviewPackSummary:
    pack_id: str
    review_stage: str
    item_count: int
    output_json: str
    latest_json: str | None


def build_yomi_final_review_pack_file(
    *,
    units_jsonl: Path,
    output_json: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    latest_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> YomiFinalReviewPackSummary:
    pack = build_yomi_final_review_pack(
        units_jsonl=units_jsonl,
        pack_id=pack_id,
        track_name=track_name,
        batch_name=batch_name,
        document_state_json=document_state_json,
        created_at_epoch=created_at_epoch,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if latest_json is not None:
        latest_json.parent.mkdir(parents=True, exist_ok=True)
        latest_json.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return YomiFinalReviewPackSummary(
        pack_id=pack_id,
        review_stage=REVIEW_STAGE,
        item_count=int(pack["item_count"]),
        unresolved_item_count=int(pack["summary"]["unresolved_item_count"]),
        unresolved_target_count=int(pack["summary"]["unresolved_target_count"]),
        provisional_skip_item_count=int(pack["summary"]["provisional_skip_item_count"]),
        output_json=str(output_json),
        latest_json=str(latest_json) if latest_json is not None else None,
    )


def build_yomi_strong_repair_review_pack_file(
    *,
    queue_jsonl: Path,
    results_jsonl: Path,
    units_jsonl: Path,
    output_json: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    latest_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> YomiStrongRepairReviewPackSummary:
    pack = build_yomi_strong_repair_review_pack(
        queue_jsonl=queue_jsonl,
        results_jsonl=results_jsonl,
        units_jsonl=units_jsonl,
        pack_id=pack_id,
        track_name=track_name,
        batch_name=batch_name,
        document_state_json=document_state_json,
        created_at_epoch=created_at_epoch,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if latest_json is not None:
        latest_json.parent.mkdir(parents=True, exist_ok=True)
        latest_json.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return YomiStrongRepairReviewPackSummary(
        pack_id=pack_id,
        review_stage=STRONG_REPAIR_REVIEW_STAGE,
        item_count=int(pack["item_count"]),
        output_json=str(output_json),
        latest_json=str(latest_json) if latest_json is not None else None,
    )


def build_yomi_final_review_pack(
    *,
    units_jsonl: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    created = created_at_epoch if created_at_epoch is not None else current_epoch()
    units = load_jsonl(units_jsonl)
    documents = build_pack_documents(units)
    doc_seq_by_id = {str(row["doc_id"]): int(row["doc_seq"]) for row in documents}

    for unit in units:
        doc_id = str(unit.get("doc_id") or "")
        item = build_review_item(
            unit,
            seq=len(items) + 1,
            doc_seq=doc_seq_by_id.get(doc_id, len(doc_seq_by_id) + 1),
        )
        items.append(item)

    unresolved_items = [item for item in items if item["unresolved_target_count"] > 0]
    unresolved_targets = sum(int(item["unresolved_target_count"]) for item in items)
    provisional_skip_items = [item for item in items if item["provisional_skip"]]
    documents = with_queue_document_metadata(
        documents,
        items,
        queue_id=QUEUE_ID_FINAL_REVIEW,
        document_state_json=document_state_json,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "review_stage": REVIEW_STAGE,
        "queue_id": QUEUE_ID_FINAL_REVIEW,
        "pack_id": pack_id,
        "track_name": track_name,
        "batch_name": batch_name,
        "created_at_epoch": created,
        "created_at": datetime.fromtimestamp(created, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "item_count": len(items),
        "summary": {
            "document_count": len(documents),
            "selectable_document_count": sum(1 for doc in documents if doc.get("selectable")),
            "document_state_counts": document_state_counts(documents),
            "unresolved_item_count": len(unresolved_items),
            "unresolved_target_count": unresolved_targets,
            "provisional_skip_item_count": len(provisional_skip_items),
        },
        "documents": documents,
        "items": items,
    }


def build_yomi_strong_repair_review_pack(
    *,
    queue_jsonl: Path,
    results_jsonl: Path,
    units_jsonl: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> dict[str, Any]:
    queue_rows = load_jsonl(queue_jsonl)
    result_rows = {str(row.get("item_id") or ""): row for row in load_jsonl(results_jsonl)}
    units_by_id = {str(row.get("unit_id") or ""): row for row in load_jsonl(units_jsonl)}
    documents = build_pack_documents(units_by_id.values())
    doc_seq_by_id = {str(row["doc_id"]): int(row["doc_seq"]) for row in documents}
    created = created_at_epoch if created_at_epoch is not None else current_epoch()
    rows_by_unit: dict[str, list[dict[str, Any]]] = {}
    for queue_row in queue_rows:
        unit_id = str(queue_row.get("unit_id") or "")
        if unit_id:
            rows_by_unit.setdefault(unit_id, []).append(queue_row)

    items = []
    for seq, (unit_id, unit_queue_rows) in enumerate(rows_by_unit.items(), start=1):
        unit = units_by_id.get(unit_id, {})
        doc_id = str(unit.get("doc_id") or "")
        regions = [
            build_strong_repair_review_region(
                queue_row,
                result_rows.get(str(queue_row.get("item_id") or ""), {}),
                unit,
            )
            for queue_row in unit_queue_rows
        ]
        items.append(
            build_strong_repair_review_sentence_item(
                unit_queue_rows,
                regions,
                unit,
                seq=seq,
                doc_seq=doc_seq_by_id.get(doc_id),
            )
        )
    documents = with_queue_document_metadata(
        documents,
        items,
        queue_id=QUEUE_ID_STRONG_REPAIR,
        document_state_json=document_state_json,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "review_stage": STRONG_REPAIR_REVIEW_STAGE,
        "queue_id": QUEUE_ID_STRONG_REPAIR,
        "pack_id": pack_id,
        "track_name": track_name,
        "batch_name": batch_name,
        "created_at_epoch": created,
        "created_at": datetime.fromtimestamp(created, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "item_count": len(items),
        "summary": {
            "document_count": len(documents),
            "selectable_document_count": sum(1 for doc in documents if doc.get("selectable")),
            "document_state_counts": document_state_counts(documents),
            "repaired_item_count": len(items),
        },
        "documents": documents,
        "items": items,
    }


def build_pack_documents(units: Any) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for unit in units:
        if not isinstance(unit, dict):
            continue
        doc_id = str(unit.get("doc_id") or "")
        if not doc_id:
            continue
        if doc_id not in by_id:
            doc = {
                "doc_id": doc_id,
                "doc_seq": len(documents) + 1,
                "unit_count": 0,
                "source_file": unit.get("source_file"),
                "source_line_no": unit.get("source_line_no"),
                "preview": str(unit.get("text") or ""),
            }
            by_id[doc_id] = doc
            documents.append(doc)
        by_id[doc_id]["unit_count"] += 1
    return documents


def with_repair_document_counts(
    documents: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    item_counts = Counter(str(item.get("doc_id") or "") for item in items)
    region_counts = Counter()
    for item in items:
        region_counts[str(item.get("doc_id") or "")] += int(item.get("region_count") or 0)
    return [
        {
            **doc,
            "item_count": int(item_counts.get(str(doc.get("doc_id") or ""), 0)),
            "region_count": int(region_counts.get(str(doc.get("doc_id") or ""), 0)),
        }
        for doc in documents
    ]


def with_queue_document_metadata(
    documents: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    queue_id: str,
    document_state_json: Path | None,
) -> list[dict[str, Any]]:
    item_stats = document_item_stats(items)
    state_rows = load_document_state_rows(document_state_json)
    enriched: list[dict[str, Any]] = []
    for doc in documents:
        doc_id = str(doc.get("doc_id") or "")
        stats = item_stats.get(doc_id, {})
        state_row = state_rows.get(doc_id, {})
        state = str(
            state_row.get("state")
            or default_document_state_for_queue(queue_id, int(stats.get("item_count") or 0))
        )
        selectable = document_is_selectable_for_queue(
            queue_id=queue_id,
            state=state,
            item_count=int(stats.get("item_count") or 0),
        )
        enriched.append(
            {
                **doc,
                "queue_id": queue_id,
                "state": state,
                "selectable": selectable,
                "state_updated_at": str(state_row.get("updated_at") or ""),
                "item_count": int(stats.get("item_count") or 0),
                "region_count": int(stats.get("region_count") or 0),
                "unresolved_count": int(stats.get("unresolved_count") or 0),
                "from_seq": stats.get("from_seq"),
                "to_seq": stats.get("to_seq"),
                "reviewed_unit_count": int(state_row.get("reviewed_unit_count") or 0),
                "skipped_unit_count": int(state_row.get("skipped_unit_count") or 0),
                "strong_repair_item_count": int(
                    state_row.get("strong_repair_item_count") or stats.get("region_count") or 0
                ),
            }
        )
    return enriched


def document_item_stats(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats_by_doc: dict[str, dict[str, Any]] = {}
    for item in items:
        doc_id = str(item.get("doc_id") or "")
        if not doc_id:
            continue
        stats = stats_by_doc.setdefault(
            doc_id,
            {
                "from_seq": int(item.get("seq") or 0),
                "to_seq": int(item.get("seq") or 0),
                "item_count": 0,
                "region_count": 0,
                "unresolved_count": 0,
            },
        )
        seq = int(item.get("seq") or 0)
        if seq:
            stats["from_seq"] = min(int(stats["from_seq"] or seq), seq)
            stats["to_seq"] = max(int(stats["to_seq"] or seq), seq)
        stats["item_count"] += 1
        stats["region_count"] += int(item.get("region_count") or 0)
        stats["unresolved_count"] += int(
            item.get("unresolved_target_count") or item.get("region_count") or 0
        )
    return stats_by_doc


def load_document_state_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("documents")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("doc_id") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("doc_id") or "")
    }


def default_document_state_for_queue(queue_id: str, item_count: int) -> str:
    if queue_id == QUEUE_ID_FINAL_REVIEW:
        return DOCUMENT_STATE_FINAL_PENDING
    if queue_id == QUEUE_ID_STRONG_REPAIR:
        return DOCUMENT_STATE_STRONG_PENDING if item_count else DOCUMENT_STATE_COMPLETE
    return DOCUMENT_STATE_COMPLETE


def document_is_selectable_for_queue(*, queue_id: str, state: str, item_count: int) -> bool:
    if item_count <= 0:
        return False
    if queue_id == QUEUE_ID_FINAL_REVIEW:
        return state in FINAL_REVIEW_SELECTABLE_STATES
    if queue_id == QUEUE_ID_STRONG_REPAIR:
        return state in STRONG_REPAIR_SELECTABLE_STATES
    return False


def document_state_counts(documents: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(doc.get("state") or "") for doc in documents)
    return {state: count for state, count in sorted(counts.items()) if state}


def build_strong_repair_review_sentence_item(
    queue_rows: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    unit: dict[str, Any],
    *,
    seq: int,
    doc_seq: int | None,
) -> dict[str, Any]:
    first_row = queue_rows[0] if queue_rows else {}
    first_region = regions[0] if regions else {}
    rendered_after = str(
        unit.get("analysis", {}).get("mechanical", {}).get("yomi", {}).get("rendered") or ""
    )
    return {
        "item_id": f"{unit.get('unit_id')}::strong_repair",
        "seq": seq,
        "doc_id": str(unit.get("doc_id") or ""),
        "doc_seq": doc_seq,
        "unit_id": str(unit.get("unit_id") or first_row.get("unit_id") or ""),
        "unit_seq": unit.get("unit_seq"),
        "source_file": unit.get("source_file"),
        "source_line_no": unit.get("source_line_no"),
        "text": str(unit.get("text") or first_row.get("text") or ""),
        "rendered_yomi_before": str(first_row.get("rendered_yomi") or ""),
        "rendered_yomi_after": rendered_after,
        "rendered_yomi_after_ruby_tokens": rendered_yomi_ruby_tokens(rendered_after),
        "repair_scope": "sentence_regions",
        "region_count": len(regions),
        "regions": regions,
        # Compatibility aliases for consumers that still expect one region per item.
        "rejected_span": first_region.get("rejected_span", ""),
        "target_escalations": first_region.get("target_escalations", []),
        "rejected_readings": first_region.get("rejected_readings", []),
        "reading_candidates": first_region.get("reading_candidates", {}),
        "reading_hints": first_region.get("reading_hints", {}),
        "llm_parsed": first_region.get("llm_parsed", []),
        "llm_raw_text": first_region.get("llm_raw_text", ""),
        "llm_parse_error": first_region.get("llm_parse_error"),
        "used_web_search": any(bool(region.get("used_web_search")) for region in regions),
        "repair_status": first_region.get("repair_status"),
        "repair_log": first_region.get("repair_log", {}),
    }


def build_strong_repair_review_region(
    queue_row: dict[str, Any],
    result: dict[str, Any],
    unit: dict[str, Any],
) -> dict[str, Any]:
    parsed = result.get("parsed")
    if not isinstance(parsed, list):
        parsed = []
    repairs = (
        unit.get("analysis", {})
        .get("llm", {})
        .get("yomi_strong_repair", {})
        .get("repairs", [])
    )
    repair_log = next(
        (
            row
            for row in repairs
            if isinstance(row, dict) and row.get("item_id") == queue_row.get("item_id")
        ),
        {},
    )
    rejected_readings = []
    for target in queue_row.get("target_escalations", []):
        if not isinstance(target, dict):
            continue
        rejected_readings.extend(
            row for row in target.get("rejected_readings", []) if isinstance(row, dict)
        )
    rejected_span = "".join(
        str(row.get("surface") or "")
        for row in queue_row.get("target_escalations", [])
        if isinstance(row, dict)
    )
    span_reading_candidates = build_strong_repair_reading_candidates(rejected_span)
    rendered_after = str(
        unit.get("analysis", {}).get("mechanical", {}).get("yomi", {}).get("rendered") or ""
    )
    return {
        "region_id": str(queue_row.get("item_id") or ""),
        "item_id": str(queue_row.get("item_id") or ""),
        "unit_id": str(queue_row.get("unit_id") or ""),
        "text": str(queue_row.get("text") or ""),
        "rendered_yomi_before": str(queue_row.get("rendered_yomi") or ""),
        "rendered_yomi_after": rendered_after,
        "repair_scope": str(queue_row.get("repair_scope") or ""),
        "reasons": list(queue_row.get("reasons") or []),
        "target_constraints": [
            row for row in queue_row.get("target_constraints", []) if isinstance(row, dict)
        ],
        "target_escalations": [
            row for row in queue_row.get("target_escalations", []) if isinstance(row, dict)
        ],
        "rejected_span": rejected_span,
        "reading_candidates": span_reading_candidates,
        "reading_hints": {
            surface: readings[0]
            for surface, readings in span_reading_candidates.items()
            if readings
        },
        "rejected_readings": rejected_readings,
        "llm_raw_text": str(result.get("raw_text") or ""),
        "llm_parsed": parsed,
        "llm_parse_error": result.get("parse_error"),
        "used_web_search": any(bool(row.get("used_web_search")) for row in parsed if isinstance(row, dict)),
        "repair_status": repair_log.get("status"),
        "repair_log": repair_log,
    }


def build_review_item(unit: dict[str, Any], *, seq: int, doc_seq: int) -> dict[str, Any]:
    safety = unit.get("analysis", {}).get("safety", {}).get("yomi", {})
    targets = safety.get("targets", [])
    if not isinstance(targets, list):
        targets = []
    review_targets = [build_review_target(target) for target in targets if isinstance(target, dict)]
    unresolved_count = sum(1 for target in review_targets if not target["is_safe"])
    scope = unit.get("analysis", {}).get("llm", {}).get("scope_triage", {})
    alphabetic_scope = unit.get("analysis", {}).get("mechanical", {}).get("alphabetic_scope", {})
    provisional_skip = bool(
        scope.get("status") == "Skip"
        and (
            scope.get("provisional")
            or scope.get("source") == "provisional_alphabetic_skip"
            or alphabetic_scope.get("provisional_skip")
        )
    )
    return {
        "item_id": str(unit.get("unit_id", "")),
        "seq": seq,
        "doc_id": str(unit.get("doc_id") or ""),
        "doc_seq": doc_seq,
        "unit_id": str(unit.get("unit_id", "")),
        "unit_seq": unit.get("unit_seq"),
        "source_file": unit.get("source_file"),
        "source_line_no": unit.get("source_line_no"),
        "text": str(unit.get("text") or ""),
        "ruby_segments": build_ruby_segments(str(unit.get("text") or ""), review_targets),
        "rendered_yomi": str(
            unit.get("analysis", {}).get("mechanical", {}).get("yomi", {}).get("rendered") or ""
        ),
        "scope_status": scope.get("status"),
        "provisional_skip": provisional_skip,
        "skip_default": bool(scope.get("status") == "Skip" or provisional_skip),
        "target_count": len(review_targets),
        "safe_target_count": len(review_targets) - unresolved_count,
        "unresolved_target_count": unresolved_count,
        "all_targets_safe": bool(review_targets) and unresolved_count == 0,
        "targets": review_targets,
        "reading_hints": build_reading_hints(review_targets),
    }


def build_review_target(target: dict[str, Any]) -> dict[str, Any]:
    candidates = reading_candidates(target)
    default_choice_source = default_candidate_source(target, candidates)
    default_candidate = candidate_by_source(candidates, default_choice_source)
    surface = str(target.get("surface") or "")
    candidates = [with_ruby_display_nodes(surface, candidate) for candidate in candidates]
    return {
        "item_id": str(target.get("item_id") or ""),
        "surface": surface,
        "token_surface": str(target.get("token_surface") or ""),
        "target_start": target.get("target_start"),
        "target_end": target.get("target_end"),
        "token_index": target.get("token_index"),
        "chunk_index": target.get("chunk_index"),
        "current_reading": target.get("current_reading"),
        "current_reading_hiragana": target.get("current_reading_hiragana"),
        "is_safe": bool(target.get("is_safe")),
        "review_status": target.get("review_status"),
        "highlight_level": target.get("highlight_level"),
        "accepted_signal_names": list(target.get("accepted_signal_names") or []),
        "status_reason": target.get("status_reason"),
        "default_choice_source": default_choice_source,
        "default_reading": default_candidate.get("reading") if default_candidate else None,
        "candidates": candidates,
        "signals": target.get("signals") if isinstance(target.get("signals"), list) else [],
    }


def with_ruby_display_nodes(surface: str, candidate: dict[str, Any]) -> dict[str, Any]:
    reading = candidate.get("reading")
    if isinstance(reading, str) and reading:
        return {
            **candidate,
            "ruby_nodes": ruby_nodes_for_surface_reading(surface, reading),
        }
    return {
        **candidate,
        "ruby_nodes": [{"type": "text", "text": surface}],
    }


def default_candidate_source(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> str:
    if has_accepted_no_ruby_signal(target):
        return "none"
    if bool(target.get("is_safe")):
        return "current"
    for signal in target.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        if signal.get("name") != "safe_by_llm_match":
            continue
        if signal.get("accepted"):
            continue
        if signal.get("status") != "mismatched":
            continue
        llm_reading = signal.get("llm_reading")
        if not isinstance(llm_reading, str) or not llm_reading:
            continue
        if candidate_by_source(candidates, "llm") is not None:
            return "llm"
    return "current"


def has_accepted_no_ruby_signal(target: dict[str, Any]) -> bool:
    for signal in target.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        if signal.get("accepted") and signal.get("preferred_choice_source") == "none":
            return True
    return False


def candidate_by_source(
    candidates: list[dict[str, Any]],
    source: str,
) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate.get("source") == source:
            return candidate
    return None


def reading_candidates(target: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    accepted_no_ruby = has_accepted_no_ruby_signal(target)

    def add(source: str, label: str, reading: object, *, accepted: bool = False) -> None:
        if not isinstance(reading, str) or not reading:
            return
        normalized = normalize_hiragana_reading(reading)
        if not is_valid_yomi_reading(normalized):
            return
        if any(candidate["reading"] == normalized for candidate in candidates):
            return
        candidates.append(
            {
                "source": source,
                "label": label,
                "reading": normalized,
                "accepted": accepted,
            }
        )

    accepted_names = set(target.get("accepted_signal_names") or [])
    add(
        "current",
        "Current mechanical/hybrid",
        target.get("current_reading_hiragana") or target.get("current_reading"),
        accepted=bool(target.get("is_safe")) and not accepted_no_ruby,
    )
    for signal in target.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        name = str(signal.get("name") or "")
        if name == "safe_by_llm_match":
            add(
                "llm",
                "LLM reading",
                signal.get("llm_reading"),
                accepted="safe_by_llm_match" in accepted_names,
            )
        elif name == "safe_by_corpus_frequency":
            dominant = signal.get("dominant")
            if isinstance(dominant, dict):
                add(
                    "corpus_frequency",
                    "Corpus-frequency dominant",
                    dominant.get("reading"),
                    accepted="safe_by_corpus_frequency" in accepted_names,
                )
        elif name == "safe_by_stable_dictionary" and signal.get("accepted"):
            add(
                "stable_dictionary",
                "Stable dictionary",
                target.get("current_reading_hiragana") or target.get("current_reading"),
                accepted=True,
            )
    candidates.append(
        {
            "source": "none",
            "label": "No ruby",
            "reading": None,
            "accepted": accepted_no_ruby,
        }
    )
    return candidates


def build_reading_hints(targets: list[dict[str, Any]]) -> dict[str, str]:
    hints: dict[str, str] = {}
    for target in targets:
        surface = str(target.get("surface") or "")
        for candidate in target.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            reading = candidate.get("reading")
            if isinstance(reading, str) and reading and surface:
                hints.setdefault(surface, reading)
                break

    stats = load_surface_reading_stats()
    for surface in target_substrings_for_hints(targets):
        if surface in hints:
            continue
        reading = stats.get(surface)
        if reading:
            hints[surface] = reading
    return hints


def target_substrings_for_hints(targets: list[dict[str, Any]]) -> set[str]:
    ordered = sorted(
        [
            target
            for target in targets
            if isinstance(target.get("target_start"), int)
            and isinstance(target.get("target_end"), int)
        ],
        key=lambda target: (int(target["target_start"]), int(target["target_end"])),
    )
    surfaces: set[str] = set()
    for index, target in enumerate(ordered):
        if target.get("is_safe"):
            continue
        end_index = index
        while (
            end_index + 1 < len(ordered)
            and ordered[end_index].get("target_end") == ordered[end_index + 1].get("target_start")
        ):
            end_index += 1
        span = "".join(str(row.get("surface") or "") for row in ordered[index : end_index + 1])
        chars = list(span)
        for start in range(len(chars)):
            for end in range(start + 2, min(len(chars), start + MAX_READING_HINT_SURFACE_LENGTH) + 1):
                surfaces.add("".join(chars[start:end]))
    return surfaces


def build_strong_repair_reading_candidates(rejected_span: str) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    surface_readings = load_annotated_form_surface_readings()
    for surface in substrings_for_reading_candidates(rejected_span):
        readings = surface_readings.get(surface)
        if readings:
            candidates[surface] = list(readings)
    return candidates


def substrings_for_reading_candidates(surface: str) -> set[str]:
    chars = list(surface)
    surfaces: set[str] = set()
    for start in range(len(chars)):
        for end in range(start + 1, min(len(chars), start + MAX_READING_HINT_SURFACE_LENGTH) + 1):
            surfaces.add("".join(chars[start:end]))
    return surfaces


@lru_cache(maxsize=1)
def load_annotated_form_surface_readings() -> dict[str, tuple[str, ...]]:
    if not ANNOTATED_FORMS_PATH.exists():
        return {}
    counts: dict[str, Counter[str]] = {}
    with ANNOTATED_FORMS_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"surface", "reading"}
        if not required.issubset(reader.fieldnames or set()):
            return {}
        for row in reader:
            surface = row["surface"]
            reading = normalize_hiragana_reading(row["reading"])
            if not surface or not reading or not is_valid_yomi_reading(reading):
                continue
            counts.setdefault(surface, Counter())[reading] += 1
    return {
        surface: tuple(
            reading
            for reading, _count in sorted(
                counter.items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        for surface, counter in counts.items()
    }


@lru_cache(maxsize=1)
def load_surface_reading_stats() -> dict[str, str]:
    if not SURFACE_READING_STATS_PATH.exists():
        return {}
    stats: dict[str, str] = {}
    with SURFACE_READING_STATS_PATH.open(encoding="utf-8") as handle:
        header = next(handle, "").rstrip("\n").split("\t")
        columns = {name: index for index, name in enumerate(header)}
        required = {"surface", "reading", "count", "share"}
        if not required.issubset(columns):
            return {}
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            try:
                surface = fields[columns["surface"]]
                reading = fields[columns["reading"]]
                count = int(fields[columns["count"]])
                share = float(fields[columns["share"]])
            except (IndexError, ValueError):
                continue
            normalized = normalize_hiragana_reading(reading)
            if (
                surface
                and normalized
                and is_valid_yomi_reading(normalized)
                and count >= READING_HINT_MIN_COUNT
                and share >= READING_HINT_MIN_SHARE
            ):
                stats[surface] = normalized
    return stats


def build_ruby_segments(text: str, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    cursor = 0
    ordered_targets = sorted(
        [
            target
            for target in targets
            if isinstance(target.get("target_start"), int)
            and isinstance(target.get("target_end"), int)
            and int(target["target_start"]) >= 0
            and int(target["target_end"]) > int(target["target_start"])
        ],
        key=lambda target: (int(target["target_start"]), int(target["target_end"])),
    )
    for target in ordered_targets:
        start = int(target["target_start"])
        end = int(target["target_end"])
        if start < cursor:
            continue
        if cursor < start:
            segments.append({"type": "text", "text": text[cursor:start]})
        segments.append(
            {
                "type": "ruby",
                "text": text[start:end],
                "target_item_id": target["item_id"],
                "reading": ruby_segment_reading(target),
                "is_safe": target.get("is_safe"),
                "highlight_level": target.get("highlight_level"),
            }
        )
        cursor = end
    if cursor < len(text):
        segments.append({"type": "text", "text": text[cursor:]})
    return segments


def ruby_segment_reading(target: dict[str, Any]) -> str | None:
    if target.get("default_choice_source") == "none":
        return None
    return target.get("default_reading") or target.get("current_reading_hiragana")


def rendered_yomi_ruby_tokens(rendered: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for raw_token in str(rendered or "").strip().split():
        surface, reading = split_rendered_yomi_token(raw_token)
        tokens.append(
            {
                "surface": surface,
                "reading": reading,
                "nodes": ruby_nodes_for_surface_reading(surface, reading),
            }
        )
    return tokens


def split_rendered_yomi_token(token: str) -> tuple[str, str]:
    separator = token.rfind("/")
    if separator < 0:
        return token, ""
    return token[:separator], token[separator + 1 :]


def ruby_nodes_for_surface_reading(surface: str, reading: str) -> list[dict[str, str]]:
    if not should_display_ruby(surface, reading):
        return [{"type": "text", "text": surface}]
    reading_hira = kata_to_hira(reading)
    if has_han(surface):
        result = furigana_converter().convert(surface, reading)
        if result.annotated_surface:
            return annotated_furigana_nodes(result.annotated_surface)
        return [{"type": "ruby", "text": surface, "reading": reading_hira}]
    mixed_nodes = mixed_latin_kana_ruby_nodes(surface, reading_hira)
    if mixed_nodes is not None:
        return mixed_nodes
    return [{"type": "ruby", "text": surface, "reading": reading_hira}]


def should_display_ruby(surface: str, reading: str) -> bool:
    if not surface or not reading or surface == reading:
        return False
    return bool(re.search(r"[一-龯々〆ヵヶA-Za-zＡ-Ｚａ-ｚ]", surface))


def mixed_latin_kana_ruby_nodes(surface: str, reading_hira: str) -> list[dict[str, str]] | None:
    if not has_latin(surface) or not has_kana(surface):
        return None
    elements = surface_reading_elements(surface)
    if not any(kind == "latin" for kind, _ in elements):
        return None

    result: list[dict[str, str]] | None = None

    def rec(index: int, reading_index: int, nodes: list[dict[str, str]]) -> None:
        nonlocal result
        if result is not None:
            return
        if index == len(elements):
            if reading_index == len(reading_hira):
                result = [node for node in nodes if node.get("text")]
            return
        kind, text = elements[index]
        if kind == "kana":
            fixed = kana_surface_to_hira(text)
            if reading_hira.startswith(fixed, reading_index):
                nodes.append({"type": "text", "text": text})
                rec(index + 1, reading_index + len(fixed), nodes)
                nodes.pop()
            return
        if kind == "other":
            if reading_hira.startswith(text, reading_index):
                nodes.append({"type": "text", "text": text})
                rec(index + 1, reading_index + len(text), nodes)
                nodes.pop()
            return

        remaining_min = minimum_remaining_surface_reading(elements[index + 1 :])
        max_end = len(reading_hira) - remaining_min
        for end in range(reading_index + 1, max_end + 1):
            nodes.append({"type": "ruby", "text": text, "reading": reading_hira[reading_index:end]})
            rec(index + 1, end, nodes)
            nodes.pop()
            if result is not None:
                return

    rec(0, 0, [])
    return result


def surface_reading_elements(surface: str) -> list[tuple[str, str]]:
    elements: list[tuple[str, str]] = []
    for char in surface:
        kind = surface_reading_kind(char)
        if elements and elements[-1][0] == kind:
            elements[-1] = (kind, elements[-1][1] + char)
        else:
            elements.append((kind, char))
    return elements


def surface_reading_kind(char: str) -> str:
    if is_latin(char):
        return "latin"
    if is_kana(char):
        return "kana"
    return "other"


def minimum_remaining_surface_reading(elements: list[tuple[str, str]]) -> int:
    total = 0
    for kind, text in elements:
        if kind == "kana":
            total += len(kana_surface_to_hira(text))
        elif kind == "other":
            total += len(text)
        else:
            total += 1
    return total


def has_latin(text: str) -> bool:
    return any(is_latin(char) for char in text)


def is_latin(char: str) -> bool:
    return bool(re.match(r"[A-Za-zＡ-Ｚａ-ｚ]", char))


def has_kana(text: str) -> bool:
    return any(is_kana(char) for char in text)


def is_kana(char: str) -> bool:
    return "\u3041" <= char <= "\u3096" or "\u30a1" <= char <= "\u30fa" or char in {"ー", "ｰ"}


def kana_surface_to_hira(text: str) -> str:
    return kata_to_hira(text).replace("ｰ", "ー")


def annotated_furigana_nodes(annotated: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    cursor = 0
    for match in re.finditer(r"([^（）]+?)（([^（）]+)）", annotated):
        if cursor < match.start():
            nodes.append({"type": "text", "text": annotated[cursor : match.start()]})
        surface_prefix, ruby_surface = split_trailing_ruby_surface(match.group(1))
        if surface_prefix:
            nodes.append({"type": "text", "text": surface_prefix})
        if ruby_surface:
            nodes.append({"type": "ruby", "text": ruby_surface, "reading": match.group(2)})
        cursor = match.end()
    if cursor < len(annotated):
        nodes.append({"type": "text", "text": annotated[cursor:]})
    return [node for node in nodes if node.get("text")]


def split_trailing_ruby_surface(text: str) -> tuple[str, str]:
    end = len(text)
    start = end
    while start > 0 and has_han(text[start - 1]):
        start -= 1
    return text[:start], text[start:end]


@lru_cache(maxsize=1)
def furigana_converter() -> FuriganaConverter:
    return FuriganaConverter.from_tsv_many([ANNOTATED_FORMS_PATH, SUPPLEMENTAL_FURIGANA_PATH])


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def store_review_submission(
    submission: dict[str, Any],
    *,
    submission_store_dir: str | Path,
) -> Path:
    store_dir = Path(submission_store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    submission_id = sanitize_submission_id(str(submission["submission_id"]))
    output_path = store_dir / f"{submission_id}.json"
    output_path.write_text(
        json.dumps(submission, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_review_submissions(
    submission_store_dir: str | Path,
    *,
    pack_id: str,
) -> list[dict[str, Any]]:
    store_dir = Path(submission_store_dir)
    rows: list[dict[str, Any]] = []
    if not store_dir.exists():
        return rows
    for path in sorted(store_dir.glob("*.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            continue
        if str(payload.get("review_stage")) != REVIEW_STAGE:
            continue
        if str(payload.get("pack_id")) != pack_id:
            continue
        payload["_source_path"] = str(path)
        rows.append(payload)
    rows.sort(
        key=lambda row: (
            int(row.get("generated_at_epoch", 0)),
            str(row.get("submission_id", "")),
            str(row.get("_source_path", "")),
        )
    )
    return rows


def replay_review_submissions(
    pack: dict[str, Any],
    submissions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    items_by_seq = {int(item["seq"]): item for item in pack.get("items", [])}
    effective: dict[str, dict[str, Any]] = {}

    for submission in submissions:
        overrides = {
            str(row["item_id"]): row
            for row in submission.get("overrides", [])
            if isinstance(row, dict) and str(row.get("item_id", "")) in items_by_id
        }
        for reviewed_range in submission.get("reviewed_ranges", []):
            from_seq = int(reviewed_range["from_seq"])
            to_seq = int(reviewed_range["to_seq"])
            if from_seq > to_seq:
                from_seq, to_seq = to_seq, from_seq
            for seq in range(from_seq, to_seq + 1):
                item = items_by_seq.get(seq)
                if item is None:
                    continue
                item_id = str(item["item_id"])
                effective[item_id] = {
                    "item_id": item_id,
                    "reviewed": True,
                    "skip": bool(item.get("skip_default", False)),
                    "targets": default_target_rows(item),
                    "span_overrides": [],
                    "note": "",
                    "submission_id": str(submission.get("submission_id", "")),
                    "generated_at_epoch": int(submission.get("generated_at_epoch", 0)),
                }
            for item_id, override in overrides.items():
                item = items_by_id[item_id]
                item_seq = int(item["seq"])
                if item_seq < from_seq or item_seq > to_seq:
                    continue
                current = effective.setdefault(
                    item_id,
                    {
                        "item_id": item_id,
                        "reviewed": True,
                        "skip": bool(item.get("skip_default", False)),
                        "targets": default_target_rows(item),
                        "span_overrides": [],
                        "note": "",
                    },
                )
                if "skip" in override:
                    current["skip"] = bool(override["skip"])
                current["targets"] = merge_default_and_explicit_target_rows(
                    item,
                    [row for row in override.get("targets", []) if isinstance(row, dict)],
                )
                current["span_overrides"] = [
                    row for row in override.get("span_overrides", []) if isinstance(row, dict)
                ]
                current["note"] = str(override.get("note", "")).strip()
                current["submission_id"] = str(submission.get("submission_id", ""))
                current["generated_at_epoch"] = int(submission.get("generated_at_epoch", 0))
    return effective


def apply_strong_repair_review_file(
    *,
    pack_json: Path,
    submission_store_dir: Path,
    strong_apply_summary_json: Path,
    output_summary_json: Path,
    units_jsonl: Path | None = None,
) -> dict[str, Any]:
    pack = load_json(pack_json)
    submissions = load_review_submissions_for_stage(
        submission_store_dir,
        pack_id=str(pack["pack_id"]),
        review_stage=STRONG_REPAIR_REVIEW_STAGE,
    )
    output_summary_json.parent.mkdir(parents=True, exist_ok=True)
    if not submissions:
        summary = {
            "stage_complete": False,
            "rule": STRONG_REPAIR_REVIEW_RULE,
            "pack_id": str(pack["pack_id"]),
            "submission_count": 0,
            "blocking_reason": (
                f"No strong yomi repair review submissions found for pack {pack['pack_id']}."
            ),
        }
        output_summary_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary

    effective = replay_simple_accept_reject_submissions(pack, submissions)
    items = [row for row in pack.get("items", []) if isinstance(row, dict)]
    reviewed_items = [row for row in items if str(row.get("item_id") or "") in effective]
    rejected_items = [
        item_id
        for item_id, state in effective.items()
        if str(state.get("decision") or "accept") == "reject"
    ]
    unreviewed_count = len(items) - len(reviewed_items)
    manual_summary = apply_manual_strong_repair_review_segments_file(
        pack=pack,
        effective=effective,
        units_jsonl=units_jsonl,
    )
    invalid_manual_items = manual_summary["invalid_items"]
    stage_complete = unreviewed_count == 0 and not rejected_items and invalid_manual_items == 0
    strong_summary = load_json(strong_apply_summary_json)
    if "stage_complete_before_confirmation" not in strong_summary:
        strong_summary["stage_complete_before_confirmation"] = bool(
            strong_summary.get("stage_complete")
        )
    strong_summary["confirmed"] = bool(stage_complete)
    if stage_complete:
        # Human review can explicitly accept a no-op strong-repair result. In that case the
        # raw LLM apply step remains diagnostic, but the overall strong-repair gate is complete.
        strong_summary["stage_complete"] = True
        strong_summary.pop("blocking_reason", None)
    strong_summary["confirmation_pack_id"] = str(pack["pack_id"])
    strong_summary["confirmation_submission_count"] = len(submissions)
    strong_summary["confirmation_rejected_items"] = rejected_items
    strong_summary["confirmation_unreviewed_items"] = unreviewed_count
    strong_summary["confirmation_manual_segment_overrides"] = manual_summary["applied_items"]
    strong_summary["confirmation_invalid_manual_segment_overrides"] = invalid_manual_items
    strong_apply_summary_json.write_text(
        json.dumps(strong_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "stage_complete": stage_complete,
        "rule": STRONG_REPAIR_REVIEW_RULE,
        "pack_id": str(pack["pack_id"]),
        "submission_count": len(submissions),
        "submission_paths": [str(row.get("_source_path", "")) for row in submissions],
        "item_count": len(items),
        "reviewed_items": len(reviewed_items),
        "unreviewed_items": unreviewed_count,
        "rejected_items": rejected_items,
        "manual_segment_overrides": manual_summary,
        "strong_apply_summary_json": str(strong_apply_summary_json),
        "summary_json": str(output_summary_json),
    }
    if not stage_complete:
        summary["blocking_reason"] = (
            "Strong yomi repair review is incomplete or contains rejected repairs."
        )
    output_summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def apply_manual_strong_repair_review_segments_file(
    *,
    pack: dict[str, Any],
    effective: dict[str, dict[str, Any]],
    units_jsonl: Path | None,
) -> dict[str, Any]:
    item_states: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in pack.get("items", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        state = effective.get(item_id)
        if not state or str(state.get("decision") or "accept") == "reject":
            continue
        if state.get("manual_segments"):
            item_states.append((item, state))
        region_by_id = {
            str(region.get("region_id") or region.get("item_id") or ""): region
            for region in item.get("regions", [])
            if isinstance(region, dict)
        }
        for region_state in state.get("regions", []):
            if not isinstance(region_state, dict) or not region_state.get("manual_segments"):
                continue
            region_id = str(region_state.get("region_id") or "")
            region = region_by_id.get(region_id)
            if region is not None:
                item_states.append((region, region_state))
    if not item_states:
        return {"applied_items": 0, "invalid_items": 0, "invalid": []}
    if units_jsonl is None:
        return {
            "applied_items": 0,
            "invalid_items": len(item_states),
            "invalid": [
                {
                    "item_id": str(item.get("region_id") or item.get("item_id") or ""),
                    "reason": "manual segments require units_jsonl",
                }
                for item, _state in item_states
            ],
        }

    overrides_by_unit: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for item, state in item_states:
        unit_id = str(item.get("unit_id") or "")
        if unit_id:
            overrides_by_unit.setdefault(unit_id, []).append((item, state))

    applied_items = 0
    invalid: list[dict[str, Any]] = []
    output_rows: list[str] = []
    with units_jsonl.open(encoding="utf-8") as src:
        for line in src:
            if not line.strip():
                continue
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id") or "")
            for item, state in overrides_by_unit.get(unit_id, []):
                result = apply_manual_strong_repair_segments(unit, item, state)
                if result["status"] == "applied":
                    applied_items += 1
                elif result["status"] != "unchanged":
                    invalid.append(
                        {
                            "item_id": str(item.get("region_id") or item.get("item_id") or ""),
                            **result,
                        }
                    )
            output_rows.append(json.dumps(unit, ensure_ascii=False))

    tmp_path = units_jsonl.with_suffix(units_jsonl.suffix + ".tmp")
    tmp_path.write_text("\n".join(output_rows) + ("\n" if output_rows else ""), encoding="utf-8")
    tmp_path.replace(units_jsonl)
    return {
        "applied_items": applied_items,
        "invalid_items": len(invalid),
        "invalid": invalid,
    }


def apply_manual_strong_repair_segments(
    unit: dict[str, Any],
    item: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    original_surface = str(item.get("rejected_span") or "")
    override = {
        "original_surface": original_surface,
        "segments": state.get("manual_segments") or [],
    }
    replacement_pairs = replacement_pairs_from_span_override(override)
    if not original_surface:
        return {"status": "invalid_manual_segments", "reason": "missing rejected span"}
    if not replacement_pairs:
        return {"status": "invalid_manual_segments", "reason": "invalid manual segment readings"}
    if "".join(surface for surface, _reading in replacement_pairs) != original_surface:
        return {
            "status": "invalid_manual_segments",
            "reason": "manual segment surfaces do not match rejected span",
            "rejected_span": original_surface,
            "replacement_span": "".join(surface for surface, _reading in replacement_pairs),
        }
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return {"status": "invalid_unit", "reason": "missing rendered yomi"}
    pairs = parse_rendered_pairs(rendered)
    span = find_unique_rendered_span(pairs, original_surface)
    if span is None:
        return {
            "status": "surface_mismatch",
            "reason": "manual rejected span is not unique in rendered yomi",
            "rejected_span": original_surface,
        }
    start, end = span
    if pairs[start:end] == replacement_pairs:
        return {"status": "unchanged", "rejected_span": original_surface}
    yomi.setdefault("rendered_before_strong_repair_review", rendered)
    pairs[start:end] = replacement_pairs
    yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    unit.setdefault("analysis", {}).setdefault("human_review", {})["yomi_strong_repair"] = {
        "rule": STRONG_REPAIR_REVIEW_RULE,
        "item_id": str(item.get("item_id") or ""),
        "manual_segments": [
            {"surface": surface, "reading": reading}
            for surface, reading in replacement_pairs
        ],
    }
    return {
        "status": "applied",
        "rejected_span": original_surface,
        "replacement": [
            {"surface": surface, "reading": reading}
            for surface, reading in replacement_pairs
        ],
    }


def load_review_submissions_for_stage(
    submission_store_dir: str | Path,
    *,
    pack_id: str,
    review_stage: str,
) -> list[dict[str, Any]]:
    store_dir = Path(submission_store_dir)
    rows: list[dict[str, Any]] = []
    if not store_dir.exists():
        return rows
    for path in sorted(store_dir.glob("*.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            continue
        if str(payload.get("review_stage")) != review_stage:
            continue
        if str(payload.get("pack_id")) != pack_id:
            continue
        payload["_source_path"] = str(path)
        rows.append(payload)
    rows.sort(
        key=lambda row: (
            int(row.get("generated_at_epoch", 0)),
            str(row.get("submission_id", "")),
            str(row.get("_source_path", "")),
        )
    )
    return rows


def replay_simple_accept_reject_submissions(
    pack: dict[str, Any],
    submissions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    items_by_seq = {int(item["seq"]): item for item in pack.get("items", [])}
    effective: dict[str, dict[str, Any]] = {}
    for submission in submissions:
        overrides = {
            str(row["item_id"]): row
            for row in submission.get("overrides", [])
            if isinstance(row, dict) and str(row.get("item_id", "")) in items_by_id
        }
        for reviewed_range in submission.get("reviewed_ranges", []):
            from_seq = int(reviewed_range["from_seq"])
            to_seq = int(reviewed_range["to_seq"])
            if from_seq > to_seq:
                from_seq, to_seq = to_seq, from_seq
            for seq in range(from_seq, to_seq + 1):
                item = items_by_seq.get(seq)
                if item is None:
                    continue
                item_id = str(item["item_id"])
                override = overrides.get(item_id, {})
                effective[item_id] = {
                    "item_id": item_id,
                    "decision": str(override.get("decision") or "accept"),
                    "manual_segments": [
                        row for row in override.get("manual_segments", []) if isinstance(row, dict)
                    ],
                    "regions": [
                        row for row in override.get("regions", []) if isinstance(row, dict)
                    ],
                    "note": str(override.get("note", "")).strip(),
                    "submission_id": str(submission.get("submission_id", "")),
                    "generated_at_epoch": int(submission.get("generated_at_epoch", 0)),
                }
    return effective


def default_target_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for target in item.get("targets", []):
        if not isinstance(target, dict):
            continue
        source = str(target.get("default_choice_source") or "current")
        if source == "current":
            continue
        rows.append(
            {
                "item_id": str(target.get("item_id") or ""),
                "choice_source": source,
                "selected_reading": target.get("default_reading"),
            }
        )
    return [row for row in rows if row["item_id"]]


def merge_default_and_explicit_target_rows(
    item: dict[str, Any],
    explicit_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {str(row["item_id"]): row for row in default_target_rows(item)}
    for row in explicit_rows:
        item_id = str(row.get("item_id") or "")
        if item_id:
            merged[item_id] = row
    return list(merged.values())


def apply_final_review_file(
    *,
    units_jsonl: Path,
    pack_json: Path,
    submission_store_dir: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    pack = load_json(pack_json)
    submissions = load_review_submissions(
        submission_store_dir,
        pack_id=str(pack["pack_id"]),
    )
    if not submissions:
        return {
            "stage_complete": False,
            "pack_id": str(pack["pack_id"]),
            "submission_count": 0,
            "blocking_reason": (
                f"No yomi final review submissions found for pack {pack['pack_id']}."
            ),
        }
    effective = replay_review_submissions(pack, submissions)
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    targets_by_id = {
        str(target["item_id"]): target
        for item in pack.get("items", [])
        for target in item.get("targets", [])
        if isinstance(target, dict) and target.get("item_id")
    }

    read_units = 0
    written_units = 0
    reviewed_units = 0
    skipped_units = 0
    target_override_count = 0
    span_override_count = 0
    exact_rendered_updates = 0
    exact_rendered_span_updates = 0
    no_ruby_target_count = 0
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            item_id = str(unit.get("unit_id", ""))
            item_state = effective.get(item_id)
            if item_state is not None:
                reviewed_units += 1
                item = items_by_id.get(item_id, {})
                target_overrides = [
                    build_target_override(row, targets_by_id)
                    for row in item_state.get("targets", [])
                    if isinstance(row, dict)
                ]
                target_overrides = [row for row in target_overrides if row is not None]
                span_overrides = normalize_span_overrides(item_state.get("span_overrides", []))
                target_override_count += len(target_overrides)
                span_override_count += len(span_overrides)
                no_ruby_target_count += sum(
                    1 for row in target_overrides if row.get("choice_source") == "none"
                )
                if item_state.get("skip"):
                    skipped_units += 1
                if item_state.get("skip"):
                    exact_target_updates = 0
                    exact_span_updates = 0
                else:
                    exact_target_updates = apply_exact_rendered_target_overrides(unit, target_overrides)
                    exact_span_updates = apply_exact_rendered_span_overrides(unit, span_overrides)
                exact_updates = exact_target_updates + exact_span_updates
                exact_rendered_updates += exact_updates
                exact_rendered_span_updates += exact_span_updates
                set_final_review_payload(
                    unit,
                    pack_id=str(pack["pack_id"]),
                    item_state=item_state,
                    item=item,
                    target_overrides=target_overrides,
                    span_overrides=span_overrides,
                    exact_rendered_updates=exact_updates,
                    exact_rendered_span_updates=exact_span_updates,
                )
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1

    unreviewed_units = read_units - reviewed_units
    stage_complete = unreviewed_units == 0
    summary = {
        "rule": APPLY_RULE,
        "pack_id": str(pack["pack_id"]),
        "submission_count": len(submissions),
        "submission_paths": [str(row.get("_source_path", "")) for row in submissions],
        "read_units": read_units,
        "written_units": written_units,
        "reviewed_units": reviewed_units,
        "unreviewed_units": unreviewed_units,
        "skipped_units": skipped_units,
        "target_override_count": target_override_count,
        "span_override_count": span_override_count,
        "no_ruby_target_count": no_ruby_target_count,
        "exact_rendered_updates": exact_rendered_updates,
        "exact_rendered_span_updates": exact_rendered_span_updates,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
    }
    if not stage_complete:
        summary["blocking_reason"] = (
            f"Yomi final review is incomplete: {unreviewed_units} of {read_units} "
            "units have not been reviewed."
        )
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"stage_complete": stage_complete, **summary}


def build_target_override(
    row: dict[str, Any],
    targets_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    target_id = str(row.get("item_id", ""))
    target = targets_by_id.get(target_id)
    if target is None:
        return None
    override = {
        "item_id": target_id,
        "choice_source": str(row.get("choice_source") or ""),
        "selected_reading": row.get("selected_reading"),
        "surface": target.get("surface"),
        "token_surface": target.get("token_surface"),
        "token_index": target.get("token_index"),
        "chunk_index": target.get("chunk_index"),
        "current_reading_hiragana": target.get("current_reading_hiragana"),
    }
    if override["choice_source"] == "none" and override["current_reading_hiragana"]:
        override["rejected_readings"] = [
            {
                "surface": target.get("surface"),
                "reading": override["current_reading_hiragana"],
                "source": "human_no_ruby",
            }
        ]
    return override


def normalize_span_overrides(rows: object) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return normalized_rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        decision = str(row.get("decision") or "")
        if decision not in {"reading", "segmentation"}:
            continue
        original_surface = str(row.get("original_surface") or "")
        segments = []
        for segment in row.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segments.append(
                {
                    "surface": str(segment.get("surface") or ""),
                    "reading": str(segment.get("reading") or ""),
                }
            )
        normalized_rows.append(
            {
                "id": str(row.get("id") or ""),
                "decision": decision,
                "target_item_ids": [
                    str(target_id)
                    for target_id in row.get("target_item_ids", [])
                    if str(target_id)
                ],
                "original_surface": original_surface,
                "segments": segments,
                "repair_required": bool(row.get("repair_required")),
                "repair_reason": str(row.get("repair_reason") or ""),
            }
        )
    return normalized_rows


def apply_exact_rendered_target_overrides(
    unit: dict[str, Any],
    target_overrides: list[dict[str, Any]],
) -> int:
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return 0
    pairs = parse_rendered_pairs(rendered)
    updated = 0
    for override in target_overrides:
        selected = override.get("selected_reading")
        if not isinstance(selected, str) or not selected:
            continue
        token_surface = str(override.get("token_surface") or "")
        if override.get("surface") != token_surface:
            continue
        replacement_index: int | None = None
        token_index = override.get("token_index")
        if isinstance(token_index, int) and 0 <= token_index < len(pairs):
            if pairs[token_index][0] == token_surface:
                replacement_index = token_index
        if replacement_index is None:
            span = find_unique_rendered_span(pairs, token_surface)
            if span is not None and span[1] - span[0] == 1:
                replacement_index = span[0]
        if replacement_index is None:
            continue
        surface, old_reading = pairs[replacement_index]
        new_reading = hira_to_kata(selected)
        if old_reading != new_reading:
            pairs[replacement_index] = (surface, new_reading)
            updated += 1
    if updated:
        yomi["rendered_before_final_review"] = rendered
        yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return updated


def apply_exact_rendered_span_overrides(
    unit: dict[str, Any],
    span_overrides: list[dict[str, Any]],
) -> int:
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return 0
    pairs = parse_rendered_pairs(rendered)
    updated = 0
    for override in span_overrides:
        original_surface = str(override.get("original_surface") or "")
        replacement_pairs = replacement_pairs_from_span_override(override)
        if not original_surface or not replacement_pairs:
            continue
        if "".join(surface for surface, _reading in replacement_pairs) != original_surface:
            continue
        span = find_unique_rendered_span(pairs, original_surface)
        if span is None:
            continue
        start, end = span
        if pairs[start:end] == replacement_pairs:
            continue
        pairs[start:end] = replacement_pairs
        updated += 1
    if updated:
        yomi.setdefault("rendered_before_final_review", rendered)
        yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return updated


def replacement_pairs_from_span_override(
    override: dict[str, Any],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for segment in override.get("segments", []):
        if not isinstance(segment, dict):
            return []
        surface = str(segment.get("surface") or "")
        reading = str(segment.get("reading") or "")
        normalized = normalize_hiragana_reading(reading)
        if not surface or not normalized or not is_valid_yomi_reading(normalized):
            return []
        pairs.append((surface, hira_to_kata(normalized)))
    return pairs


def set_final_review_payload(
    unit: dict[str, Any],
    *,
    pack_id: str,
    item_state: dict[str, Any],
    item: dict[str, Any],
    target_overrides: list[dict[str, Any]],
    span_overrides: list[dict[str, Any]],
    exact_rendered_updates: int,
    exact_rendered_span_updates: int,
) -> None:
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    human_review["yomi_final"] = {
        "rule": APPLY_RULE,
        "pack_id": pack_id,
        "reviewed": True,
        "item_id": item.get("item_id"),
        "skip": bool(item_state.get("skip")),
        "target_overrides": target_overrides,
        "span_overrides": span_overrides,
        "note": str(item_state.get("note", "")),
        "submission_id": str(item_state.get("submission_id", "")),
        "generated_at_epoch": int(item_state.get("generated_at_epoch", 0)),
        "exact_rendered_updates": exact_rendered_updates,
        "exact_rendered_span_updates": exact_rendered_span_updates,
    }


def build_strong_repair_queue_file(
    *,
    units_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    read_units = 0
    queued_items = 0
    target_escalations = 0
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            review = (
                unit.get("analysis", {})
                .get("human_review", {})
                .get("yomi_final", {})
            )
            if not isinstance(review, dict) or not review.get("reviewed"):
                continue
            if review.get("skip"):
                continue
            target_constraints = [
                row
                for row in review.get("target_overrides", [])
                if isinstance(row, dict)
            ]
            span_repair_overrides = [
                row
                for row in review.get("span_overrides", [])
                if isinstance(row, dict) and row.get("repair_required")
            ]
            span_repair_target_ids = {
                str(target_id)
                for span in span_repair_overrides
                for target_id in span.get("target_item_ids", [])
                if str(target_id)
            }
            target_escalation_overrides = [
                row
                for row in target_constraints
                if row.get("choice_source") == "none"
                and str(row.get("item_id") or "") not in span_repair_target_ids
                and not is_no_ruby_laughter_w_override(row)
            ]
            if not target_escalation_overrides and not span_repair_overrides:
                continue
            rendered_yomi = (
                unit.get("analysis", {})
                .get("mechanical", {})
                .get("yomi", {})
                .get("rendered")
            )
            target_groups = group_consecutive_target_overrides(target_escalation_overrides)
            target_escalations += len(target_escalation_overrides)
            for group_index, target_group in enumerate(target_groups, start=1):
                dst.write(
                    json.dumps(
                        {
                            "item_id": f"{unit.get('unit_id')}::target_group:{group_index}",
                            "unit_id": unit.get("unit_id"),
                            "doc_id": unit.get("doc_id"),
                            "text": unit.get("text"),
                            "rendered_yomi": rendered_yomi,
                            "repair_scope": "target_group",
                            "repair_order": 1,
                            "reasons": ["target_no_ruby"],
                            "target_constraints": target_group,
                            "target_escalations": target_group,
                            # Backward-compatible alias for existing mock consumers.
                            "target_overrides": target_group,
                            "status": "mock_pending",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                queued_items += 1
            for span_index, span_override in enumerate(span_repair_overrides, start=1):
                span_target = span_repair_target(span_override)
                if not span_target:
                    continue
                target_escalations += 1
                dst.write(
                    json.dumps(
                        {
                            "item_id": f"{unit.get('unit_id')}::span_group:{span_index}",
                            "unit_id": unit.get("unit_id"),
                            "doc_id": unit.get("doc_id"),
                            "text": unit.get("text"),
                            "rendered_yomi": rendered_yomi,
                            "repair_scope": "target_group",
                            "repair_order": 1,
                            "reasons": [span_override.get("repair_reason") or "span_repair_required"],
                            "target_constraints": [span_target],
                            "target_escalations": [span_target],
                            "target_overrides": [span_target],
                            "span_override": span_override,
                            "status": "mock_pending",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                queued_items += 1
    summary = {
        "rule": "yomi_strong_repair_queue_v1",
        "read_units": read_units,
        "queued_items": queued_items,
        "target_escalations": target_escalations,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
        "mock_only": False,
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def is_no_ruby_laughter_w_override(row: dict[str, Any]) -> bool:
    if row.get("choice_source") != "none":
        return False
    return is_standalone_laughter_w(str(row.get("surface") or ""))


def span_repair_target(span_override: dict[str, Any]) -> dict[str, Any] | None:
    surface = str(span_override.get("original_surface") or "")
    if not surface:
        segment_surfaces = [
            str(segment.get("surface") or "")
            for segment in span_override.get("segments", [])
            if isinstance(segment, dict)
        ]
        surface = "".join(segment_surfaces)
    if not surface:
        return None
    return {
        "surface": surface,
        "token_surface": surface,
        "choice_source": "none",
        "selected_reading": None,
        "source_span_override_id": str(span_override.get("id") or ""),
        "rejected_readings": [
            {
                "surface": surface,
                "reading": "",
                "source": span_override.get("repair_reason") or "human_span_repair",
            }
        ],
    }


def apply_yomi_strong_repair_results_file(
    *,
    units_jsonl: Path,
    queue_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    queue_rows = load_jsonl(queue_jsonl)
    result_rows = {str(row.get("item_id") or ""): row for row in load_jsonl(results_jsonl)}
    rows_by_unit: dict[str, list[dict[str, Any]]] = {}
    for row in queue_rows:
        unit_id = str(row.get("unit_id") or "")
        if unit_id:
            rows_by_unit.setdefault(unit_id, []).append(row)

    read_units = 0
    written_units = 0
    queued_items = len(queue_rows)
    applied_items = 0
    missing_results = 0
    parse_error_items = 0
    invalid_items = 0
    noop_items = 0
    unsupported_items = 0
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id") or "")
            repair_log: list[dict[str, Any]] = []
            for queue_row in sorted(
                rows_by_unit.get(unit_id, []),
                key=strong_repair_apply_sort_key,
                reverse=True,
            ):
                item_id = str(queue_row.get("item_id") or "")
                result = result_rows.get(item_id)
                if result is None:
                    missing_results += 1
                    repair_log.append({"item_id": item_id, "status": "missing_result"})
                    continue
                if result.get("parse_error"):
                    parse_error_items += 1
                    repair_log.append(
                        {
                            "item_id": item_id,
                            "status": "parse_error",
                            "parse_error": result.get("parse_error"),
                        }
                    )
                    continue
                if queue_row.get("repair_scope") != "target_group":
                    unsupported_items += 1
                    repair_log.append(
                        {
                            "item_id": item_id,
                            "status": "unsupported_scope",
                            "repair_scope": queue_row.get("repair_scope"),
                        }
                    )
                    continue
                apply_result = apply_target_group_strong_repair(unit, queue_row, result)
                repair_log.append({"item_id": item_id, **apply_result})
                if apply_result["status"] == "applied":
                    applied_items += 1
                elif apply_result["status"] in {"reused_rejected_reading", "unchanged"}:
                    noop_items += 1
                else:
                    invalid_items += 1
            if repair_log:
                unit.setdefault("analysis", {}).setdefault("llm", {})["yomi_strong_repair"] = {
                    "rule": "yomi_strong_repair_apply_v1",
                    "repairs": repair_log,
                }
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1

    unapplied_items = queued_items - applied_items
    summary = {
        "rule": "yomi_strong_repair_apply_v1",
        "stage_complete": unapplied_items == 0,
        "read_units": read_units,
        "written_units": written_units,
        "queued_items": queued_items,
        "result_count": len(result_rows),
        "applied_items": applied_items,
        "unapplied_items": unapplied_items,
        "missing_results": missing_results,
        "parse_error_items": parse_error_items,
        "invalid_items": invalid_items,
        "noop_items": noop_items,
        "unsupported_items": unsupported_items,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
    }
    if unapplied_items:
        summary["blocking_reason"] = (
            "Strong yomi repair has unapplied items; inspect yomi_strong_repair_apply_summary.json."
        )
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def strong_repair_apply_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    targets = [target for target in row.get("target_escalations", []) if isinstance(target, dict)]
    token_indexes = [
        int(target["token_index"])
        for target in targets
        if isinstance(target.get("token_index"), int)
    ]
    return (
        int(row.get("repair_order") or 0),
        max(token_indexes) if token_indexes else -1,
        str(row.get("item_id") or ""),
    )


def apply_target_group_strong_repair(
    unit: dict[str, Any],
    queue_row: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    parsed = result.get("parsed")
    if not isinstance(parsed, list) or not parsed:
        return {"status": "invalid_result", "reason": "parsed result is not a non-empty array"}
    replacement_pairs = []
    for item in parsed:
        if not isinstance(item, dict):
            return {"status": "invalid_result", "reason": "parsed item is not an object"}
        surface = str(item.get("surface") or "")
        reading = str(item.get("reading") or "")
        if not surface:
            return {"status": "invalid_result", "reason": "empty replacement surface"}
        normalized = normalize_hiragana_reading(reading)
        if not is_valid_yomi_reading(normalized):
            return {
                "status": "invalid_result",
                "reason": "invalid replacement reading",
                "surface": surface,
                "reading": reading,
            }
        replacement_pairs.append((surface, hira_to_kata(normalized)))

    targets = [target for target in queue_row.get("target_escalations", []) if isinstance(target, dict)]
    rejected_pairs = rejected_surface_reading_pairs(targets)
    for surface, reading in replacement_pairs:
        if (surface, normalize_hiragana_reading(reading)) in rejected_pairs:
            return {
                "status": "reused_rejected_reading",
                "reason": "replacement reused rejected reading",
                "surface": surface,
                "reading": reading,
            }
    token_indexes = [
        int(target["token_index"])
        for target in targets
        if isinstance(target.get("token_index"), int)
    ]
    if not targets:
        return {"status": "invalid_queue", "reason": "target group lacks targets"}
    rejected_span = "".join(str(target.get("surface") or "") for target in targets)
    replacement_span = "".join(surface for surface, _reading in replacement_pairs)
    if replacement_span != rejected_span:
        return {
            "status": "surface_mismatch",
            "rejected_span": rejected_span,
            "replacement_span": replacement_span,
        }

    yomi = unit.setdefault("analysis", {}).setdefault("mechanical", {}).setdefault("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return {"status": "invalid_unit", "reason": "missing rendered yomi"}
    pairs = parse_rendered_pairs(rendered)
    if len(token_indexes) == len(targets):
        start = min(token_indexes)
        end = max(token_indexes) + 1
        if end > len(pairs):
            return {"status": "invalid_queue", "reason": "target token index out of range"}
        original_span = "".join(surface for surface, _reading in pairs[start:end])
        if original_span != rejected_span:
            fallback = find_unique_rendered_span(pairs, rejected_span)
            if fallback is None:
                return {
                    "status": "surface_mismatch",
                    "rejected_span": rejected_span,
                    "original_span": original_span,
                }
            start, end = fallback
    else:
        fallback = find_unique_rendered_span(pairs, rejected_span)
        if fallback is None:
            return {
                "status": "invalid_queue",
                "reason": "target group lacks token indexes and surface span is not unique",
                "rejected_span": rejected_span,
            }
        start, end = fallback
    yomi.setdefault("rendered_before_strong_repair", rendered)
    pairs[start:end] = replacement_pairs
    yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return {
        "status": "applied",
        "rejected_span": rejected_span,
        "replacement": [
            {"surface": surface, "reading": reading}
            for surface, reading in replacement_pairs
        ],
    }


def rejected_surface_reading_pairs(targets: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for target in targets:
        for rejected in target.get("rejected_readings", []) or []:
            if not isinstance(rejected, dict):
                continue
            surface = str(rejected.get("surface") or "")
            reading = str(rejected.get("reading") or "")
            if surface and reading:
                pairs.add((surface, normalize_hiragana_reading(reading)))
    return pairs


def find_unique_rendered_span(
    pairs: list[tuple[str, str]],
    surface_span: str,
) -> tuple[int, int] | None:
    matches: list[tuple[int, int]] = []
    for start in range(len(pairs)):
        joined = ""
        for end in range(start + 1, len(pairs) + 1):
            joined += pairs[end - 1][0]
            if joined == surface_span:
                matches.append((start, end))
                break
            if not surface_span.startswith(joined):
                break
    if len(matches) != 1:
        return None
    return matches[0]


def group_consecutive_target_overrides(targets: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    def sort_key(target: dict[str, Any]) -> tuple[int, int, str]:
        token_index = target.get("token_index")
        chunk_index = target.get("chunk_index")
        return (
            int(chunk_index) if isinstance(chunk_index, int) else 0,
            int(token_index) if isinstance(token_index, int) else 10**9,
            str(target.get("item_id", "")),
        )

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_chunk: int | None = None
    previous_token: int | None = None
    for target in sorted(targets, key=sort_key):
        chunk_index = target.get("chunk_index")
        token_index = target.get("token_index")
        chunk = chunk_index if isinstance(chunk_index, int) else None
        token = token_index if isinstance(token_index, int) else None
        continues_previous = (
            bool(current)
            and chunk is not None
            and token is not None
            and previous_chunk == chunk
            and previous_token is not None
            and token == previous_token + 1
        )
        if not continues_previous:
            if current:
                groups.append(current)
            current = []
        current.append(target)
        previous_chunk = chunk
        previous_token = token
    if current:
        groups.append(current)
    return groups


def finalize_reviewed_yomi_file(
    *,
    units_jsonl: Path,
    strong_queue_summary_json: Path,
    strong_apply_summary_json: Path | None = None,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    strong_summary = load_json(strong_queue_summary_json)
    queued_items = int(strong_summary.get("queued_items", 0))
    if queued_items:
        if strong_apply_summary_json is None or not strong_apply_summary_json.exists():
            return {
                "stage_complete": False,
                "queued_items": queued_items,
                "blocking_reason": "Strong yomi repair queue is not empty and has not been applied yet.",
            }
        strong_apply_summary = load_json(strong_apply_summary_json)
        if not strong_apply_summary.get("stage_complete"):
            return {
                "stage_complete": False,
                "queued_items": queued_items,
                "blocking_reason": str(
                    strong_apply_summary.get(
                        "blocking_reason",
                        "Strong yomi repair has unapplied items.",
                    )
                ),
            }
        if not strong_apply_summary.get("confirmed"):
            return {
                "stage_complete": False,
                "queued_items": queued_items,
                "blocking_reason": (
                    "Strong yomi repair results require human confirmation before finalization."
                ),
            }
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    read_units = 0
    written_units = 0
    skipped_units = 0
    unreviewed_units = 0
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            review = (
                unit.get("analysis", {})
                .get("human_review", {})
                .get("yomi_final", {})
            )
            if not isinstance(review, dict) or not review.get("reviewed"):
                unreviewed_units += 1
                continue
            if review.get("skip"):
                skipped_units += 1
                continue
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1
    summary = {
        "rule": "yomi_finalized_no_strong_repairs_v1",
        "read_units": read_units,
        "written_units": written_units,
        "skipped_units": skipped_units,
        "unreviewed_units": unreviewed_units,
        "strong_queue_items": queued_items,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"stage_complete": True, **summary}


def harvest_yomi_finalization_artifacts_file(
    *,
    final_units_jsonl: Path,
    batch_manual_rewrites_jsonl: Path,
    batch_supplemental_furigana_tsv: Path,
    global_manual_rewrites_jsonl: Path,
    global_supplemental_furigana_tsv: Path,
    summary_json: Path,
    batch_name: str,
    track_name: str,
) -> dict[str, Any]:
    units = load_jsonl(final_units_jsonl)
    manual_rewrites = harvest_manual_yomi_rewrites(
        units,
        batch_name=batch_name,
        track_name=track_name,
    )
    supplemental_furigana = harvest_supplemental_furigana(
        units,
        batch_name=batch_name,
        track_name=track_name,
    )
    write_jsonl(batch_manual_rewrites_jsonl, manual_rewrites)
    write_tsv(
        batch_supplemental_furigana_tsv,
        supplemental_furigana,
        [
            "surface",
            "reading",
            "annotated_surface",
            "source_batch",
            "source_track",
            "source_unit_id",
            "source_method",
        ],
    )
    appended_rewrites = append_unique_jsonl(
        global_manual_rewrites_jsonl,
        manual_rewrites,
        key_fields=["original_surface", "replacement_rendered"],
    )
    appended_furigana = append_unique_tsv(
        global_supplemental_furigana_tsv,
        supplemental_furigana,
        [
            "surface",
            "reading",
            "annotated_surface",
            "source_batch",
            "source_track",
            "source_unit_id",
            "source_method",
        ],
        key_fields=["surface", "reading", "annotated_surface"],
    )
    summary = {
        "rule": "yomi_finalization_harvest_v1",
        "batch_name": batch_name,
        "track_name": track_name,
        "unit_count": len(units),
        "manual_rewrite_count": len(manual_rewrites),
        "manual_rewrite_appended_count": appended_rewrites,
        "supplemental_furigana_count": len(supplemental_furigana),
        "supplemental_furigana_appended_count": appended_furigana,
        "batch_manual_rewrites_jsonl": str(batch_manual_rewrites_jsonl),
        "batch_supplemental_furigana_tsv": str(batch_supplemental_furigana_tsv),
        "global_manual_rewrites_jsonl": str(global_manual_rewrites_jsonl),
        "global_supplemental_furigana_tsv": str(global_supplemental_furigana_tsv),
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def harvest_manual_yomi_rewrites(
    units: list[dict[str, Any]],
    *,
    batch_name: str,
    track_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for unit in units:
        unit_id = str(unit.get("unit_id") or "")
        for repair in (
            unit.get("analysis", {})
            .get("llm", {})
            .get("yomi_strong_repair", {})
            .get("repairs", [])
        ):
            if not isinstance(repair, dict) or repair.get("status") != "applied":
                continue
            row = manual_rewrite_row_from_repair(
                repair,
                batch_name=batch_name,
                track_name=track_name,
                unit_id=unit_id,
                source="llm_strong_repair",
            )
            if row and (row["original_surface"], row["replacement_rendered"]) not in seen:
                seen.add((row["original_surface"], row["replacement_rendered"]))
                rows.append(row)
        manual = (
            unit.get("analysis", {})
            .get("human_review", {})
            .get("yomi_strong_repair", {})
        )
        if isinstance(manual, dict) and manual.get("manual_segments"):
            row = manual_rewrite_row_from_repair(
                {
                    "rejected_span": "".join(
                        str(segment.get("surface") or "")
                        for segment in manual.get("manual_segments", [])
                        if isinstance(segment, dict)
                    ),
                    "replacement": manual.get("manual_segments"),
                    "item_id": manual.get("item_id"),
                },
                batch_name=batch_name,
                track_name=track_name,
                unit_id=unit_id,
                source="human_strong_repair",
            )
            if row and (row["original_surface"], row["replacement_rendered"]) not in seen:
                seen.add((row["original_surface"], row["replacement_rendered"]))
                rows.append(row)
    return rows


def manual_rewrite_row_from_repair(
    repair: dict[str, Any],
    *,
    batch_name: str,
    track_name: str,
    unit_id: str,
    source: str,
) -> dict[str, Any] | None:
    original_surface = str(repair.get("rejected_span") or "")
    replacement = [row for row in repair.get("replacement", []) if isinstance(row, dict)]
    if not original_surface or not replacement:
        return None
    replacement_pairs: list[tuple[str, str]] = []
    for row in replacement:
        surface = str(row.get("surface") or "")
        reading = str(row.get("reading") or "")
        if not surface:
            return None
        replacement_pairs.append((surface, hira_to_kata(reading)))
    if "".join(surface for surface, _reading in replacement_pairs) != original_surface:
        return None
    replacement_rendered = " ".join(f"{surface}/{reading}" for surface, reading in replacement_pairs)
    return {
        "original_surface": original_surface,
        "replacement_rendered": replacement_rendered,
        "replacement": [
            {"surface": surface, "reading": reading}
            for surface, reading in replacement_pairs
        ],
        "source": source,
        "source_batch": batch_name,
        "source_track": track_name,
        "source_unit_id": unit_id,
        "source_item_id": str(repair.get("item_id") or ""),
    }


def harvest_supplemental_furigana(
    units: list[dict[str, Any]],
    *,
    batch_name: str,
    track_name: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    converter = base_furigana_converter()
    for unit in units:
        unit_id = str(unit.get("unit_id") or "")
        rendered = str(
            unit.get("analysis", {})
            .get("mechanical", {})
            .get("yomi", {})
            .get("rendered")
            or ""
        )
        for surface, reading in parse_rendered_pairs(rendered):
            if not surface or not reading or not has_han(surface):
                continue
            result = converter.convert(surface, reading)
            if not result.annotated_surface or result.method == "exact_lookup":
                continue
            key = (surface, result.reading, result.annotated_surface)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "surface": surface,
                    "reading": result.reading,
                    "annotated_surface": result.annotated_surface,
                    "source_batch": batch_name,
                    "source_track": track_name,
                    "source_unit_id": unit_id,
                    "source_method": result.method,
                }
            )
    return rows


@lru_cache(maxsize=1)
def base_furigana_converter() -> FuriganaConverter:
    return FuriganaConverter.from_tsv_many([ANNOTATED_FORMS_PATH])


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def append_unique_jsonl(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    key_fields: list[str],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, ...]] = set()
    if path.exists():
        for row in load_jsonl(path):
            existing.add(tuple(str(row.get(field) or "") for field in key_fields))
    appended = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            key = tuple(str(row.get(field) or "") for field in key_fields)
            if key in existing:
                continue
            existing.add(key)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            appended += 1
    return appended


def append_unique_tsv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    *,
    key_fields: list[str],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, ...]] = set()
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                existing.add(tuple(str(row.get(field) or "") for field in key_fields))
    needs_header = not path.exists() or path.stat().st_size == 0
    appended = 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        if needs_header:
            writer.writeheader()
        for row in rows:
            key = tuple(str(row.get(field) or "") for field in key_fields)
            if key in existing:
                continue
            existing.add(key)
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            appended += 1
    return appended


def parse_rendered_pairs(rendered: str) -> list[tuple[str, str]]:
    pairs = []
    for token in rendered.split():
        if "/" not in token:
            pairs.append((token, ""))
            continue
        surface, reading = token.rsplit("/", 1)
        pairs.append((surface, reading))
    return pairs


def hira_to_kata(text: str) -> str:
    chars = []
    for char in text:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            chars.append(chr(code + 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def sanitize_submission_id(submission_id: str) -> str:
    keep = []
    for char in submission_id:
        if char.isalnum() or char in {"_", "-", "."}:
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep)


def current_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def write_summary(summary: YomiFinalReviewPackSummary, summary_json: Path) -> None:
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
