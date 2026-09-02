from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import struct
import tempfile
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
ENTRY_FORMAT = "uint32-le"
ENTRY_SIZE = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ProcessingOrderStore:
    def __init__(self, root: Path, track_name: str) -> None:
        self.root = Path(root)
        self.track_name = track_name
        self.state_dir = self.root / "data" / "pipeline" / "processing_order"
        self.order_path = self.state_dir / f"{track_name}.u32"
        self.manifest_path = self.state_dir / f"{track_name}.json"
        self.events_path = self.state_dir / f"{track_name}.events.jsonl"

    def ensure(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        ledger_rows: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        rows = list(ledger_rows)
        if not self.manifest_path.exists() or not self.order_path.exists():
            self.initialize(
                source_path=source_path,
                dataset_name=dataset_name,
                ledger_rows=rows,
            )
        manifest = self.load_manifest()
        self._validate_source_identity(
            manifest,
            source_path=source_path,
            dataset_name=dataset_name,
        )
        manifest = self.recover_materialized_reservation(manifest)
        manifest = self.reconcile_cursor(rows)
        self.validate_frozen_prefix(rows, manifest=manifest)
        return manifest

    def recover_materialized_reservation(
        self, manifest: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        current = manifest or self.load_manifest()
        reservation = current.get("reservation")
        if not isinstance(reservation, dict):
            return current
        batch_name = str(reservation["batch_name"])
        batch_manifest_path = self.root / "data" / "units" / batch_name / "manifest.json"
        batch_state_path = self.root / "data" / "pipeline" / "batches" / f"{batch_name}.json"
        if not batch_manifest_path.exists() or not batch_state_path.exists():
            return current
        batch_manifest = json.loads(batch_manifest_path.read_text(encoding="utf-8"))
        expected = self.reservation_assignments(reservation)
        if batch_manifest.get("processing_order_assignments") != expected:
            raise ValueError(
                f"Materialized batch {batch_name} does not match its order reservation."
            )
        return self.commit_reservation(batch_name)

    def initialize(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        ledger_rows: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        source_path = Path(source_path).resolve()
        rows = list(ledger_rows)
        by_slot = self._ledger_by_slot(rows)
        cursor = max(by_slot, default=0) + 1
        self.state_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        document_count = 0

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.track_name}.", suffix=".u32.tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(fd, "wb") as output, gzip.open(
                source_path, "rt", encoding="utf-8"
            ) as source:
                chunk = array("I")
                for source_line_no, line in enumerate(source, start=1):
                    digest.update(line.encode("utf-8"))
                    payload = json.loads(line)
                    text = payload.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    document_count += 1
                    expected = by_slot.get(document_count)
                    if expected is not None and expected != source_line_no:
                        raise ValueError(
                            "Existing ledger does not match identity processing order: "
                            f"slot {document_count} has source line {expected}, expected "
                            f"{source_line_no}."
                        )
                    chunk.append(source_line_no)
                    if len(chunk) >= 65536:
                        self._write_uint32_chunk(output, chunk)
                        chunk = array("I")
                if chunk:
                    self._write_uint32_chunk(output, chunk)
                output.flush()
                os.fsync(output.fileno())
            if cursor > document_count + 1:
                raise ValueError(
                    f"Ledger cursor {cursor} exceeds source document count {document_count}."
                )
            os.replace(temp_name, self.order_path)
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise

        stat = source_path.stat()
        created_at = now_iso()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "track_name": self.track_name,
            "dataset_name": dataset_name,
            "source_path": str(source_path),
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "source_content_sha256": digest.hexdigest(),
            "document_count": document_count,
            "cursor": cursor,
            "order_generation": 1,
            "entry_format": ENTRY_FORMAT,
            "reservation": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        self._write_manifest(manifest)
        self._append_event(
            {
                "event": "initialized",
                "at": created_at,
                "document_count": document_count,
                "cursor": cursor,
                "source_content_sha256": digest.hexdigest(),
            }
        )
        return manifest

    def load_manifest(self) -> dict[str, Any]:
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version") or 0) != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported processing-order schema: {payload.get('schema_version')}"
            )
        if payload.get("entry_format") != ENTRY_FORMAT:
            raise ValueError(
                "Unsupported processing-order entry format: "
                f"{payload.get('entry_format')}"
            )
        expected_size = int(payload["document_count"]) * ENTRY_SIZE
        actual_size = self.order_path.stat().st_size
        if actual_size != expected_size:
            raise ValueError(
                f"Processing-order size mismatch: expected {expected_size}, got {actual_size}."
            )
        return payload

    def peek(self, count: int) -> list[dict[str, int]]:
        manifest = self.load_manifest()
        reservation = manifest.get("reservation")
        if isinstance(reservation, dict):
            return self.reservation_assignments(reservation)
        cursor = int(manifest["cursor"])
        source_lines = self.read_slots(cursor, count)
        return [
            {"processing_slot": cursor + offset, "source_line_no": source_line_no}
            for offset, source_line_no in enumerate(source_lines)
        ]

    def reserve(self, *, batch_name: str, count: int) -> dict[str, Any]:
        manifest = self.load_manifest()
        existing = manifest.get("reservation")
        if isinstance(existing, dict):
            if str(existing.get("batch_name")) != batch_name:
                raise ValueError(
                    "Processing order already has an active reservation for "
                    f"{existing.get('batch_name')}."
                )
            return existing
        cursor = int(manifest["cursor"])
        source_lines = self.read_slots(cursor, count)
        if len(source_lines) != count:
            raise EOFError(
                f"Requested {count} documents at slot {cursor}, but only "
                f"{len(source_lines)} remain."
            )
        reservation = {
            "batch_name": batch_name,
            "start_slot": cursor,
            "count": count,
            "source_line_nos": source_lines,
            "created_at": now_iso(),
        }
        manifest["reservation"] = reservation
        manifest["updated_at"] = now_iso()
        self._write_manifest(manifest)
        self._append_event({"event": "reserved", "at": now_iso(), **reservation})
        return reservation

    def commit_reservation(self, batch_name: str) -> dict[str, Any]:
        manifest = self.load_manifest()
        reservation = manifest.get("reservation")
        if not isinstance(reservation, dict):
            return manifest
        if str(reservation.get("batch_name")) != batch_name:
            raise ValueError(
                f"Cannot commit reservation for {batch_name}; active batch is "
                f"{reservation.get('batch_name')}."
            )
        start_slot = int(reservation["start_slot"])
        count = int(reservation["count"])
        if int(manifest["cursor"]) != start_slot:
            raise ValueError("Processing-order cursor moved while a reservation was active.")
        manifest["cursor"] = start_slot + count
        manifest["reservation"] = None
        manifest["updated_at"] = now_iso()
        self._write_manifest(manifest)
        self._append_event(
            {
                "event": "committed",
                "at": now_iso(),
                "batch_name": batch_name,
                "start_slot": start_slot,
                "count": count,
                "cursor": manifest["cursor"],
            }
        )
        return manifest

    def reconcile_cursor(self, ledger_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
        manifest = self.load_manifest()
        if isinstance(manifest.get("reservation"), dict):
            return manifest
        by_slot = self._ledger_by_slot(ledger_rows)
        cursor = int(manifest["cursor"])
        while cursor in by_slot:
            source_line = self.read_slots(cursor, 1)
            if not source_line or source_line[0] != by_slot[cursor]:
                raise ValueError(
                    f"Ledger source line does not match processing slot {cursor}."
                )
            cursor += 1
        if cursor != int(manifest["cursor"]):
            manifest["cursor"] = cursor
            manifest["updated_at"] = now_iso()
            self._write_manifest(manifest)
            self._append_event(
                {"event": "cursor_reconciled", "at": now_iso(), "cursor": cursor}
            )
        return manifest

    def validate_frozen_prefix(
        self,
        ledger_rows: Iterable[dict[str, Any]],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        current = manifest or self.load_manifest()
        by_slot = self._ledger_by_slot(ledger_rows)
        cursor = int(current["cursor"])
        if set(range(1, cursor)) != {slot for slot in by_slot if slot < cursor}:
            raise ValueError("Document ledger does not contain the complete frozen order prefix.")
        frozen = self.read_slots(1, cursor - 1)
        for slot, source_line in enumerate(frozen, start=1):
            if by_slot.get(slot) != source_line:
                raise ValueError(
                    f"Frozen processing slot {slot} does not match the document ledger."
                )

    def read_slots(self, start_slot: int, count: int) -> list[int]:
        if start_slot < 1 or count < 0:
            raise ValueError("Processing slots are one-based and count must be nonnegative.")
        with self.order_path.open("rb") as handle:
            handle.seek((start_slot - 1) * ENTRY_SIZE)
            data = handle.read(count * ENTRY_SIZE)
        if len(data) % ENTRY_SIZE:
            raise ValueError("Processing-order file contains a partial entry.")
        return [value[0] for value in struct.iter_unpack("<I", data)]

    def swap_slots(self, first_slot: int, second_slot: int) -> dict[str, Any]:
        manifest = self.load_manifest()
        if isinstance(manifest.get("reservation"), dict):
            raise ValueError("Cannot reorder while a refill reservation is active.")
        cursor = int(manifest["cursor"])
        document_count = int(manifest["document_count"])
        for slot in (first_slot, second_slot):
            if slot < cursor:
                raise ValueError(f"Processing slot {slot} is frozen before cursor {cursor}.")
            if slot > document_count:
                raise IndexError(f"Processing slot {slot} exceeds {document_count} documents.")
        if first_slot == second_slot:
            return manifest

        values = array("I")
        with self.order_path.open("rb") as source:
            values.fromfile(source, document_count)
        if values.itemsize != ENTRY_SIZE:
            raise RuntimeError("This platform does not provide 32-bit unsigned array entries.")
        if os.sys.byteorder != "little":
            values.byteswap()
        values[first_slot - 1], values[second_slot - 1] = (
            values[second_slot - 1],
            values[first_slot - 1],
        )
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.track_name}.", suffix=".u32.tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(values.tobytes())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, self.order_path)
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise
        manifest["order_generation"] = int(manifest["order_generation"]) + 1
        manifest["updated_at"] = now_iso()
        self._write_manifest(manifest)
        self._append_event(
            {
                "event": "slots_swapped",
                "at": now_iso(),
                "first_slot": first_slot,
                "second_slot": second_slot,
                "order_generation": manifest["order_generation"],
            }
        )
        return manifest

    def migrate_unprocessed_suffix(
        self,
        *,
        source_path: Path,
        dataset_name: str,
        ledger_rows: Iterable[dict[str, Any]],
        frozen_through_slot: int,
    ) -> dict[str, Any]:
        """Move an order to a regenerated source without changing its frozen prefix."""
        old_manifest = self.load_manifest()
        if isinstance(old_manifest.get("reservation"), dict):
            raise ValueError("Cannot migrate processing order with an active reservation.")
        rows = list(ledger_rows)
        if any(int(row.get("track_doc_seq") or 0) > frozen_through_slot for row in rows):
            raise ValueError("Document ledger extends beyond the requested frozen prefix.")
        frozen_by_slot = self._ledger_by_slot(rows)
        if set(frozen_by_slot) != set(range(1, frozen_through_slot + 1)):
            raise ValueError("Document ledger does not contain the requested frozen prefix.")
        for slot, source_line in enumerate(
            self.read_slots(1, frozen_through_slot), start=1
        ):
            if frozen_by_slot[slot] != source_line:
                raise ValueError(f"Frozen processing slot {slot} does not match the ledger.")

        source_path = Path(source_path).resolve()
        old_source_path = Path(str(old_manifest["source_path"]))
        frozen_source_lines = {
            int(row["source_line_no"])
            for row in rows
            if int(row.get("track_doc_seq") or 0) <= frozen_through_slot
        }
        old_order = array("I")
        with self.order_path.open("rb") as handle:
            old_order.fromfile(handle, int(old_manifest["document_count"]))
        if os.sys.byteorder != "little":
            old_order.byteswap()
        max_source_line = max(old_order, default=0)
        rank_by_source_line = array("I", [0]) * (max_source_line + 1)
        for rank, source_line in enumerate(old_order, start=1):
            if rank > frozen_through_slot:
                rank_by_source_line[source_line] = rank

        self.state_dir.mkdir(parents=True, exist_ok=True)
        database_fd, database_name = tempfile.mkstemp(
            prefix=f".{self.track_name}.source-migration.",
            suffix=".sqlite3",
            dir=self.state_dir,
        )
        os.close(database_fd)
        suffix_fd, suffix_name = tempfile.mkstemp(
            prefix=f".{self.track_name}.new-only.", suffix=".u32.tmp", dir=self.state_dir
        )
        os.close(suffix_fd)
        order_fd, order_name = tempfile.mkstemp(
            prefix=f".{self.track_name}.", suffix=".u32.tmp", dir=self.state_dir
        )
        os.close(order_fd)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database_name)
            connection.execute(
                "CREATE TABLE old_suffix (source_id TEXT PRIMARY KEY, old_rank INTEGER NOT NULL, "
                "new_line INTEGER)"
            )
            frozen_source_ids: set[str] = set()
            pending: list[tuple[str, int]] = []
            with gzip.open(old_source_path, "rt", encoding="utf-8") as source:
                for source_line_no, line in enumerate(source, start=1):
                    if source_line_no in frozen_source_lines:
                        frozen_source_ids.add(_source_record_id(json.loads(line)))
                    if source_line_no >= len(rank_by_source_line):
                        continue
                    old_rank = int(rank_by_source_line[source_line_no])
                    if not old_rank:
                        continue
                    pending.append((_source_record_id(json.loads(line)), old_rank))
                    if len(pending) >= 10000:
                        connection.executemany("INSERT INTO old_suffix VALUES (?, ?, NULL)", pending)
                        pending.clear()
                if pending:
                    connection.executemany("INSERT INTO old_suffix VALUES (?, ?, NULL)", pending)
            connection.commit()
            if len(frozen_source_ids) != len(frozen_source_lines):
                raise ValueError("Could not resolve every frozen document identity in the old source.")

            digest = hashlib.sha256()
            new_only_count = 0
            matched_count = 0
            physical_line_count = 0
            pending_matches: list[tuple[int, str]] = []
            with gzip.open(source_path, "rt", encoding="utf-8") as source, open(
                suffix_name, "wb"
            ) as new_only:
                for source_line_no, line in enumerate(source, start=1):
                    physical_line_count = source_line_no
                    digest.update(line.encode("utf-8"))
                    payload = json.loads(line)
                    text = payload.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    source_id = _source_record_id(payload)
                    if source_id in frozen_source_ids:
                        continue
                    row = connection.execute(
                        "SELECT old_rank FROM old_suffix WHERE source_id = ?", (source_id,)
                    ).fetchone()
                    if row is None:
                        new_only.write(struct.pack("<I", source_line_no))
                        new_only_count += 1
                        continue
                    pending_matches.append((source_line_no, source_id))
                    matched_count += 1
                    if len(pending_matches) >= 10000:
                        connection.executemany(
                            "UPDATE old_suffix SET new_line = ? WHERE source_id = ?",
                            pending_matches,
                        )
                        pending_matches.clear()
                if pending_matches:
                    connection.executemany(
                        "UPDATE old_suffix SET new_line = ? WHERE source_id = ?", pending_matches
                    )
                new_only.flush()
                os.fsync(new_only.fileno())
            connection.commit()

            with open(order_name, "wb") as output:
                self._write_uint32_chunk(output, old_order[:frozen_through_slot])
                chunk = array("I")
                for (new_line,) in connection.execute(
                    "SELECT new_line FROM old_suffix WHERE new_line IS NOT NULL ORDER BY old_rank"
                ):
                    chunk.append(int(new_line))
                    if len(chunk) >= 65536:
                        self._write_uint32_chunk(output, chunk)
                        chunk = array("I")
                if chunk:
                    self._write_uint32_chunk(output, chunk)
                with open(suffix_name, "rb") as new_only:
                    while data := new_only.read(1024 * 1024):
                        output.write(data)
                output.flush()
                os.fsync(output.fileno())

            os.replace(order_name, self.order_path)
            stat = source_path.stat()
            migrated_at = now_iso()
            document_count = frozen_through_slot + matched_count + new_only_count
            manifest = {
                **old_manifest,
                "dataset_name": dataset_name,
                "source_path": str(source_path),
                "source_size": stat.st_size,
                "source_mtime_ns": stat.st_mtime_ns,
                "source_content_sha256": digest.hexdigest(),
                "document_count": document_count,
                "cursor": frozen_through_slot + 1,
                "order_generation": int(old_manifest["order_generation"]) + 1,
                "reservation": None,
                "updated_at": migrated_at,
            }
            self._write_manifest(manifest)
            self._append_event(
                {
                    "event": "source_suffix_migrated",
                    "at": migrated_at,
                    "old_source_path": str(old_source_path),
                    "new_source_path": str(source_path),
                    "frozen_through_slot": frozen_through_slot,
                    "matched_suffix_documents": matched_count,
                    "new_suffix_documents": new_only_count,
                    "physical_source_lines": physical_line_count,
                    "document_count": document_count,
                    "cursor": manifest["cursor"],
                    "order_generation": manifest["order_generation"],
                }
            )
            return manifest
        finally:
            if connection is not None:
                connection.close()
            for temporary in (database_name, suffix_name, order_name):
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def _validate_source_identity(
        self,
        manifest: dict[str, Any],
        *,
        source_path: Path,
        dataset_name: str,
    ) -> None:
        resolved = Path(source_path).resolve()
        if str(manifest.get("source_path")) != str(resolved):
            raise ValueError("Processing order belongs to a different source path.")
        if str(manifest.get("dataset_name")) != dataset_name:
            raise ValueError("Processing order belongs to a different dataset.")
        stat = resolved.stat()
        if int(manifest.get("source_size") or -1) != stat.st_size:
            raise ValueError("Processing-order source size changed; rebuild the order.")
        if int(manifest.get("source_mtime_ns") or -1) != stat.st_mtime_ns:
            raise ValueError("Processing-order source timestamp changed; rebuild the order.")

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.track_name}.", suffix=".json.tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, self.manifest_path)
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise

    def _append_event(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_uint32_chunk(output: Any, values: array[int]) -> None:
        if values.itemsize != ENTRY_SIZE:
            raise RuntimeError("This platform does not provide 32-bit unsigned array entries.")
        if os.sys.byteorder != "little":
            values.byteswap()
        output.write(values.tobytes())

    @staticmethod
    def _ledger_by_slot(rows: Iterable[dict[str, Any]]) -> dict[int, int]:
        result: dict[int, int] = {}
        for row in rows:
            slot = int(row.get("track_doc_seq") or 0)
            source_line = int(row.get("source_line_no") or 0)
            if slot <= 0 or source_line <= 0:
                continue
            previous = result.get(slot)
            if previous is not None and previous != source_line:
                raise ValueError(f"Multiple source documents occupy processing slot {slot}.")
            result[slot] = source_line
        return result

    @staticmethod
    def reservation_assignments(reservation: dict[str, Any]) -> list[dict[str, int]]:
        start_slot = int(reservation["start_slot"])
        return [
            {"processing_slot": start_slot + offset, "source_line_no": int(source_line)}
            for offset, source_line in enumerate(reservation.get("source_line_nos", []))
        ]


def _source_record_id(payload: dict[str, Any]) -> str:
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("docId", "doc_id", "id"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("doc_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError("Source record does not contain a stable document identity.")
