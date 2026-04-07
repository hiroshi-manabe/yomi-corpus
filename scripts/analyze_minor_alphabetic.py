#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic import (
    apply_global_decisions,
    aggregate_occurrences,
    attach_examples_to_types,
    build_occurrences_for_unit,
    load_alphabetic_config,
    project_minor_alphabetic_judgment,
)
from yomi_corpus.alphabetic_state import (
    AlphabeticEvidence,
    append_alphabetic_evidence,
    load_alphabetic_decisions,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build batch-level Latin/alphanumeric entity artifacts and project the result back to units."
    )
    parser.add_argument(
        "--input",
        default="data/units/batch_0001/units.jsonl",
        help="Input units JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--output-units",
        default="data/units/batch_0001/units.alphabetic.jsonl",
        help="Projected unit JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--output-occurrences",
        default="data/units/batch_0001/alphabetic_occurrences.jsonl",
        help="Alphabetic occurrence JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--output-types",
        default="data/units/batch_0001/alphabetic_types.jsonl",
        help="Alphabetic entity-type JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--config",
        default="config/alphabetic/default.toml",
        help="Alphabetic matching config path relative to repo root.",
    )
    parser.add_argument(
        "--global-decisions",
        default="data/state/alphabetic/token_decisions.jsonl",
        help="Global alphabetic decision registry path relative to repo root.",
    )
    parser.add_argument(
        "--global-evidence",
        default="data/state/alphabetic/token_evidence.jsonl",
        help="Append-only cross-batch evidence path relative to repo root.",
    )
    parser.add_argument(
        "--batch-name",
        default="batch_0001",
        help="Batch name recorded in evidence rows.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_alphabetic_config(args.config)
    input_path = PROJECT_ROOT / args.input
    output_units_path = PROJECT_ROOT / args.output_units
    output_occurrences_path = PROJECT_ROOT / args.output_occurrences
    output_types_path = PROJECT_ROOT / args.output_types
    global_decisions_path = PROJECT_ROOT / args.global_decisions
    global_evidence_path = PROJECT_ROOT / args.global_evidence

    output_units_path.parent.mkdir(parents=True, exist_ok=True)
    output_occurrences_path.parent.mkdir(parents=True, exist_ok=True)
    output_types_path.parent.mkdir(parents=True, exist_ok=True)

    units: list[dict] = []
    occurrences_by_unit: dict[str, list] = {}
    all_occurrences = []
    unit_text_by_id: dict[str, str] = {}
    decision_status_by_key = {
        entity_key: decision.status
        for entity_key, decision in load_alphabetic_decisions(global_decisions_path).items()
    }

    with input_path.open(encoding="utf-8") as src:
        for line in src:
            unit = json.loads(line)
            units.append(unit)
            unit_text_by_id[str(unit["unit_id"])] = str(unit["text"])
            occurrences = apply_global_decisions(
                build_occurrences_for_unit(unit, config),
                decision_status_by_key,
            )
            occurrences_by_unit[str(unit["unit_id"])] = occurrences
            all_occurrences.extend(occurrences)

    types = attach_examples_to_types(
        aggregate_occurrences(all_occurrences),
        unit_text_by_id,
    )

    with output_occurrences_path.open("w", encoding="utf-8") as dst:
        for occurrence in all_occurrences:
            dst.write(json.dumps(asdict(occurrence), ensure_ascii=False) + "\n")

    with output_types_path.open("w", encoding="utf-8") as dst:
        for token_type in types:
            dst.write(json.dumps(asdict(token_type), ensure_ascii=False) + "\n")

    append_alphabetic_evidence(
        global_evidence_path,
        [
            AlphabeticEvidence(
                batch_name=args.batch_name,
                entity_key=token_type.entity_key,
                strict_case=token_type.strict_case,
                resolved_status=token_type.resolved_status,
                base_list_status=token_type.base_list_status,
                occurrence_count=token_type.occurrence_count,
                unit_count=token_type.unit_count,
                surface_forms=token_type.surface_forms,
                example_unit_ids=token_type.example_unit_ids,
            )
            for token_type in types
        ],
    )

    with output_units_path.open("w", encoding="utf-8") as dst:
        for unit in units:
            judgment = project_minor_alphabetic_judgment(occurrences_by_unit[str(unit["unit_id"])])
            unit["analysis"]["mechanical"]["minor_alphabetic_sequence"] = {
                "value": judgment.value,
                "certain": judgment.certain,
                "signals": judgment.signals,
                "matches": judgment.matches,
                "decision_granularity": "entity_type",
            }
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
