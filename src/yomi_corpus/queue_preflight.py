"""Checkpointed, non-LLM preflight in upcoming processing-order priority."""

from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import os
import signal
import subprocess
import sys
import time

from yomi_corpus.pipeline import PipelineWorkspace, now_iso


def implementation_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for directory in (root / "src", root / "config"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".toml", ".txt", ".tsv"}:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def run_queue_preflight(
    root: Path,
    track: str,
    *,
    documents: int | None = None,
    chunk_size: int = 10,
    poll_seconds: float = 300,
    timeout_seconds: float = 3600,
    retry_failures: bool = False,
) -> None:
    if (
        (documents is not None and documents <= 0)
        or chunk_size <= 0
        or poll_seconds <= 0
        or timeout_seconds <= 0
    ):
        raise ValueError("documents and chunk_size must be positive")
    state_dir = root / "data/preflight/queue" / track
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def stop(_signum, _frame):
            raise KeyboardInterrupt("Preflight stopped")

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            _run(
                root,
                track,
                documents,
                chunk_size,
                state_dir,
                poll_seconds,
                timeout_seconds,
                retry_failures,
            )
        finally:
            signal.signal(signal.SIGTERM, previous)


def run_check(
    root: Path, track: str, lines: list[int], state_dir: Path, timeout: float
) -> dict:
    result_path = state_dir / "child_result.json"
    result_path.unlink(missing_ok=True)
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(root / "scripts/queue_preflight.py"),
            track,
            "--check-lines",
            ",".join(map(str, lines)),
            "--result-path",
            str(result_path),
        ],
        cwd=root,
        start_new_session=True,
    )
    try:
        returncode = process.wait(timeout=timeout)
    except BaseException:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    if returncode or not result_path.exists():
        raise RuntimeError(f"Preflight subprocess failed with exit code {returncode}")
    return json.loads(result_path.read_text())


def stable_checks(records: dict) -> dict:
    """Reuse old successful checks without treating routine model updates as invalidation."""
    return {
        f"{row['source_sha256']}:{row['source_line']}": row
        for row in records.values()
        if row.get("source_sha256") and row.get("source_line")
    }


def next_unchecked(
    store, manifest: dict, completed: dict, failures: dict, count: int
) -> list[int]:
    identity = manifest["source_content_sha256"]
    selected = []
    for start in range(
        int(manifest["cursor"]), int(manifest["document_count"]) + 1, 10000
    ):
        lines = store.read_slots(
            start, min(10000, int(manifest["document_count"]) - start + 1)
        )
        selected.extend(
            line
            for line in lines
            if f"{identity}:{line}" not in completed
            and f"{identity}:{line}" not in failures
        )
        if len(selected) >= count:
            break
    return selected[:count]


def check_with_isolation(lines: list[int], check, record) -> None:
    result = check(lines)
    if result["status"] == "passed":
        record(lines, result)
    elif result.get("error_type") not in {
        "YomiTokenError",
        "ValueError",
        "AssertionError",
    }:
        raise RuntimeError(
            f"Preflight infrastructure/unknown failure: {result.get('error')}; {result.get('report_path')}"
        )
    elif len(lines) > 1:
        for line in lines:
            check_with_isolation([line], check, record)
    else:
        record(lines, result)


def _run(
    root: Path,
    track: str,
    documents: int | None,
    chunk_size: int,
    state_dir: Path,
    poll_seconds: float,
    timeout_seconds: float,
    retry_failures: bool,
) -> None:
    checkpoint = state_dir / "completed.json"
    completed = (
        stable_checks(json.loads(checkpoint.read_text())) if checkpoint.exists() else {}
    )
    failure_path = state_dir / "failures.json"
    failures = json.loads(failure_path.read_text()) if failure_path.exists() else {}
    skipped_failures = {} if retry_failures else dict(failures)
    status = {
        "status": "running",
        "started_at": now_iso(),
        "checked_this_run": 0,
        "llm_requests_sent": 0,
        "document_limit": documents,
        "failed_this_run": 0,
    }

    def write(path: Path, payload: dict) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)

    def save() -> None:
        status["updated_at"] = now_iso()
        write(state_dir / "status.json", status)

    save()
    workspace = PipelineWorkspace(root)
    consecutive_failures = 0
    try:
        while documents is None or status["checked_this_run"] < documents:
            try:
                store = workspace.processing_order_store(track)
                manifest = store.load_manifest()
                source = Path(manifest["source_path"])
                model = workspace.load_track_state(track).decoder_model_dir
                identity = manifest["source_content_sha256"]
                cursor = int(manifest["cursor"])
                selected = next_unchecked(
                    store,
                    manifest,
                    completed,
                    skipped_failures,
                    (
                        chunk_size
                        if documents is None
                        else min(chunk_size, documents - status["checked_this_run"])
                    ),
                )
                if not selected:
                    status["status"] = "caught_up"
                    save()
                    if documents is not None:
                        break
                    time.sleep(poll_seconds)
                    continue
                payloads = workspace._load_source_payloads(
                    source_path=source, source_line_nos=selected
                )
                status.update(
                    status="running",
                    cursor=cursor,
                    order_generation=manifest["order_generation"],
                    current_source_lines=selected,
                    decoder_model_dir=model,
                )
                save()
                print(
                    f"Checking queue from slot {cursor}; source lines {selected}",
                    flush=True,
                )
                code_identity = implementation_digest(root)

                def record(lines, result):
                    nonlocal consecutive_failures
                    passed = result["status"] == "passed"
                    consecutive_failures = 0 if passed else consecutive_failures + 1
                    for line in lines:
                        key = f"{identity}:{line}"
                        row = {
                            "source_line": line,
                            "source_sha256": manifest["source_content_sha256"],
                            "text_sha256": hashlib.sha256(
                                payloads[line]["text"].encode()
                            ).hexdigest(),
                            "checked_at": now_iso(),
                            "report_path": result["report_path"],
                            "implementation_digest": code_identity,
                            "decoder_model_dir": result.get("prepared", {}).get(
                                "decoder_model_dir"
                            ),
                        }
                        if passed:
                            completed[key] = row
                            failures.pop(key, None)
                            skipped_failures.pop(key, None)
                        else:
                            row.update(
                                error=result.get("error"),
                                workspace_path=result.get("workspace_path"),
                            )
                            failures[key] = row
                            skipped_failures[key] = row
                            status["failed_this_run"] += 1
                        with (state_dir / "history.jsonl").open("a") as history:
                            history.write(
                                json.dumps({**row, "status": result["status"]}) + "\n"
                            )
                    if passed:
                        shutil.rmtree(result["workspace_path"])
                    write(checkpoint, completed)
                    write(failure_path, failures)
                    status["last_report"] = result["report_path"]
                    status["checked_this_run"] += len(lines)
                    status.pop("error", None)
                    save()
                    if consecutive_failures >= 3:
                        consecutive_failures = 0
                        raise RuntimeError(
                            "Three consecutive document failures; pausing before continuing"
                        )

                check_with_isolation(
                    selected,
                    lambda lines: run_check(
                        root, track, lines, state_dir, timeout_seconds
                    ),
                    record,
                )
            except Exception as exc:
                status.update(status="backoff", error=str(exc))
                save()
                print(f"Preflight backing off: {exc}", flush=True)
                time.sleep(poll_seconds)
        else:
            status["status"] = "limit_reached"
        save()
    except BaseException as exc:
        status.update(
            status="stopped" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=str(exc),
        )
        save()
        raise
