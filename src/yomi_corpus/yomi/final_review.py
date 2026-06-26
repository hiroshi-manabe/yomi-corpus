from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.llm_readings import normalize_hiragana_reading


REVIEW_STAGE = "yomi_final_review"
SCHEMA_VERSION = 1
APPLY_RULE = "yomi_final_review_apply_v1"


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
            "source": "none",
            "label": "No ruby",
            "reading": None,
            "accepted": False,
        }
    )
    return candidates


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
                "reading": target.get("current_reading_hiragana"),
                "is_safe": target.get("is_safe"),
                "highlight_level": target.get("highlight_level"),
            }
        )
        cursor = end
    if cursor < len(text):
        segments.append({"type": "text", "text": text[cursor:]})
    return segments


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
                    "escalate_sentence": False,
                    "targets": [],
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
                        "escalate_sentence": False,
                        "targets": [],
                        "note": "",
                    },
                )
                if "skip" in override:
                    current["skip"] = bool(override["skip"])
                if "escalate_sentence" in override:
                    current["escalate_sentence"] = bool(override["escalate_sentence"])
                current["targets"] = [
                    row for row in override.get("targets", []) if isinstance(row, dict)
                ]
                current["note"] = str(override.get("note", "")).strip()
                current["submission_id"] = str(submission.get("submission_id", ""))
                current["generated_at_epoch"] = int(submission.get("generated_at_epoch", 0))
    return effective


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
    escalated_units = 0
    target_override_count = 0
    exact_rendered_updates = 0
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
                target_override_count += len(target_overrides)
                no_ruby_target_count += sum(
                    1 for row in target_overrides if row.get("choice_source") == "none"
                )
                if item_state.get("skip"):
                    skipped_units += 1
                if item_state.get("escalate_sentence"):
                    escalated_units += 1
                exact_updates = apply_exact_rendered_target_overrides(unit, target_overrides)
                exact_rendered_updates += exact_updates
                set_final_review_payload(
                    unit,
                    pack_id=str(pack["pack_id"]),
                    item_state=item_state,
                    item=item,
                    target_overrides=target_overrides,
                    exact_rendered_updates=exact_updates,
                )
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1

    summary = {
        "rule": APPLY_RULE,
        "pack_id": str(pack["pack_id"]),
        "submission_count": len(submissions),
        "submission_paths": [str(row.get("_source_path", "")) for row in submissions],
        "read_units": read_units,
        "written_units": written_units,
        "reviewed_units": reviewed_units,
        "unreviewed_units": read_units - reviewed_units,
        "skipped_units": skipped_units,
        "escalated_units": escalated_units,
        "target_override_count": target_override_count,
        "no_ruby_target_count": no_ruby_target_count,
        "exact_rendered_updates": exact_rendered_updates,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"stage_complete": True, **summary}


def build_target_override(
    row: dict[str, Any],
    targets_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    target_id = str(row.get("item_id", ""))
    target = targets_by_id.get(target_id)
    if target is None:
        return None
    return {
        "item_id": target_id,
        "choice_source": str(row.get("choice_source") or ""),
        "selected_reading": row.get("selected_reading"),
        "surface": target.get("surface"),
        "token_surface": target.get("token_surface"),
        "token_index": target.get("token_index"),
        "chunk_index": target.get("chunk_index"),
        "current_reading_hiragana": target.get("current_reading_hiragana"),
    }


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
        if override.get("surface") != override.get("token_surface"):
            continue
        token_index = override.get("token_index")
        if not isinstance(token_index, int) or token_index < 0 or token_index >= len(pairs):
            continue
        surface, old_reading = pairs[token_index]
        if surface != override.get("token_surface"):
            continue
        new_reading = hira_to_kata(selected)
        if old_reading != new_reading:
            pairs[token_index] = (surface, new_reading)
            updated += 1
    if updated:
        yomi["rendered_before_final_review"] = rendered
        yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return updated


def set_final_review_payload(
    unit: dict[str, Any],
    *,
    pack_id: str,
    item_state: dict[str, Any],
    item: dict[str, Any],
    target_overrides: list[dict[str, Any]],
    exact_rendered_updates: int,
) -> None:
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    human_review["yomi_final"] = {
        "rule": APPLY_RULE,
        "pack_id": pack_id,
        "reviewed": True,
        "item_id": item.get("item_id"),
        "skip": bool(item_state.get("skip")),
        "escalate_sentence": bool(item_state.get("escalate_sentence")),
        "target_overrides": target_overrides,
        "note": str(item_state.get("note", "")),
        "submission_id": str(item_state.get("submission_id", "")),
        "generated_at_epoch": int(item_state.get("generated_at_epoch", 0)),
        "exact_rendered_updates": exact_rendered_updates,
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
    sentence_escalations = 0
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
            reasons = []
            if review.get("escalate_sentence"):
                reasons.append("sentence_escalation")
                sentence_escalations += 1
            target_constraints = [
                row
                for row in review.get("target_overrides", [])
                if isinstance(row, dict)
            ]
            target_escalation_overrides = [
                row
                for row in target_constraints
                if row.get("choice_source") == "none"
            ]
            if target_escalation_overrides:
                reasons.append("target_no_ruby")
                target_escalations += len(target_escalation_overrides)
            if not reasons:
                continue
            dst.write(
                json.dumps(
                    {
                        "unit_id": unit.get("unit_id"),
                        "text": unit.get("text"),
                        "rendered_yomi": (
                            unit.get("analysis", {})
                            .get("mechanical", {})
                            .get("yomi", {})
                            .get("rendered")
                        ),
                        "reasons": reasons,
                        "target_constraints": target_constraints,
                        "target_escalations": target_escalation_overrides,
                        # Backward-compatible alias for existing mock consumers.
                        "target_overrides": target_escalation_overrides,
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
        "sentence_escalations": sentence_escalations,
        "target_escalations": target_escalations,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
        "mock_only": True,
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def finalize_reviewed_yomi_file(
    *,
    units_jsonl: Path,
    strong_queue_summary_json: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    strong_summary = load_json(strong_queue_summary_json)
    queued_items = int(strong_summary.get("queued_items", 0))
    if queued_items:
        return {
            "stage_complete": False,
            "queued_items": queued_items,
            "blocking_reason": (
                "Strong yomi repair queue is not empty; real strong-LLM repair is not implemented yet."
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
