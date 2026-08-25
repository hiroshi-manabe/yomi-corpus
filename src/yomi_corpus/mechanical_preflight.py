from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import (
    STAGE_YOMI_READING_QUEUED,
    PipelineWorkspace,
    TrackState,
    llm_task_for_stage,
    now_iso,
)


MECHANICAL_PREFLIGHT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MechanicalPreflightOptions:
    track_name: str
    target_documents: int
    dataset_config_path: str = "config/datasets/ja_cc_level2.toml"
    keep_workspace: bool = False


def run_mechanical_preflight(
    root: Path,
    options: MechanicalPreflightOptions,
) -> dict[str, Any]:
    root = root.resolve()
    if options.target_documents <= 0:
        raise ValueError("target_documents must be positive")

    live_workspace = PipelineWorkspace(root)
    preview = live_workspace.preview_next_source_documents(
        track_name=options.track_name,
        target_documents=options.target_documents,
        dataset_config_path=options.dataset_config_path,
    )
    if int(preview.get("selected_document_count") or 0) == 0:
        raise ValueError("No future source documents are available for preflight")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    preflight_root = root / "data" / "preflight"
    workspace_parent = preflight_root / "workspaces"
    report_dir = preflight_root / "reports"
    workspace_parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=f"{options.track_name}_{run_id}_",
            dir=workspace_parent,
        )
    )
    report_path = report_dir / f"{options.track_name}_{run_id}.json"
    started = time.monotonic()
    report: dict[str, Any] = {
        "schema_version": MECHANICAL_PREFLIGHT_SCHEMA_VERSION,
        "run_id": run_id,
        "track_name": options.track_name,
        "target_documents": options.target_documents,
        "target_stage": STAGE_YOMI_READING_QUEUED,
        "llm_requests_sent": 0,
        "source_preview": preview,
        "workspace_path": str(temporary_root),
        "workspace_retained": options.keep_workspace,
        "started_at": now_iso(),
        "steps": [],
    }

    try:
        write_temporary_dataset_config(
            temporary_root=temporary_root,
            dataset_config_path=options.dataset_config_path,
            preview=preview,
        )
        workspace = PipelineWorkspace(temporary_root)
        live_track_state = live_workspace.load_track_state(options.track_name)
        workspace.save_track_state(
            TrackState(
                track_name=options.track_name,
                current_batch_name=None,
                decoder_model_dir=live_track_state.decoder_model_dir,
                updated_at=now_iso(),
            )
        )
        write_source_cursor_anchor(
            workspace=workspace,
            preview=preview,
            track_name=options.track_name,
        )
        prepared = workspace.prepare_next_batch(
            track_name=options.track_name,
            target_documents=options.target_documents,
            dataset_config_path=options.dataset_config_path,
        )
        batch_name = str(prepared["batch_name"])
        report["batch_name"] = batch_name
        report["prepared"] = prepared

        while True:
            state = workspace.load_batch_state(batch_name)
            if state.current_stage == STAGE_YOMI_READING_QUEUED:
                break
            next_stage = workspace._next_stage_name(state.current_stage)
            if next_stage is None:
                raise RuntimeError(
                    f"No stage follows {state.current_stage!r} before mechanical preflight target"
                )
            llm_task = llm_task_for_stage(next_stage)
            if llm_task is not None:
                raise RuntimeError(
                    f"Mechanical preflight refused LLM stage {next_stage!r} ({llm_task})"
                )
            result = workspace.advance_batch(batch_name)
            report["steps"].append(
                {
                    "attempted_stage": next_stage,
                    "advanced": bool(result.get("advanced")),
                    "current_stage": result.get("current_stage"),
                    "blocking_reason": result.get("blocking_reason"),
                }
            )
            if not result.get("advanced"):
                raise RuntimeError(
                    str(result.get("blocking_reason") or f"Stage {next_stage!r} did not advance")
                )

        final_state = workspace.load_batch_state(batch_name)
        queue_summary_path = workspace.batch_dir(batch_name) / "yomi_reading_queue_summary.json"
        report.update(
            {
                "status": "passed",
                "current_stage": final_state.current_stage,
                "units_written": prepared.get("units_written"),
                "source_start_line_no": read_manifest_value(
                    workspace.manifest_path(batch_name), "source_start_line_no"
                ),
                "source_end_line_no": read_manifest_value(
                    workspace.manifest_path(batch_name), "source_end_line_no"
                ),
                "queue_summary": compact_queue_summary(
                    read_json_if_present(queue_summary_path)
                ),
            }
        )
    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        report["completed_at"] = now_iso()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        if not options.keep_workspace:
            shutil.rmtree(temporary_root, ignore_errors=True)
            report["workspace_retained"] = False
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
    return report


def write_source_cursor_anchor(
    *,
    workspace: PipelineWorkspace,
    preview: dict[str, Any],
    track_name: str,
) -> None:
    anchor_dir = workspace.units_root() / "preflight_source_cursor"
    anchor_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "batch_name": "preflight_source_cursor",
        "track_name": track_name,
        "dataset_name": str(preview["dataset_name"]),
        "dataset_source_path": str(preview["dataset_source_path"]),
        "source_end_line_no": int(preview.get("skip_source_line_no") or 0),
    }
    (anchor_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_temporary_dataset_config(
    *,
    temporary_root: Path,
    dataset_config_path: str,
    preview: dict[str, Any],
) -> None:
    destination = temporary_root / dataset_config_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "name = "
        + json.dumps(str(preview["dataset_name"]), ensure_ascii=False)
        + "\nsource_path = "
        + json.dumps(str(preview["dataset_source_path"]), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def read_manifest_value(path: Path, key: str) -> Any:
    return json.loads(path.read_text(encoding="utf-8")).get(key)


def read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def compact_queue_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        key: value
        for key, value in summary.items()
        if not key.endswith("_jsonl")
        and not key.endswith("_json")
        and not key.endswith("_path")
    }
