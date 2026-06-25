from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.llm_readings import normalize_hiragana_reading


REVIEW_STAGE = "yomi_final_review"
SCHEMA_VERSION = 1


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


def build_yomi_final_review_pack_file(
    *,
    units_jsonl: Path,
    output_json: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    latest_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> YomiFinalReviewPackSummary:
    pack = build_yomi_final_review_pack(
        units_jsonl=units_jsonl,
        pack_id=pack_id,
        track_name=track_name,
        batch_name=batch_name,
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


def build_yomi_final_review_pack(
    *,
    units_jsonl: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    created_at_epoch: int | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    doc_order: dict[str, int] = {}
    created = created_at_epoch if created_at_epoch is not None else current_epoch()

    with units_jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            unit = json.loads(line)
            doc_id = str(unit.get("doc_id") or "")
            if doc_id not in doc_order:
                doc_order[doc_id] = len(doc_order) + 1
            item = build_review_item(unit, seq=len(items) + 1, doc_seq=doc_order[doc_id])
            items.append(item)

    unresolved_items = [item for item in items if item["unresolved_target_count"] > 0]
    unresolved_targets = sum(int(item["unresolved_target_count"]) for item in items)
    provisional_skip_items = [item for item in items if item["provisional_skip"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "review_stage": REVIEW_STAGE,
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
            "document_count": len(doc_order),
            "unresolved_item_count": len(unresolved_items),
            "unresolved_target_count": unresolved_targets,
            "provisional_skip_item_count": len(provisional_skip_items),
        },
        "items": items,
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
    }


def build_review_target(target: dict[str, Any]) -> dict[str, Any]:
    candidates = reading_candidates(target)
    return {
        "item_id": str(target.get("item_id") or ""),
        "surface": str(target.get("surface") or ""),
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
        "candidates": candidates,
        "signals": target.get("signals") if isinstance(target.get("signals"), list) else [],
    }


def reading_candidates(target: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(source: str, label: str, reading: object, *, accepted: bool = False) -> None:
        if not isinstance(reading, str) or not reading:
            return
        normalized = normalize_hiragana_reading(reading)
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
        accepted=bool(target.get("is_safe")),
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
            "source": "other",
            "label": "Other / none of these",
            "reading": None,
            "accepted": False,
        }
    )
    return candidates


def current_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def write_summary(summary: YomiFinalReviewPackSummary, summary_json: Path) -> None:
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
