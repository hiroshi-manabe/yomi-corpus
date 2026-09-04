from __future__ import annotations

import csv
import gzip
import hashlib
import heapq
import json
import math
import os
import random
import sqlite3
import subprocess
import tempfile
from array import array
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from yomi_corpus.vocabulary_balance import HAN_RE


INDEX_SCHEMA_VERSION = 1
MATCHING_POLICY = "literal-kanji-forms-min-2-v1"
PLAN_SCHEMA_VERSION = 1
PREVIEW_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class VocabularyTarget:
    target_id: int
    lemma: str
    forms: tuple[str, ...]
    bccwj_count: int
    bccwj_document_count: int


class FixedStringMatcher:
    """Unicode Aho-Corasick matcher returning pattern occurrence counts."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.transitions: list[dict[str, int]] = [{}]
        self.failures = [0]
        self.outputs: list[list[str]] = [[]]
        for pattern in sorted(set(patterns)):
            state = 0
            for char in pattern:
                state = self.transitions[state].setdefault(char, len(self.transitions))
                if state == len(self.failures):
                    self.transitions.append({})
                    self.failures.append(0)
                    self.outputs.append([])
            self.outputs[state].append(pattern)
        queue = deque(self.transitions[0].values())
        while queue:
            state = queue.popleft()
            for char, next_state in self.transitions[state].items():
                queue.append(next_state)
                failure = self.failures[state]
                while failure and char not in self.transitions[failure]:
                    failure = self.failures[failure]
                self.failures[next_state] = self.transitions[failure].get(char, 0)
                self.outputs[next_state].extend(self.outputs[self.failures[next_state]])

    def count(self, text: str) -> Counter[str]:
        found: Counter[str] = Counter()
        state = 0
        for char in text:
            while state and char not in self.transitions[state]:
                state = self.failures[state]
            state = self.transitions[state].get(char, 0)
            found.update(self.outputs[state])
        return found


class AcceleratedFixedStringMatcher:
    def __init__(self, patterns: Iterable[str]) -> None:
        import ahocorasick

        self.automaton = ahocorasick.Automaton()
        for pattern in sorted(set(patterns)):
            self.automaton.add_word(pattern, pattern)
        self.automaton.make_automaton()

    def count(self, text: str) -> Counter[str]:
        return Counter(pattern for _, pattern in self.automaton.iter(text))


def make_fixed_string_matcher(patterns: Iterable[str]):
    try:
        return AcceleratedFixedStringMatcher(patterns), "pyahocorasick"
    except ImportError:
        return FixedStringMatcher(patterns), "python"


def read_vocabulary_targets(path: Path, *, min_form_chars: int = 2) -> list[VocabularyTarget]:
    rows: list[VocabularyTarget] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for target_id, row in enumerate(csv.DictReader(handle, delimiter="\t"), start=1):
            lemma = str(row["lemma"]).strip()
            forms = {lemma}
            forms.update(_compact_counter_values(str(row.get("written_forms") or "")))
            eligible_forms = tuple(
                sorted(
                    form
                    for form in forms
                    if len(form) >= min_form_chars and HAN_RE.search(form)
                )
            )
            rows.append(
                VocabularyTarget(
                    target_id=target_id,
                    lemma=lemma,
                    forms=eligible_forms,
                    bccwj_count=int(row.get("bccwj_common_noun_count") or row.get("bccwj_count") or 0),
                    bccwj_document_count=int(
                        row.get("bccwj_common_noun_document_count")
                        or row.get("bccwj_document_count")
                        or 0
                    ),
                )
            )
    return rows


def _compact_counter_values(value: str) -> Iterator[str]:
    for item in value.split("|"):
        if not item:
            continue
        surface, separator, count = item.rpartition(":")
        if separator and count.isdigit() and surface:
            yield surface


def build_coverage_index(
    *,
    source_path: Path,
    order_path: Path,
    order_manifest: dict,
    targets_path: Path,
    output_path: Path,
    reserved_source_documents: int = 50_000,
    first_mutable_slot: int | None = None,
    progress_every: int = 100_000,
) -> dict:
    targets = read_vocabulary_targets(targets_path)
    searchable = [target for target in targets if target.forms]
    form_targets: dict[str, list[int]] = defaultdict(list)
    for target in searchable:
        for form in target.forms:
            form_targets[form].append(target.target_id)
    matcher, matcher_engine = make_fixed_string_matcher(form_targets)
    rank_by_source_line = _rank_by_source_line(order_path)
    mutable_slot = first_mutable_slot or int(order_manifest["cursor"])
    if order_manifest.get("reservation") is not None:
        raise ValueError("Cannot build a campaign index while an order reservation is active.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    patterns_path: Path | None = None
    connection: sqlite3.Connection | None = None
    matched_documents = 0
    hit_rows = 0
    try:
        connection = sqlite3.connect(temp_path)
        _create_index_schema(connection)
        connection.executemany(
            "INSERT INTO targets VALUES (?, ?, ?, ?, ?)",
            [
                (
                    target.target_id,
                    target.lemma,
                    json.dumps(target.forms, ensure_ascii=False, separators=(",", ":")),
                    target.bccwj_count,
                    target.bccwj_document_count,
                )
                for target in targets
            ],
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as patterns:
            patterns.write("\n".join(sorted(form_targets)) + "\n")
            patterns_path = Path(patterns.name)
        process = subprocess.Popen(
            [
                "bash",
                "-o",
                "pipefail",
                "-c",
                "gzip -dc -- \"$1\" | rg --text --line-number --no-filename --fixed-strings --file \"$2\" -",
                "vocabulary-index",
                str(source_path),
                str(patterns_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        assert process.stdout is not None
        pending_documents: list[tuple] = []
        pending_hits: list[tuple[int, int, int]] = []
        for output_line in process.stdout:
            line_text, raw_json = output_line.split(":", 1)
            source_line_no = int(line_text)
            if source_line_no <= reserved_source_documents:
                continue
            current_slot = _rank_for_source_line(rank_by_source_line, source_line_no)
            if current_slot < mutable_slot:
                continue
            payload = json.loads(raw_json)
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            form_counts = matcher.count(text)
            target_counts: Counter[int] = Counter()
            for form, count in form_counts.items():
                for target_id in form_targets[form]:
                    target_counts[target_id] = max(target_counts[target_id], count)
            if not target_counts:
                continue
            source_record_id = source_record_identity(payload)
            pending_documents.append(
                (
                    source_line_no,
                    source_record_id,
                    current_slot,
                    len(text),
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    json.dumps(_quality_signals(text), separators=(",", ":")),
                )
            )
            pending_hits.extend(
                (source_line_no, target_id, count)
                for target_id, count in target_counts.items()
            )
            matched_documents += 1
            hit_rows += len(target_counts)
            if len(pending_documents) >= 20_000:
                _flush_index_rows(connection, pending_documents, pending_hits)
                pending_documents.clear()
                pending_hits.clear()
            if progress_every and matched_documents % progress_every == 0:
                print(f"indexed matched documents: {matched_documents:,}", flush=True)
        if pending_documents:
            _flush_index_rows(connection, pending_documents, pending_hits)
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr is not None else ""
        returncode = process.wait()
        if returncode not in (0, 1):
            raise RuntimeError(f"source scan failed ({returncode}): {stderr.strip()}")

        connection.execute(
            "CREATE UNIQUE INDEX documents_by_source_record_id "
            "ON documents(source_record_id)"
        )
        connection.execute(
            "CREATE INDEX hits_by_target ON hits(target_id, source_line_no)"
        )

        metadata = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "matching_policy": MATCHING_POLICY,
            "matcher_engine": matcher_engine,
            "complete": True,
            "created_at": now_iso(),
            "source_path": str(source_path.resolve()),
            "source_content_sha256": str(order_manifest.get("source_content_sha256") or ""),
            "source_sequence_epoch": str(order_manifest.get("source_sequence_epoch") or ""),
            "processing_order_generation": int(order_manifest["order_generation"]),
            "processing_order_cursor": int(order_manifest["cursor"]),
            "reserved_source_documents": reserved_source_documents,
            "first_mutable_slot": mutable_slot,
            "target_list_sha256": hashlib.sha256(targets_path.read_bytes()).hexdigest(),
            "target_count": len(targets),
            "searchable_target_count": len(searchable),
            "matched_document_count": matched_documents,
            "hit_row_count": hit_rows,
        }
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
        connection.execute("PRAGMA optimize")
        connection.close()
        connection = None
        os.replace(temp_path, output_path)
        return metadata
    finally:
        if connection is not None:
            connection.close()
        temp_path.unlink(missing_ok=True)
        if patterns_path is not None:
            patterns_path.unlink(missing_ok=True)


def _create_index_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;
        PRAGMA cache_size = -262144;
        CREATE TABLE documents (
            source_line_no INTEGER PRIMARY KEY,
            source_record_id TEXT NOT NULL,
            current_slot INTEGER NOT NULL,
            text_length INTEGER NOT NULL,
            text_sha256 TEXT NOT NULL,
            quality_signals TEXT NOT NULL
        );
        CREATE TABLE targets (
            target_id INTEGER PRIMARY KEY,
            lemma TEXT NOT NULL UNIQUE,
            approved_forms TEXT NOT NULL,
            bccwj_count INTEGER NOT NULL,
            bccwj_document_count INTEGER NOT NULL
        );
        CREATE TABLE hits (
            source_line_no INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            occurrence_count INTEGER NOT NULL,
            PRIMARY KEY (source_line_no, target_id)
        ) WITHOUT ROWID;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )


def _flush_index_rows(connection: sqlite3.Connection, documents: list[tuple], hits: list[tuple]) -> None:
    connection.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)", documents)
    connection.executemany("INSERT INTO hits VALUES (?, ?, ?)", hits)
    connection.commit()


def _rank_by_source_line(order_path: Path) -> array:
    order = array("I")
    with order_path.open("rb") as handle:
        order.fromfile(handle, order_path.stat().st_size // 4)
    if os.sys.byteorder != "little":
        order.byteswap()
    ranks = array("I", [0]) * (max(order, default=0) + 1)
    for slot, source_line_no in enumerate(order, start=1):
        ranks[source_line_no] = slot
    return ranks


def _rank_for_source_line(ranks: array, source_line_no: int) -> int:
    return int(ranks[source_line_no]) if source_line_no < len(ranks) else 0


def source_record_identity(payload: dict) -> str:
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("docId", "doc_id", "id"):
            if meta.get(key):
                return str(meta[key])
    for key in ("doc_id", "id"):
        if payload.get(key):
            return str(payload[key])
    raise ValueError("Source record does not contain a stable identity.")


def _quality_signals(text: str) -> dict[str, float | int]:
    whitespace = sum(char.isspace() for char in text)
    return {
        "line_count": text.count("\n") + 1,
        "whitespace_ratio": round(whitespace / max(1, len(text)), 6),
    }


def index_metadata(connection: sqlite3.Connection) -> dict:
    return {
        key: json.loads(value)
        for key, value in connection.execute("SELECT key, value FROM metadata")
    }


def build_selection_plan(
    *,
    index_path: Path,
    output_path: Path,
    slot_start: int = 2_001,
    document_count: int = 2_000,
    target_examples: int = 3,
    scoring_pool_size: int = 50_000,
    order_manifest: dict | None = None,
) -> dict:
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    try:
        metadata = index_metadata(connection)
        if not metadata.get("complete"):
            raise ValueError("Coverage index is incomplete.")
        if order_manifest is not None:
            if order_manifest.get("reservation") is not None:
                raise ValueError("Cannot plan while a processing-order reservation is active.")
            if int(order_manifest["cursor"]) > slot_start:
                raise ValueError("Plan starts before the current processing-order cursor.")
            if int(order_manifest["order_generation"]) != int(
                metadata["processing_order_generation"]
            ):
                raise ValueError("Coverage index belongs to a different order generation.")
            if str(order_manifest.get("source_content_sha256") or "") != str(
                metadata.get("source_content_sha256") or ""
            ):
                raise ValueError("Coverage index belongs to a different source build.")
        minimum_current_slot = max(
            slot_start,
            int(order_manifest["cursor"] if order_manifest is not None else slot_start),
        )
        availability = {
            int(target_id): int(count)
            for target_id, count in connection.execute(
                "SELECT target_id, COUNT(*) FROM hits GROUP BY target_id"
            )
        }
        target_rows = {
            int(row["target_id"]): row
            for row in connection.execute("SELECT * FROM targets")
        }
        weights = {
            target_id: math.log1p(int(target_rows[target_id]["bccwj_document_count"]))
            / math.sqrt(max(1, available))
            for target_id, available in availability.items()
        }
        connection.execute(
            "CREATE TEMP TABLE target_weights (target_id INTEGER PRIMARY KEY, weight REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO target_weights VALUES (?, ?)", weights.items()
        )
        top_scoring = {
            int(source_line_no)
            for (source_line_no,) in connection.execute(
                """
                SELECT h.source_line_no
                FROM hits AS h
                JOIN target_weights AS w USING (target_id)
                JOIN documents AS d USING (source_line_no)
                WHERE d.current_slot >= ?
                GROUP BY h.source_line_no
                ORDER BY SUM(w.weight) / SQRT(MAX(1.0, d.text_length / 5000.0)) DESC,
                         h.source_line_no
                LIMIT ?
                """,
                (minimum_current_slot, scoring_pool_size),
            )
        }
        rare_representatives = {
            int(source_line_no)
            for source_line_no, rank in connection.execute(
                """
                SELECT source_line_no, target_rank
                FROM (
                    SELECT h.source_line_no AS source_line_no,
                           ROW_NUMBER() OVER (
                               PARTITION BY target_id
                               ORDER BY occurrence_count DESC, source_line_no
                           ) AS target_rank
                    FROM hits AS h
                    JOIN documents AS d USING (source_line_no)
                    WHERE d.current_slot >= ?
                )
                WHERE target_rank <= ?
                """,
                (minimum_current_slot, target_examples),
            )
        }
        candidate_lines = top_scoring | rare_representatives
        connection.execute(
            "CREATE TEMP TABLE candidate_sources (source_line_no INTEGER PRIMARY KEY)"
        )
        connection.executemany(
            "INSERT INTO candidate_sources VALUES (?)",
            ((line,) for line in candidate_lines),
        )
        grouped: dict[int, list[int]] = defaultdict(list)
        for source_line_no, target_id in connection.execute(
            """
            SELECT h.source_line_no, h.target_id
            FROM hits AS h
            JOIN candidate_sources AS c USING (source_line_no)
            ORDER BY h.source_line_no, h.target_id
            """
        ):
            grouped[int(source_line_no)].append(int(target_id))
        doc_targets = {key: tuple(value) for key, value in grouped.items()}
        docs = {
            int(row["source_line_no"]): row
            for row in connection.execute(
                """
                SELECT d.source_line_no, d.source_record_id, d.current_slot,
                       d.text_length, d.text_sha256
                FROM documents AS d
                JOIN candidate_sources AS c USING (source_line_no)
                """
            )
        }
        coverage: Counter[int] = Counter()
        selected: list[int] = []
        used: set[int] = set()
        used_text_hashes: set[str] = set()
        heap: list[tuple[float, int, int]] = []

        def score(source_line_no: int) -> float:
            row = docs[source_line_no]
            if str(row["text_sha256"]) in used_text_hashes:
                return 0.0
            gain = sum(
                weights.get(target_id, 0.0)
                for target_id in doc_targets[source_line_no]
                if coverage[target_id] < target_examples
            )
            return gain / math.sqrt(max(1.0, int(row["text_length"]) / 5_000.0))

        for source_line_no in docs:
            heapq.heappush(heap, (-score(source_line_no), source_line_no, 0))
        selected_scores: dict[int, float] = {}
        while heap and len(selected) < document_count:
            negative_old_score, source_line_no, revision = heapq.heappop(heap)
            if source_line_no in used:
                continue
            current_score = score(source_line_no)
            if current_score <= 0:
                continue
            if revision != len(selected) and not math.isclose(
                current_score, -negative_old_score, rel_tol=1e-12, abs_tol=1e-15
            ):
                heapq.heappush(heap, (-current_score, source_line_no, len(selected)))
                continue
            used.add(source_line_no)
            selected.append(source_line_no)
            selected_scores[source_line_no] = current_score
            used_text_hashes.add(str(docs[source_line_no]["text_sha256"]))
            for target_id in doc_targets[source_line_no]:
                if coverage[target_id] < target_examples:
                    coverage[target_id] += 1
        if len(selected) < document_count:
            remaining = sorted(
                (line for line in docs if line not in used),
                key=lambda line: (int(docs[line]["current_slot"]), line),
            )
            selected.extend(remaining[: document_count - len(selected)])
            selected_scores.update({line: 0.0 for line in selected if line not in selected_scores})
        if len(selected) < document_count:
            raise ValueError(f"Only {len(selected)} eligible documents are available.")

        selection = []
        for offset, source_line_no in enumerate(selected):
            target_ids = doc_targets[source_line_no]
            selection.append(
                {
                    "assigned_slot": slot_start + offset,
                    "source_line_no": source_line_no,
                    "source_record_id": str(docs[source_line_no]["source_record_id"]),
                    "current_slot": int(docs[source_line_no]["current_slot"]),
                    "text_length": int(docs[source_line_no]["text_length"]),
                    "score": round(selected_scores[source_line_no], 10),
                    "targets": [str(target_rows[target_id]["lemma"]) for target_id in target_ids],
                    "rarest_target_availability": min(
                        (availability[target_id] for target_id in target_ids), default=0
                    ),
                }
            )
        identity = {
            "index_source_sha256": metadata.get("source_content_sha256"),
            "index_target_sha256": metadata.get("target_list_sha256"),
            "slot_start": slot_start,
            "document_count": document_count,
            "target_examples": target_examples,
            "processing_order_cursor_at_planning": int(
                order_manifest["cursor"] if order_manifest is not None else metadata["processing_order_cursor"]
            ),
            "scoring_pool_size": scoring_pool_size,
            "candidate_pool_document_count": len(docs),
            "selected_source_record_ids": [row["source_record_id"] for row in selection],
        }
        plan_id = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        plan = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "plan_id": plan_id,
            "created_at": now_iso(),
            "status": "proposal",
            "installed": False,
            "index_path": str(index_path.resolve()),
            "index_metadata": metadata,
            "slot_start": slot_start,
            "slot_end": slot_start + document_count - 1,
            "document_count": document_count,
            "target_examples": target_examples,
            "scoring_pool_size": scoring_pool_size,
            "candidate_pool_document_count": len(docs),
            "target_count": len(target_rows),
            "targets_with_candidates": len(availability),
            "targets_meeting_goal": sum(coverage[target_id] >= target_examples for target_id in target_rows),
            "selection": selection,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return plan
    finally:
        connection.close()


def build_preview_artifact(
    *,
    plan_path: Path,
    index_path: Path,
    output_path: Path,
    sample_size: int = 50,
) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    selection = list(plan.get("selection") or [])
    if not selection:
        raise ValueError("Selection plan contains no documents.")
    sample_size = min(sample_size, len(selection))
    sample_ids, strata = _stratified_sample(selection, sample_size, str(plan["plan_id"]))
    rows_by_line: dict[int, sqlite3.Row] = {}
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    try:
        metadata = index_metadata(connection)
        placeholders = ",".join("?" for _ in sample_ids)
        for row in connection.execute(
            f"SELECT source_line_no, source_record_id, current_slot, text_length FROM documents WHERE source_line_no IN ({placeholders})",
            sample_ids,
        ):
            rows_by_line[int(row["source_line_no"])] = row
    finally:
        connection.close()
    texts_by_line = extract_source_texts(Path(metadata["source_path"]), set(sample_ids))
    selection_by_line = {int(row["source_line_no"]): row for row in selection}
    documents = []
    for source_line_no in sample_ids:
        source = rows_by_line[source_line_no]
        selected = selection_by_line[source_line_no]
        documents.append(
            {
                "source_line_no": source_line_no,
                "source_record_id": str(source["source_record_id"]),
                "proposed_slot": int(selected["assigned_slot"]),
                "current_slot": int(source["current_slot"]),
                "text_length": int(source["text_length"]),
                "score": selected["score"],
                "sample_stratum": strata[source_line_no],
                "matched_targets": selected["targets"],
                "text": texts_by_line[source_line_no],
            }
        )
    artifact = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "plan_id": plan["plan_id"],
        "created_at": now_iso(),
        "read_only": True,
        "installation_status": "not_installed",
        "slot_start": plan["slot_start"],
        "slot_end": plan["slot_end"],
        "planned_document_count": plan["document_count"],
        "sample_document_count": len(documents),
        "sampling": {
            "seed": plan["plan_id"],
            "uniform_count": sum(value == "uniform" for value in strata.values()),
            "high_score_count": sum(value == "high_score" for value in strata.values()),
            "rare_target_count": sum(value == "rare_target" for value in strata.values()),
        },
        "documents": documents,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def extract_source_texts(source_path: Path, source_line_nos: set[int]) -> dict[int, str]:
    if not source_line_nos:
        return {}
    remaining = set(source_line_nos)
    texts: dict[int, str] = {}
    maximum = max(remaining)
    with gzip.open(source_path, "rt", encoding="utf-8") as source:
        for source_line_no, raw_line in enumerate(source, start=1):
            if source_line_no > maximum or not remaining:
                break
            if source_line_no not in remaining:
                continue
            payload = json.loads(raw_line)
            text = payload.get("text")
            if not isinstance(text, str):
                raise ValueError(f"Source line {source_line_no} has no text.")
            texts[source_line_no] = text
            remaining.remove(source_line_no)
    if remaining:
        raise ValueError(f"Could not find {len(remaining)} sampled source documents.")
    return texts


def _stratified_sample(
    selection: list[dict], sample_size: int, seed: str
) -> tuple[list[int], dict[int, str]]:
    high_count = min(10, sample_size // 5)
    rare_count = min(10, sample_size // 5)
    uniform_count = sample_size - high_count - rare_count
    chosen: list[int] = []
    strata: dict[int, str] = {}

    def add(rows: Iterable[dict], label: str, count: int) -> None:
        if count <= 0:
            return
        for row in rows:
            source_line_no = int(row["source_line_no"])
            if source_line_no in strata:
                continue
            chosen.append(source_line_no)
            strata[source_line_no] = label
            if sum(value == label for value in strata.values()) >= count:
                return

    add(sorted(selection, key=lambda row: (-float(row["score"]), int(row["source_line_no"]))), "high_score", high_count)
    add(
        sorted(
            selection,
            key=lambda row: (
                int(row.get("rarest_target_availability") or 10**12),
                -len(row.get("targets") or []),
                int(row["source_line_no"]),
            ),
        ),
        "rare_target",
        rare_count,
    )
    remaining = [row for row in selection if int(row["source_line_no"]) not in strata]
    random.Random(seed).shuffle(remaining)
    add(remaining, "uniform", uniform_count)
    return chosen, strata
