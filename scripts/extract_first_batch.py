#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.datasets import load_dataset_config
from yomi_corpus.models import UnitRecord, empty_analysis
from yomi_corpus.paths import repo_root
from yomi_corpus.splitter import split_text_into_units


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract the first batch of unit records.")
    parser.add_argument(
        "--dataset-config",
        default="config/datasets/ja_cc_level2.toml",
        help="Dataset config path relative to repo root.",
    )
    parser.add_argument(
        "--target-documents",
        type=int,
        default=100,
        help="Stop after writing units for this many source documents.",
    )
    parser.add_argument(
        "--batch-name",
        default="batch_0001",
        help="Batch directory name under data/units.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = load_dataset_config(args.dataset_config)
    output_dir = repo_root() / "data" / "units" / args.batch_name
    output_dir.mkdir(parents=True, exist_ok=True)
    units_path = output_dir / "units.jsonl"
    manifest_path = output_dir / "manifest.json"

    units_written = 0
    docs_written = 0
    with gzip.open(dataset.source_path, "rt", encoding="utf-8") as handle, units_path.open(
        "w", encoding="utf-8"
    ) as out:
        for source_line_no, line in enumerate(handle, start=1):
            payload = json.loads(line)
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            docs_written += 1
            doc_id = f"{dataset.name}:{source_line_no:010d}"
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
            if docs_written >= args.target_documents:
                write_manifest(
                    manifest_path=manifest_path,
                    dataset_name=dataset.name,
                    dataset_source_path=str(dataset.source_path),
                    docs_written=docs_written,
                    units_written=units_written,
                    batch_name=args.batch_name,
                    target_documents=args.target_documents,
                )
                return

    write_manifest(
        manifest_path=manifest_path,
        dataset_name=dataset.name,
        dataset_source_path=str(dataset.source_path),
        docs_written=docs_written,
        units_written=units_written,
        batch_name=args.batch_name,
        target_documents=args.target_documents,
    )


def write_manifest(
    manifest_path: Path,
    dataset_name: str,
    dataset_source_path: str,
    docs_written: int,
    units_written: int,
    batch_name: str,
    target_documents: int,
) -> None:
    manifest = {
        "batch_name": batch_name,
        "dataset_name": dataset_name,
        "dataset_source_path": dataset_source_path,
        "target_documents": target_documents,
        "docs_written": docs_written,
        "units_written": units_written,
        "unit_schema_version": 1,
        "mechanical_analysis_initialized": True,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
