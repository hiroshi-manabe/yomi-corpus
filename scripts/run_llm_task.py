#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import json

from yomi_corpus.llm.batch_jobs import (
    fetch_batch_job,
    list_batch_jobs,
    poll_batch_job,
    prepare_batch_job,
    submit_batch_job,
)
from yomi_corpus.llm.runner import prepare_batch_task, run_sync_task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or manage one project-local LLM task.")
    parser.add_argument(
        "--task-config",
        help="Task config TOML path relative to repo root.",
    )
    parser.add_argument(
        "--input-jsonl",
        help="Input JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--mode",
        choices=[
            "sync",
            "batch_prepare",
            "prepare_batch_job",
            "submit_batch_job",
            "poll_batch_job",
            "fetch_batch_job",
            "list_batch_jobs",
        ],
        required=True,
        help="Execution mode.",
    )
    parser.add_argument(
        "--output-jsonl",
        help="Output JSONL path for sync mode.",
    )
    parser.add_argument(
        "--requests-jsonl",
        help="Batch request JSONL path for batch_prepare mode.",
    )
    parser.add_argument(
        "--manifest-json",
        help="Manifest JSON path for batch_prepare mode.",
    )
    parser.add_argument(
        "--job-dir",
        help="Job directory for prepare/submit/poll/fetch modes.",
    )
    parser.add_argument(
        "--jobs-root",
        default="runs/llm",
        help="Root directory for list_batch_jobs mode.",
    )
    parser.add_argument(
        "--api-key-file",
        help=(
            "Optional OpenAI API key file. If omitted, the runner uses OPENAI_API_KEY, "
            "then OPENAI_API_KEY_FILE, then ~/.config/api_keys/openai/default.txt."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "sync":
        if not args.task_config or not args.input_jsonl or not args.output_jsonl:
            raise SystemExit("--task-config, --input-jsonl, and --output-jsonl are required for sync mode.")
        run_sync_task(
            args.task_config,
            args.input_jsonl,
            args.output_jsonl,
            api_key_file=args.api_key_file,
        )
        return

    if args.mode == "prepare_batch_job":
        if not args.task_config or not args.input_jsonl or not args.job_dir:
            raise SystemExit("--task-config, --input-jsonl, and --job-dir are required for prepare_batch_job.")
        prepare_batch_job(args.task_config, args.input_jsonl, args.job_dir)
        return

    if args.mode == "submit_batch_job":
        if not args.job_dir:
            raise SystemExit("--job-dir is required for submit_batch_job.")
        submit_batch_job(args.job_dir, api_key_file=args.api_key_file)
        return

    if args.mode == "poll_batch_job":
        if not args.job_dir:
            raise SystemExit("--job-dir is required for poll_batch_job.")
        poll_batch_job(args.job_dir, api_key_file=args.api_key_file)
        return

    if args.mode == "fetch_batch_job":
        if not args.job_dir:
            raise SystemExit("--job-dir is required for fetch_batch_job.")
        fetch_batch_job(args.job_dir, api_key_file=args.api_key_file)
        return

    if args.mode == "list_batch_jobs":
        for row in list_batch_jobs(args.jobs_root):
            print(json.dumps(row, ensure_ascii=False))
        return

    if not args.task_config or not args.input_jsonl or not args.requests_jsonl or not args.manifest_json:
        raise SystemExit(
            "--task-config, --input-jsonl, --requests-jsonl, and --manifest-json are required for batch_prepare mode."
        )
    prepare_batch_task(
        args.task_config,
        args.input_jsonl,
        args.requests_jsonl,
        args.manifest_json,
    )


if __name__ == "__main__":
    main()
