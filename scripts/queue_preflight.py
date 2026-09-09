#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomi_corpus.queue_preflight import run_queue_preflight

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check upcoming queue documents without LLM calls"
    )
    parser.add_argument("track", choices=["dev", "working"])
    parser.add_argument(
        "--documents",
        type=int,
        default=None,
        help="Optional per-run limit; otherwise keep running",
    )
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=300)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--check-lines", help=argparse.SUPPRESS)
    parser.add_argument("--result-path", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check_lines:
        from yomi_corpus.mechanical_preflight import (
            MechanicalPreflightOptions,
            run_mechanical_preflight,
        )

        lines = tuple(map(int, args.check_lines.split(",")))
        result = run_mechanical_preflight(
            ROOT,
            MechanicalPreflightOptions(
                track_name=args.track,
                target_documents=len(lines),
                source_line_nos=lines,
                keep_workspace=True,
            ),
        )
        Path(args.result_path).write_text(json.dumps(result, ensure_ascii=False) + "\n")
    else:
        run_queue_preflight(
            ROOT,
            args.track,
            documents=args.documents,
            chunk_size=args.chunk_size,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
            retry_failures=args.retry_failures,
        )
