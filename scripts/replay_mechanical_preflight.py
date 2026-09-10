#!/usr/bin/env python3
"""Replay a failed preflight's source range without modifying live pipeline state."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.splitter import split_text_into_units
from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start-source-line", type=int, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.report.read_text())
    preview = original["source_preview"]
    selected = {int(row["source_line_no"]) for row in preview["selected_documents"]}
    if args.start_source_line not in selected:
        parser.error("start source line is not in the original preflight")
    selected = {line for line in selected if line >= args.start_source_line}
    source = Path(preview["dataset_source_path"])
    config = load_yomi_generation_config(ROOT / "config/yomi/default.toml")
    state = PipelineWorkspace(ROOT).load_track_state(original["track_name"])
    config = replace(config, decoder_model_dir=state.decoder_model_dir)
    progress = {
        "status": "running",
        "original_report": str(args.report.resolve()),
        "source_path": str(source),
        "source_start_line": min(selected),
        "source_end_line": max(selected),
        "target_documents": len(selected),
        "completed_documents": 0,
        "completed_units": 0,
        "llm_requests_sent": 0,
        "decoder_model_dir": state.decoder_model_dir,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    args.progress.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = args.progress.with_suffix(".tmp")
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.progress)

    save()
    try:
        with gzip.open(source, "rt", encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if line_no > max(selected):
                    break
                if line_no not in selected:
                    continue
                progress["current_source_line"] = line_no
                row = json.loads(line)
                for unit_no, span in enumerate(split_text_into_units(row["text"]), 1):
                    progress["current_unit"] = unit_no
                    progress["current_text"] = span.text
                    save()
                    yomi = generate_mechanical_yomi(span.text, config=config)
                    if "".join(surface for surface, _ in yomi.tokens) != span.text:
                        raise ValueError("mechanical token surfaces differ from source")
                    progress["completed_units"] += 1
                progress["completed_documents"] += 1
                progress["last_completed_source_line"] = line_no
                save()
                print(f"source {line_no}: {progress['completed_documents']}/{len(selected)} documents, "
                      f"{progress['completed_units']} units", flush=True)
        if progress["completed_documents"] != len(selected):
            raise ValueError("source ended before all selected documents were checked")
        progress["status"] = "passed"
    except BaseException as exc:
        progress.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
