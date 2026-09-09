"""Optional exact-request cache. Failures must never block ordinary execution."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
import tomllib

from yomi_corpus.llm.backend import build_response_create_kwargs
from yomi_corpus.llm.parsers import parse_output

LOG = logging.getLogger(__name__)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_identity(task, item):
    body = build_response_create_kwargs(task, item.prompt)
    canonical = encode({"version": 1, "endpoint": task.batch_endpoint, "body": body})
    return hashlib.sha256(canonical.encode()).hexdigest(), canonical


def load_cache_config(root, track):
    path = Path(root) / "config/speculative.toml"
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())["tracks"].get(track, {})


def configured_cache_path(root, track):
    try:
        config = load_cache_config(root, track)
        if config.get("consume_enabled"):
            return Path(root) / config["database"]
    except (OSError, ValueError, KeyError) as exc:
        LOG.warning("Reading cache configuration unavailable: %s", exc)
    return None


def validated_response(task, item, snapshot):
    if snapshot.get("status") != "completed" or not snapshot.get("raw_text"):
        raise ValueError("Not a completed text response")
    parsed = parse_output(snapshot["raw_text"], task.parser, metadata=item.metadata)
    if task.task_name == "yomi_reading":
        from yomi_corpus.yomi.llm_readings import is_valid_yomi_reading

        surface = item.metadata.get("surface")
        reading = parsed.get(surface) if isinstance(parsed, dict) else None
        if not isinstance(reading, str) or not is_valid_yomi_reading(reading):
            raise ValueError("Missing target or non-kana reading")
    return parsed


class ResponseCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            # Rollback journal: the deployed storage is not suitable for WAL.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS responses (
                    key TEXT PRIMARY KEY, request TEXT NOT NULL, snapshot TEXT NOT NULL,
                    origin TEXT NOT NULL, created REAL NOT NULL, used REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, key TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, kind, key):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(kind,key,created) VALUES(?,?,?)",
                (kind, key, time.time()),
            )

    def lookup(self, task, item):
        key, canonical = request_identity(task, item)
        with self.connect() as db:
            row = db.execute(
                "SELECT request,snapshot,origin FROM responses WHERE key=?", (key,)
            ).fetchone()
        if row is None or row[0] != canonical:
            self.event("miss", key)
            return None
        snapshot = json.loads(row[1])
        try:
            parsed = validated_response(task, item, snapshot)
        except (ValueError, TypeError, KeyError):
            self.event("rejected", key)
            return None
        self.event("hit", key)
        with self.connect() as db:
            db.execute("UPDATE responses SET used=? WHERE key=?", (time.time(), key))
        return {
            "item_id": item.item_id,
            "raw_text": snapshot["raw_text"],
            "parsed": parsed,
            "parse_error": None,
            "usage": {
                k: 0
                for k in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cached_input_tokens",
                    "reasoning_tokens",
                )
            },
            "tool_calls": {},
            "metadata": {
                **item.metadata,
                "response_cache": {
                    "key": key,
                    "origin": row[2],
                    "original_usage": snapshot.get("usage"),
                    "response_id": snapshot.get("response_id"),
                },
            },
        }

    def store(self, task, item, snapshot, origin):
        validated_response(task, item, snapshot)
        key, canonical = request_identity(task, item)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO responses VALUES(?,?,?,?,?,?)",
                (key, canonical, encode(snapshot), origin, time.time(), time.time()),
            )

    def records(self, table):
        if table not in {"jobs", "documents", "attempts"}:
            raise ValueError(table)
        with self.connect() as db:
            return [
                json.loads(row[0]) for row in db.execute(f"SELECT payload FROM {table}")
            ]

    def active_jobs(self):
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM jobs WHERE state NOT IN ('fetched','abandoned')"
                )
            ]

    def save_job(self, job):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO jobs VALUES(?,?,?)",
                (job["id"], job["state"], encode(job)),
            )

    def save_document(self, key, document):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO documents VALUES(?,?)", (key, encode(document))
            )

    def forget_job_documents(self, job):
        with self.connect() as db:
            for key in job["documents"]:
                row = db.execute(
                    "SELECT payload FROM documents WHERE key=?", (key,)
                ).fetchone()
                if row and json.loads(row[0]).get("job_id") == job["id"]:
                    db.execute("DELETE FROM documents WHERE key=?", (key,))

    def save_attempt(self, identifier, key, payload):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO attempts VALUES(?,?,?)",
                (identifier, key, encode(payload)),
            )

    def has(self, key):
        with self.connect() as db:
            return (
                db.execute("SELECT 1 FROM responses WHERE key=?", (key,)).fetchone()
                is not None
            )

    def prune(self, days):
        with self.connect() as db:
            cutoff = time.time() - days * 86400
            db.execute("DELETE FROM responses WHERE used < ?", (cutoff,))
            db.execute("DELETE FROM events WHERE created < ?", (cutoff,))
            for identifier, payload in db.execute(
                "SELECT id,payload FROM jobs WHERE state IN ('fetched','abandoned')"
            ).fetchall():
                job = json.loads(payload)
                if job.get("created", time.time()) < cutoff and job.get("items"):
                    job["item_count"] = len(job["items"])
                    job["items"] = {}
                    job["payload_pruned"] = True
                    db.execute(
                        "UPDATE jobs SET payload=? WHERE id=?",
                        (encode(job), identifier),
                    )

    def summary(self):
        with self.connect() as db:
            summary = {
                "responses": db.execute("SELECT count(*) FROM responses").fetchone()[0],
                "documents": db.execute("SELECT count(*) FROM documents").fetchone()[0],
                "events": dict(
                    db.execute("SELECT kind,count(*) FROM events GROUP BY kind")
                ),
                "jobs": dict(
                    db.execute("SELECT state,count(*) FROM jobs GROUP BY state")
                ),
            }
            attempts = [
                json.loads(row[0]) for row in db.execute("SELECT payload FROM attempts")
            ]
        summary["batch_usage"] = {
            field: sum(
                int((row["snapshot"].get("usage") or {}).get(field) or 0)
                for row in attempts
            )
            for field in ("input_tokens", "output_tokens", "total_tokens")
        }
        summary["batch_results"] = len(attempts)
        summary["invalid_batch_results"] = sum(not row["valid"] for row in attempts)
        summary["preparation_failures"] = [
            row for row in self.records("documents") if row.get("error")
        ]
        summary["active_jobs"] = [
            {
                key: job.get(key)
                for key in ("id", "state", "batch_id", "remote_status", "error")
            }
            for job in self.active_jobs()
        ]
        return summary


def seed_results(cache, task, items, output_path, pending_ids=()):
    from yomi_corpus.llm.runner import load_result_item_ids

    completed = load_result_item_ids(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for item in items:
            if item.item_id in completed or item.item_id in pending_ids:
                continue
            row = cache.lookup(task, item)
            if row is not None:
                handle.write(encode(row) + "\n")
                handle.flush()
                completed.add(item.item_id)


def capture_results(cache, task, items, output_path):
    from yomi_corpus.llm.runner import iter_jsonl_rows_tolerating_truncated_tail

    by_id = {item.item_id: item for item in items}
    for row in iter_jsonl_rows_tolerating_truncated_tail(output_path):
        if row.get("parse_error") or row.get("metadata", {}).get("response_cache"):
            continue
        item = by_id.get(row.get("item_id"))
        if item is None:
            continue
        try:
            api = row.get("metadata", {}).get("api_response") or {}
            if api.get("request_key") != request_identity(task, item)[0]:
                # Legacy/resumed jobs may have been submitted using different
                # settings. Unknown provenance must not poison the cache.
                continue
            cache.store(
                task,
                item,
                {
                    **row,
                    "status": api.get("status") or "completed",
                    "response_id": api.get("response_id"),
                },
                str(output_path),
            )
        except (ValueError, TypeError, KeyError):
            continue
