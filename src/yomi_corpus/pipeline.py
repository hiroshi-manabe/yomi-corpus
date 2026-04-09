from __future__ import annotations

import gzip
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import tomllib

from yomi_corpus.alphabetic import (
    apply_global_decisions,
    aggregate_occurrences,
    attach_examples_to_types,
    build_occurrences_for_unit,
    load_alphabetic_config,
    project_minor_alphabetic_judgment,
)
from yomi_corpus.alphabetic_reports import build_unresolved_entity_rows
from yomi_corpus.alphabetic_state import (
    AlphabeticEvidence,
    append_alphabetic_evidence,
    load_alphabetic_decisions,
)
from yomi_corpus.models import UnitRecord, empty_analysis
from yomi_corpus.splitter import split_text_into_units
from yomi_corpus.yomi.export import export_named_variant


TRACKS: dict[str, dict[str, str]] = {
    "working": {
        "batch_prefix": "batch_",
        "batch_kind": "working",
        "pipeline_profile": "working",
    },
    "dev": {
        "batch_prefix": "dev_batch_",
        "batch_kind": "dev",
        "pipeline_profile": "dev",
    },
}

STAGE_SEQUENCE = [
    "prepared",
    "alphabetic_analyzed",
    "alphabetic_reported",
    "yomi_generated",
]


@dataclass
class TrackState:
    track_name: str
    current_batch_name: str | None
    updated_at: str


@dataclass
class BatchState:
    batch_name: str
    track_name: str
    batch_kind: str
    pipeline_profile: str
    dataset_name: str
    dataset_config_path: str
    dataset_source_path: str
    target_documents: int
    docs_written: int
    units_written: int
    current_stage: str
    blocking_reason: str | None
    artifacts: dict[str, str]
    updated_at: str


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_track_name(name: str | None) -> str:
    track_name = name or "working"
    if track_name not in TRACKS:
        raise ValueError(f"Unknown track: {track_name}")
    return track_name


class PipelineWorkspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def units_root(self) -> Path:
        return self.root / "data" / "units"

    def pipeline_root(self) -> Path:
        return self.root / "data" / "pipeline"

    def tracks_root(self) -> Path:
        return self.pipeline_root() / "tracks"

    def batches_root(self) -> Path:
        return self.pipeline_root() / "batches"

    def track_state_path(self, track_name: str) -> Path:
        return self.tracks_root() / f"{track_name}.json"

    def batch_state_path(self, batch_name: str) -> Path:
        return self.batches_root() / f"{batch_name}.json"

    def batch_dir(self, batch_name: str) -> Path:
        return self.units_root() / batch_name

    def manifest_path(self, batch_name: str) -> Path:
        return self.batch_dir(batch_name) / "manifest.json"

    def ensure_dirs(self) -> None:
        self.tracks_root().mkdir(parents=True, exist_ok=True)
        self.batches_root().mkdir(parents=True, exist_ok=True)

    def load_track_state(self, track_name: str) -> TrackState:
        normalized = normalize_track_name(track_name)
        path = self.track_state_path(normalized)
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            return TrackState(
                track_name=str(payload["track_name"]),
                current_batch_name=payload.get("current_batch_name"),
                updated_at=str(payload["updated_at"]),
            )

        inferred_batch_name = self._infer_latest_batch_name_for_track(normalized)
        state = TrackState(
            track_name=normalized,
            current_batch_name=inferred_batch_name,
            updated_at=now_iso(),
        )
        self.save_track_state(state)
        return state

    def save_track_state(self, state: TrackState) -> None:
        self.ensure_dirs()
        self.track_state_path(state.track_name).write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_batch_state(self, batch_name: str) -> BatchState:
        path = self.batch_state_path(batch_name)
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            return BatchState(**payload)
        return self._infer_batch_state(batch_name)

    def save_batch_state(self, state: BatchState) -> None:
        self.ensure_dirs()
        self.batch_state_path(state.batch_name).write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def status(self, track_name: str | None = None) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        track_state = self.load_track_state(normalized)
        if not track_state.current_batch_name:
            return {
                "track_name": normalized,
                "current_batch_name": None,
                "message": "No current batch is set for this track.",
            }
        batch_state = self.load_batch_state(track_state.current_batch_name)
        return {
            "track_name": normalized,
            "current_batch_name": batch_state.batch_name,
            "current_stage": batch_state.current_stage,
            "blocking_reason": batch_state.blocking_reason,
            "artifacts": batch_state.artifacts,
            "target_documents": batch_state.target_documents,
            "docs_written": batch_state.docs_written,
            "units_written": batch_state.units_written,
            "next_stage": self._next_stage_name(batch_state.current_stage),
            "updated_at": batch_state.updated_at,
        }

    def prepare_next_batch(
        self,
        *,
        track_name: str | None,
        target_documents: int,
        dataset_config_path: str = "config/datasets/ja_cc_level2.toml",
    ) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        batch_name = self._allocate_next_batch_name(normalized)
        dataset = self._load_dataset_config(dataset_config_path)

        docs_written, units_written = self._extract_batch_documents(
            source_path=dataset["source_path"],
            dataset_name=dataset["name"],
            target_documents=target_documents,
            batch_name=batch_name,
        )

        manifest_payload = {
            "batch_name": batch_name,
            "track_name": normalized,
            "batch_kind": TRACKS[normalized]["batch_kind"],
            "pipeline_profile": TRACKS[normalized]["pipeline_profile"],
            "dataset_name": dataset["name"],
            "dataset_config_path": dataset_config_path,
            "dataset_source_path": str(dataset["source_path"]),
            "target_documents": target_documents,
            "docs_written": docs_written,
            "units_written": units_written,
            "unit_schema_version": 1,
            "mechanical_analysis_initialized": True,
        }
        self.batch_dir(batch_name).mkdir(parents=True, exist_ok=True)
        self.manifest_path(batch_name).write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        batch_state = BatchState(
            batch_name=batch_name,
            track_name=normalized,
            batch_kind=TRACKS[normalized]["batch_kind"],
            pipeline_profile=TRACKS[normalized]["pipeline_profile"],
            dataset_name=dataset["name"],
            dataset_config_path=dataset_config_path,
            dataset_source_path=str(dataset["source_path"]),
            target_documents=target_documents,
            docs_written=docs_written,
            units_written=units_written,
            current_stage="prepared",
            blocking_reason=None,
            artifacts={
                "units_jsonl": str(self.batch_dir(batch_name) / "units.jsonl"),
                "manifest": str(self.manifest_path(batch_name)),
            },
            updated_at=now_iso(),
        )
        self.save_batch_state(batch_state)
        self.save_track_state(
            TrackState(
                track_name=normalized,
                current_batch_name=batch_name,
                updated_at=now_iso(),
            )
        )
        return {
            "track_name": normalized,
            "batch_name": batch_name,
            "target_documents": target_documents,
            "docs_written": docs_written,
            "units_written": units_written,
            "current_stage": "prepared",
        }

    def advance(self, track_name: str | None = None) -> dict[str, object]:
        normalized = normalize_track_name(track_name)
        track_state = self.load_track_state(normalized)
        if not track_state.current_batch_name:
            return {
                "track_name": normalized,
                "advanced": False,
                "message": "No current batch is set for this track. Run prepare first.",
            }

        batch_state = self.load_batch_state(track_state.current_batch_name)
        current_stage = batch_state.current_stage

        if current_stage == "prepared":
            summary = self._run_alphabetic_analysis(batch_state.batch_name)
            batch_state.current_stage = "alphabetic_analyzed"
            batch_state.blocking_reason = None
            batch_state.artifacts.update(summary["artifacts"])
        elif current_stage == "alphabetic_analyzed":
            summary = self._build_unresolved_alphabetic_report(batch_state.batch_name)
            batch_state.current_stage = "alphabetic_reported"
            batch_state.blocking_reason = None
            batch_state.artifacts.update(summary["artifacts"])
        elif current_stage == "alphabetic_reported":
            summary = self._generate_mechanical_yomi(batch_state.batch_name)
            batch_state.current_stage = "yomi_generated"
            batch_state.blocking_reason = (
                "No later automated stage is implemented yet after mechanical yomi generation."
            )
            batch_state.artifacts.update(summary["artifacts"])
        else:
            return {
                "track_name": normalized,
                "batch_name": batch_state.batch_name,
                "advanced": False,
                "current_stage": batch_state.current_stage,
                "blocking_reason": batch_state.blocking_reason
                or "No automated next stage is implemented for this batch.",
            }

        batch_state.updated_at = now_iso()
        self.save_batch_state(batch_state)
        return {
            "track_name": normalized,
            "batch_name": batch_state.batch_name,
            "advanced": True,
            "current_stage": batch_state.current_stage,
            "blocking_reason": batch_state.blocking_reason,
            "artifacts": batch_state.artifacts,
        }

    def _next_stage_name(self, current_stage: str) -> str | None:
        try:
            index = STAGE_SEQUENCE.index(current_stage)
        except ValueError:
            return None
        next_index = index + 1
        if next_index >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[next_index]

    def _allocate_next_batch_name(self, track_name: str) -> str:
        prefix = TRACKS[track_name]["batch_prefix"]
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_value = 0
        if self.units_root().exists():
            for path in self.units_root().iterdir():
                match = pattern.match(path.name)
                if match:
                    max_value = max(max_value, int(match.group(1)))
        return f"{prefix}{max_value + 1:04d}"

    def _infer_latest_batch_name_for_track(self, track_name: str) -> str | None:
        prefix = TRACKS[track_name]["batch_prefix"]
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        candidates: list[tuple[int, str]] = []
        if self.units_root().exists():
            for path in self.units_root().iterdir():
                match = pattern.match(path.name)
                if match:
                    candidates.append((int(match.group(1)), path.name))
        if not candidates:
            return None
        candidates.sort()
        return candidates[-1][1]

    def _infer_batch_state(self, batch_name: str) -> BatchState:
        manifest_path = self.manifest_path(batch_name)
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest found for batch {batch_name}")
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        track_name = str(manifest.get("track_name") or ("dev" if batch_name.startswith("dev_batch_") else "working"))
        current_stage = self._infer_stage_from_artifacts(batch_name)
        blocking_reason = (
            "No later automated stage is implemented yet after mechanical yomi generation."
            if current_stage == "yomi_generated"
            else None
        )
        artifacts = {
            "units_jsonl": str(self.batch_dir(batch_name) / "units.jsonl"),
            "manifest": str(manifest_path),
        }
        if current_stage in {"alphabetic_analyzed", "alphabetic_reported", "yomi_generated"}:
            artifacts.update(
                {
                    "units_alphabetic_jsonl": str(self.batch_dir(batch_name) / "units.alphabetic.jsonl"),
                    "alphabetic_occurrences_jsonl": str(self.batch_dir(batch_name) / "alphabetic_occurrences.jsonl"),
                    "alphabetic_types_jsonl": str(self.batch_dir(batch_name) / "alphabetic_types.jsonl"),
                }
            )
        if current_stage in {"alphabetic_reported", "yomi_generated"}:
            artifacts.update(
                {
                    "alphabetic_unresolved_jsonl": str(
                        self.batch_dir(batch_name) / "alphabetic_unresolved_entities.jsonl"
                    ),
                    "alphabetic_unresolved_tsv": str(
                        self.batch_dir(batch_name) / "alphabetic_unresolved_entities.tsv"
                    ),
                }
            )
        if current_stage == "yomi_generated":
            artifacts["units_yomi_jsonl"] = str(
                self.batch_dir(batch_name) / "units.yomi.aligned_hybrid.jsonl"
            )
        state = BatchState(
            batch_name=batch_name,
            track_name=track_name,
            batch_kind=str(manifest.get("batch_kind") or TRACKS[track_name]["batch_kind"]),
            pipeline_profile=str(manifest.get("pipeline_profile") or TRACKS[track_name]["pipeline_profile"]),
            dataset_name=str(manifest["dataset_name"]),
            dataset_config_path=str(manifest.get("dataset_config_path", "config/datasets/ja_cc_level2.toml")),
            dataset_source_path=str(manifest["dataset_source_path"]),
            target_documents=int(manifest["target_documents"]),
            docs_written=int(manifest["docs_written"]),
            units_written=int(manifest["units_written"]),
            current_stage=current_stage,
            blocking_reason=blocking_reason,
            artifacts=artifacts,
            updated_at=now_iso(),
        )
        self.save_batch_state(state)
        return state

    def _infer_stage_from_artifacts(self, batch_name: str) -> str:
        batch_dir = self.batch_dir(batch_name)
        if (batch_dir / "units.yomi.aligned_hybrid.jsonl").exists():
            return "yomi_generated"
        if (batch_dir / "alphabetic_unresolved_entities.jsonl").exists():
            return "alphabetic_reported"
        if (batch_dir / "units.alphabetic.jsonl").exists():
            return "alphabetic_analyzed"
        return "prepared"

    def _load_dataset_config(self, path_str: str) -> dict[str, object]:
        path = self.root / path_str
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
        source_path = Path(str(payload["source_path"]))
        if not source_path.is_absolute():
            source_path = (self.root / source_path).resolve()
        return {
            "name": str(payload["name"]),
            "source_path": source_path,
        }

    def _extract_batch_documents(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        target_documents: int,
        batch_name: str,
    ) -> tuple[int, int]:
        output_dir = self.batch_dir(batch_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        units_path = output_dir / "units.jsonl"

        units_written = 0
        docs_written = 0
        with gzip.open(source_path, "rt", encoding="utf-8") as handle, units_path.open(
            "w", encoding="utf-8"
        ) as out:
            for source_line_no, line in enumerate(handle, start=1):
                payload = json.loads(line)
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                docs_written += 1
                doc_id = f"{dataset_name}:{source_line_no:010d}"
                source_file = str(payload.get("source_file", ""))
                spans = split_text_into_units(text)
                for unit_seq, span in enumerate(spans, start=1):
                    units_written += 1
                    unit = UnitRecord(
                        doc_id=doc_id,
                        unit_id=f"{doc_id}:u{unit_seq:04d}",
                        unit_seq=unit_seq,
                        char_start=span.start,
                        char_end=span.end,
                        text=span.text,
                        source_file=source_file,
                        source_line_no=source_line_no,
                        analysis=empty_analysis(),
                    )
                    out.write(json.dumps(unit.to_dict(), ensure_ascii=False) + "\n")
                if docs_written >= target_documents:
                    break
        return docs_written, units_written

    def _run_alphabetic_analysis(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        config = load_alphabetic_config("config/alphabetic/default.toml")
        input_path = batch_dir / "units.jsonl"
        output_units_path = batch_dir / "units.alphabetic.jsonl"
        output_occurrences_path = batch_dir / "alphabetic_occurrences.jsonl"
        output_types_path = batch_dir / "alphabetic_types.jsonl"
        global_decisions_path = self.root / "data" / "state" / "alphabetic" / "token_decisions.jsonl"
        global_evidence_path = self.root / "data" / "state" / "alphabetic" / "token_evidence.jsonl"
        output_units_path.parent.mkdir(parents=True, exist_ok=True)

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
            for entity_type in types:
                dst.write(json.dumps(asdict(entity_type), ensure_ascii=False) + "\n")

        append_alphabetic_evidence(
            global_evidence_path,
            [
                AlphabeticEvidence(
                    batch_name=batch_name,
                    entity_key=entity_type.entity_key,
                    strict_case=entity_type.strict_case,
                    resolved_status=entity_type.resolved_status,
                    base_list_status=entity_type.base_list_status,
                    occurrence_count=entity_type.occurrence_count,
                    unit_count=entity_type.unit_count,
                    surface_forms=entity_type.surface_forms,
                    example_unit_ids=entity_type.example_unit_ids,
                )
                for entity_type in types
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

        return {
            "artifacts": {
                "units_alphabetic_jsonl": str(output_units_path),
                "alphabetic_occurrences_jsonl": str(output_occurrences_path),
                "alphabetic_types_jsonl": str(output_types_path),
            }
        }

    def _build_unresolved_alphabetic_report(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        input_types_path = batch_dir / "alphabetic_types.jsonl"
        output_jsonl_path = batch_dir / "alphabetic_unresolved_entities.jsonl"
        output_tsv_path = batch_dir / "alphabetic_unresolved_entities.tsv"

        rows: list[dict] = []
        with input_types_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))

        unresolved = build_unresolved_entity_rows(
            rows,
            min_occurrences=1,
            max_examples=3,
            max_example_chars=160,
        )

        with output_jsonl_path.open("w", encoding="utf-8") as handle:
            for row in unresolved:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        with output_tsv_path.open("w", encoding="utf-8") as handle:
            header = [
                "entity_key",
                "strict_case",
                "resolved_status",
                "base_list_status",
                "occurrence_count",
                "unit_count",
                "surface_forms",
                "example_unit_ids",
                "example_texts",
            ]
            handle.write("\t".join(header) + "\n")
            for row in unresolved:
                handle.write(
                    "\t".join(
                        [
                            row["entity_key"],
                            str(row["strict_case"]),
                            row["resolved_status"],
                            row["base_list_status"],
                            str(row["occurrence_count"]),
                            str(row["unit_count"]),
                            " | ".join(row["surface_forms"]),
                            " | ".join(row["example_unit_ids"]),
                            " || ".join(
                                text.replace("\t", " ").replace("\n", " ") for text in row["example_texts"]
                            ),
                        ]
                    )
                    + "\n"
                )
        return {
            "artifacts": {
                "alphabetic_unresolved_jsonl": str(output_jsonl_path),
                "alphabetic_unresolved_tsv": str(output_tsv_path),
            }
        }

    def _generate_mechanical_yomi(self, batch_name: str) -> dict[str, object]:
        batch_dir = self.batch_dir(batch_name)
        summary = export_named_variant(
            variant_name="aligned_hybrid",
            batch_dir=batch_dir,
            config_path="config/yomi/default.toml",
            formats=["jsonl"],
            show_progress=True,
        )
        return {
            "artifacts": {
                "units_yomi_jsonl": str(batch_dir / "units.yomi.aligned_hybrid.jsonl"),
                "yomi_variant": str(summary["variant_name"]),
            }
        }
