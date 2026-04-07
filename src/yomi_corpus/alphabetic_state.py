from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class AlphabeticDecision:
    entity_key: str
    strict_case: bool
    status: str
    source: str
    note: str = ""


@dataclass(frozen=True)
class AlphabeticEvidence:
    batch_name: str
    entity_key: str
    strict_case: bool
    resolved_status: str
    base_list_status: str
    occurrence_count: int
    unit_count: int
    surface_forms: list[str]
    example_unit_ids: list[str]


def load_alphabetic_decisions(path: str | Path) -> dict[str, AlphabeticDecision]:
    decisions_path = Path(path)
    if not decisions_path.exists():
        return {}

    decisions: dict[str, AlphabeticDecision] = {}
    with decisions_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            entity_key = payload.get("entity_key")
            if entity_key is None:
                entity_key = payload["token_key"]
            decision = AlphabeticDecision(
                entity_key=str(entity_key),
                strict_case=bool(payload["strict_case"]),
                status=str(payload["status"]),
                source=str(payload.get("source", "unknown")),
                note=str(payload.get("note", "")),
            )
            decisions[decision.entity_key] = decision
    return decisions


def append_alphabetic_evidence(path: str | Path, records: list[AlphabeticEvidence]) -> None:
    evidence_path = Path(path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    kept_payloads: list[dict] = []
    incoming_batch_names = {record.batch_name for record in records}
    if evidence_path.exists():
        with evidence_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload["batch_name"]) in incoming_batch_names:
                    continue
                kept_payloads.append(payload)
    with evidence_path.open("w", encoding="utf-8") as handle:
        for payload in kept_payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def upsert_alphabetic_decision(path: str | Path, decision: AlphabeticDecision) -> None:
    decisions = load_alphabetic_decisions(path)
    decisions[decision.entity_key] = decision
    decisions_path = Path(path)
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    with decisions_path.open("w", encoding="utf-8") as handle:
        for entity_key in sorted(decisions):
            handle.write(json.dumps(asdict(decisions[entity_key]), ensure_ascii=False) + "\n")
