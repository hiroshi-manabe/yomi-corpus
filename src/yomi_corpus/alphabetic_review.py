from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from time import time

from yomi_corpus.alphabetic_state import AlphabeticDecision, load_alphabetic_decisions


@dataclass(frozen=True)
class AlphabeticLLMJudgment:
    batch_name: str
    entity_key: str
    strict_case: bool
    llm_status: str
    confidence: str
    note: str
    occurrence_count: int
    unit_count: int
    surface_forms: list[str]
    example_unit_ids: list[str]
    example_texts: list[str]
    source_path: str


@dataclass(frozen=True)
class AlphabeticPromotionCandidate:
    entity_key: str
    strict_case: bool
    proposed_decision: str
    threshold_observations: int
    supporting_observations: int
    opposing_observations: int
    supporting_batch_count: int
    opposing_batch_count: int
    source_batches: list[str]
    confidence_counts: dict[str, int]
    surface_forms: list[str]
    example_texts: list[str]
    note_samples: list[str]


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    path_obj = Path(path)
    if not path_obj.exists():
        return rows
    with path_obj.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def append_alphabetic_llm_judgments(
    path: str | Path,
    records: list[AlphabeticLLMJudgment],
) -> None:
    judgments_path = Path(path)
    judgments_path.parent.mkdir(parents=True, exist_ok=True)
    incoming_batch_names = {record.batch_name for record in records}
    kept_payloads: list[dict] = []
    if judgments_path.exists():
        with judgments_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload["batch_name"]) in incoming_batch_names:
                    continue
                kept_payloads.append(payload)

    with judgments_path.open("w", encoding="utf-8") as handle:
        for payload in kept_payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def build_llm_judgments_from_results(
    rows: list[dict],
    *,
    batch_name: str,
    source_path: str,
) -> list[AlphabeticLLMJudgment]:
    judgments: list[AlphabeticLLMJudgment] = []
    for row in rows:
        if row.get("parse_error"):
            continue
        parsed = row.get("parsed")
        if not isinstance(parsed, dict):
            continue
        status = str(parsed.get("status", ""))
        if status not in {"in_scope", "out_of_scope"}:
            continue
        source_row = ((row.get("metadata") or {}).get("source_row") or {})
        judgments.append(
            AlphabeticLLMJudgment(
                batch_name=batch_name,
                entity_key=str(source_row.get("entity_key", row.get("item_id"))),
                strict_case=bool(source_row.get("strict_case", False)),
                llm_status=status,
                confidence=str(parsed.get("confidence", "")),
                note=str(parsed.get("note", "")),
                occurrence_count=int(source_row.get("occurrence_count", 0)),
                unit_count=int(source_row.get("unit_count", 0)),
                surface_forms=list(source_row.get("surface_forms", [])),
                example_unit_ids=list(source_row.get("example_unit_ids", [])),
                example_texts=list(source_row.get("example_texts", [])),
                source_path=source_path,
            )
        )
    return judgments


def build_promotion_candidates(
    judgments: list[dict],
    *,
    threshold_observations: int,
    existing_decisions: dict[str, object] | None = None,
    max_examples: int = 3,
    max_notes: int = 3,
) -> list[AlphabeticPromotionCandidate]:
    existing_decisions = existing_decisions or {}
    buckets: dict[str, dict] = {}
    for row in judgments:
        entity_key = str(row["entity_key"])
        if entity_key in existing_decisions:
            continue
        status = str(row["llm_status"])
        if status not in {"in_scope", "out_of_scope"}:
            continue
        bucket = buckets.setdefault(
            entity_key,
            {
                "strict_case": bool(row.get("strict_case", False)),
                "in_scope_observations": 0,
                "out_of_scope_observations": 0,
                "in_scope_batches": set(),
                "out_of_scope_batches": set(),
                "confidence_counts": {},
                "surface_forms": [],
                "surface_seen": set(),
                "example_texts": [],
                "example_seen": set(),
                "note_samples": [],
                "note_seen": set(),
            },
        )
        count = int(row.get("occurrence_count", 0))
        batch_name = str(row.get("batch_name", ""))
        confidence = str(row.get("confidence", ""))
        note = str(row.get("note", "")).strip()

        if status == "in_scope":
            bucket["in_scope_observations"] += count
            bucket["in_scope_batches"].add(batch_name)
        else:
            bucket["out_of_scope_observations"] += count
            bucket["out_of_scope_batches"].add(batch_name)

        if confidence:
            bucket["confidence_counts"][confidence] = bucket["confidence_counts"].get(confidence, 0) + 1

        for surface_form in row.get("surface_forms", []):
            if surface_form not in bucket["surface_seen"]:
                bucket["surface_seen"].add(surface_form)
                bucket["surface_forms"].append(surface_form)

        for example_text in row.get("example_texts", []):
            if example_text not in bucket["example_seen"] and len(bucket["example_texts"]) < max_examples:
                bucket["example_seen"].add(example_text)
                bucket["example_texts"].append(example_text)

        if note and note not in bucket["note_seen"] and len(bucket["note_samples"]) < max_notes:
            bucket["note_seen"].add(note)
            bucket["note_samples"].append(note)

    candidates: list[AlphabeticPromotionCandidate] = []
    for entity_key, bucket in sorted(buckets.items()):
        in_scope_observations = int(bucket["in_scope_observations"])
        out_of_scope_observations = int(bucket["out_of_scope_observations"])
        if in_scope_observations >= threshold_observations and out_of_scope_observations == 0:
            candidates.append(
                AlphabeticPromotionCandidate(
                    entity_key=entity_key,
                    strict_case=bool(bucket["strict_case"]),
                    proposed_decision="whitelist",
                    threshold_observations=threshold_observations,
                    supporting_observations=in_scope_observations,
                    opposing_observations=out_of_scope_observations,
                    supporting_batch_count=len(bucket["in_scope_batches"]),
                    opposing_batch_count=len(bucket["out_of_scope_batches"]),
                    source_batches=sorted(bucket["in_scope_batches"]),
                    confidence_counts=dict(sorted(bucket["confidence_counts"].items())),
                    surface_forms=list(bucket["surface_forms"]),
                    example_texts=list(bucket["example_texts"]),
                    note_samples=list(bucket["note_samples"]),
                )
            )
        elif out_of_scope_observations >= threshold_observations and in_scope_observations == 0:
            candidates.append(
                AlphabeticPromotionCandidate(
                    entity_key=entity_key,
                    strict_case=bool(bucket["strict_case"]),
                    proposed_decision="blacklist",
                    threshold_observations=threshold_observations,
                    supporting_observations=out_of_scope_observations,
                    opposing_observations=in_scope_observations,
                    supporting_batch_count=len(bucket["out_of_scope_batches"]),
                    opposing_batch_count=len(bucket["in_scope_batches"]),
                    source_batches=sorted(bucket["out_of_scope_batches"]),
                    confidence_counts=dict(sorted(bucket["confidence_counts"].items())),
                    surface_forms=list(bucket["surface_forms"]),
                    example_texts=list(bucket["example_texts"]),
                    note_samples=list(bucket["note_samples"]),
                )
            )

    candidates.sort(
        key=lambda row: (-row.supporting_observations, row.proposed_decision, row.entity_key)
    )
    return candidates


def build_review_pack(
    candidates: list[AlphabeticPromotionCandidate],
    *,
    pack_id: str,
    review_stage: str = "alphabetic_candidate_review",
) -> dict:
    created_at_epoch = int(time())
    items = []
    for seq, candidate in enumerate(candidates, start=1):
        items.append(
            {
                "item_id": f"entity:{candidate.entity_key}",
                "seq": seq,
                "entity_key": candidate.entity_key,
                "strict_case": candidate.strict_case,
                "proposed_action": candidate.proposed_decision,
                "surface_forms": candidate.surface_forms,
                "example_texts": candidate.example_texts,
                "note_samples": candidate.note_samples,
                "evidence": {
                    "threshold_observations": candidate.threshold_observations,
                    "supporting_observations": candidate.supporting_observations,
                    "opposing_observations": candidate.opposing_observations,
                    "supporting_batch_count": candidate.supporting_batch_count,
                    "opposing_batch_count": candidate.opposing_batch_count,
                    "source_batches": candidate.source_batches,
                    "confidence_counts": candidate.confidence_counts,
                },
            }
        )

    return {
        "schema_version": 1,
        "review_stage": review_stage,
        "pack_id": pack_id,
        "created_at_epoch": created_at_epoch,
        "item_count": len(items),
        "items": items,
    }


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: str | Path, payload: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def find_review_pack(review_pack_root: str | Path, pack_id: str) -> Path:
    root = Path(review_pack_root)
    for path in sorted(root.rglob("*.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            continue
        if str(payload.get("pack_id")) == pack_id:
            return path
    raise FileNotFoundError(f"Review pack not found for pack_id={pack_id}")


def store_review_submission(
    submission: dict,
    *,
    submission_store_dir: str | Path,
) -> Path:
    store_dir = Path(submission_store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    submission_id = sanitize_submission_id(str(submission["submission_id"]))
    output_path = store_dir / f"{submission_id}.json"
    output_path.write_text(json.dumps(submission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def load_review_submissions(submission_store_dir: str | Path, *, review_stage: str, pack_id: str) -> list[dict]:
    store_dir = Path(submission_store_dir)
    rows: list[dict] = []
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


def replay_review_submissions(pack: dict, submissions: list[dict]) -> dict[str, dict]:
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    items_by_seq = {int(item["seq"]): item for item in pack.get("items", [])}
    effective: dict[str, dict] = {}

    for submission in submissions:
        ranges = submission.get("reviewed_ranges", [])
        overrides = {
            str(row["item_id"]): row
            for row in submission.get("overrides", [])
            if str(row.get("item_id", "")) in items_by_id
        }
        for reviewed_range in ranges:
            from_seq = int(reviewed_range["from_seq"])
            to_seq = int(reviewed_range["to_seq"])
            if from_seq > to_seq:
                from_seq, to_seq = to_seq, from_seq
            for seq in range(from_seq, to_seq + 1):
                item = items_by_seq.get(seq)
                if not item:
                    continue
                item_id = str(item["item_id"])
                effective[item_id] = {
                    "item_id": item_id,
                    "status": "accept",
                    "submission_id": str(submission.get("submission_id", "")),
                    "generated_at_epoch": int(submission.get("generated_at_epoch", 0)),
                }
            for item_id, override in overrides.items():
                item = items_by_id[item_id]
                item_seq = int(item["seq"])
                if item_seq < from_seq or item_seq > to_seq:
                    continue
                effective[item_id] = {
                    "item_id": item_id,
                    "status": str(override.get("decision", "")),
                    "note": str(override.get("note", "")).strip(),
                    "submission_id": str(submission.get("submission_id", "")),
                    "generated_at_epoch": int(submission.get("generated_at_epoch", 0)),
                }
    return effective


def build_review_promoted_decisions(pack: dict, effective_item_states: dict[str, dict]) -> list[AlphabeticDecision]:
    decisions: list[AlphabeticDecision] = []
    for item in pack.get("items", []):
        item_id = str(item["item_id"])
        item_state = effective_item_states.get(item_id)
        if not item_state or item_state.get("status") != "accept":
            continue
        proposed_action = str(item.get("proposed_action", ""))
        if proposed_action not in {"whitelist", "blacklist"}:
            continue
        decisions.append(
            AlphabeticDecision(
                entity_key=str(item["entity_key"]),
                strict_case=bool(item.get("strict_case", False)),
                status=proposed_action,
                source="review:alphabetic_candidate_review",
                note=f"pack={pack['pack_id']};submission={item_state.get('submission_id', '')}",
            )
        )
    decisions.sort(key=lambda row: (row.status, row.entity_key))
    return decisions


def rewrite_alphabetic_decisions_with_review_promotions(
    path: str | Path,
    review_promoted_decisions: list[AlphabeticDecision],
) -> None:
    existing = load_alphabetic_decisions(path)
    preserved = {
        entity_key: decision
        for entity_key, decision in existing.items()
        if not decision.source.startswith("review:")
    }
    for decision in review_promoted_decisions:
        preserved[decision.entity_key] = decision

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for entity_key in sorted(preserved):
            handle.write(json.dumps(asdict(preserved[entity_key]), ensure_ascii=False) + "\n")


def build_review_import_summary(
    submission: dict,
    *,
    stored_path: str,
    pack: dict,
    effective_item_states: dict[str, dict],
    promoted_decisions: list[AlphabeticDecision],
) -> dict:
    accepted_count = sum(1 for row in effective_item_states.values() if row.get("status") == "accept")
    rejected_count = sum(1 for row in effective_item_states.values() if row.get("status") == "reject")
    deferred_count = sum(1 for row in effective_item_states.values() if row.get("status") == "defer")
    return {
        "submission_id": str(submission["submission_id"]),
        "pack_id": str(pack["pack_id"]),
        "review_stage": str(pack["review_stage"]),
        "stored_path": stored_path,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "deferred_count": deferred_count,
        "promoted_whitelist_count": sum(1 for row in promoted_decisions if row.status == "whitelist"),
        "promoted_blacklist_count": sum(1 for row in promoted_decisions if row.status == "blacklist"),
        "promoted_entities": [
            {
                "entity_key": row.entity_key,
                "status": row.status,
                "strict_case": row.strict_case,
            }
            for row in promoted_decisions
        ],
    }


def sanitize_submission_id(submission_id: str) -> str:
    keep = []
    for char in submission_id:
        if char.isalnum() or char in {"_", "-", "."}:
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep)
