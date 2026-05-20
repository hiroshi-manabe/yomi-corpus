#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Collapse OK/Review/Skip yomi eval labels to Keep/Skip.")
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--summary-json")
    args = parser.parse_args()

    input_path = Path(args.input_jsonl)
    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    written = 0
    with input_path.open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            original = str(row.get("expected_status", ""))
            if original == "Skip":
                expected = "Skip"
            elif original in {"OK", "Review", "Keep"}:
                expected = "Keep"
            else:
                raise ValueError(f"Unsupported expected_status {original!r} in {input_path}")
            row["expected_status"] = expected
            row["scope_triage_source_status"] = original
            row["scope_triage_source_file"] = str(input_path)
            counts[expected] = counts.get(expected, 0) + 1
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

    summary = {"input_jsonl": str(input_path), "output_jsonl": str(output_path), "written": written, "counts": counts}
    summary_path = Path(args.summary_json) if args.summary_json else output_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
