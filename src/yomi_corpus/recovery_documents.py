from __future__ import annotations

import difflib
import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from yomi_corpus.splitter import split_text_into_units


DEFAULT_TARGET_CHARS = 900
DEFAULT_MIN_CHARS = 600
DEFAULT_MAX_UNITS = 32


@dataclass(frozen=True)
class RestoredChunk:
    old_start: int
    new_start: int
    new_end: int
    text: str


def source_record_id(record: dict[str, Any]) -> str:
    meta = record.get("meta")
    value = meta.get("docId") if isinstance(meta, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Source record does not contain a stable meta.docId.")
    return value.strip()


def insertion_only_diff(old_text: str, new_text: str) -> list[RestoredChunk]:
    old_segments = _cleaner_segments(old_text)
    if "".join(old_segments) != old_text or "".join(_cleaner_segments(new_text)) != new_text:
        raise ValueError("Cleaner text contains unsupported inter-segment whitespace.")
    try:
        left_positions = _match_segment_positions(old_segments, new_text, reverse=False)
        right_positions = _match_segment_positions(old_segments, new_text, reverse=True)
    except ValueError:
        return _character_insertion_diff(old_text, new_text)
    if left_positions != right_positions:
        return _character_insertion_diff(old_text, new_text)
    chunks: list[RestoredChunk] = []
    old_offset = 0
    new_offset = 0
    for segment, new_start in zip(old_segments, left_positions):
        text = new_text[new_offset:new_start]
        if text:
            chunks.append(
                RestoredChunk(
                    old_start=old_offset,
                    new_start=new_offset,
                    new_end=new_start,
                    text=text,
                )
            )
        old_offset += len(segment)
        new_offset = new_start + len(segment)
    tail = new_text[new_offset:]
    if tail:
        chunks.append(
            RestoredChunk(
                old_start=old_offset,
                new_start=new_offset,
                new_end=len(new_text),
                text=tail,
            )
        )
    rebuilt = old_text
    for chunk in reversed(chunks):
        rebuilt = rebuilt[: chunk.old_start] + chunk.text + rebuilt[chunk.old_start :]
    if rebuilt != new_text:
        raise ValueError("Insertion-only diff did not reconstruct regenerated text exactly.")
    return chunks


def build_recovery_units(
    *,
    campaign_id: str,
    old_record: dict[str, Any],
    new_record: dict[str, Any],
    destination: dict[str, Any],
    restored_chunks: list[RestoredChunk] | None = None,
) -> list[dict[str, Any]]:
    stable_id = source_record_id(old_record)
    if source_record_id(new_record) != stable_id:
        raise ValueError("Old and regenerated source identities differ.")
    old_text = str(old_record.get("text") or "")
    new_text = str(new_record.get("text") or "")
    units: list[dict[str, Any]] = []
    chunks = restored_chunks if restored_chunks is not None else insertion_only_diff(old_text, new_text)
    if restored_chunks is not None:
        validate_restored_chunks(old_text, new_text, chunks)
        positioned: list[RestoredChunk] = []
        inserted_chars = 0
        for chunk in chunks:
            new_start = chunk.old_start + inserted_chars
            positioned.append(
                RestoredChunk(
                    old_start=chunk.old_start,
                    new_start=new_start,
                    new_end=new_start + len(chunk.text),
                    text=chunk.text,
                )
            )
            inserted_chars += len(chunk.text)
        chunks = positioned
    for chunk_seq, chunk in enumerate(chunks, start=1):
        spans = split_text_into_units(chunk.text)
        for span_seq, span in enumerate(spans, start=1):
            text_hash = hashlib.sha256(span.text.encode("utf-8")).hexdigest()
            recovery_unit_id = (
                f"recovery:{campaign_id}:{stable_id}:"
                f"{chunk.new_start + span.start:010d}:{text_hash[:16]}"
            )
            old_before = old_text[: chunk.old_start]
            old_after = old_text[chunk.old_start :]
            units.append(
                {
                    "schema_version": 1,
                    "campaign_id": campaign_id,
                    "recovery_unit_id": recovery_unit_id,
                    "source_record_id": stable_id,
                    "destination_doc_id": str(destination["doc_id"]),
                    "destination_track_doc_seq": int(destination["track_doc_seq"]),
                    "destination_source_line_no": int(destination["source_line_no"]),
                    "chunk_seq": chunk_seq,
                    "span_seq": span_seq,
                    "new_char_start": chunk.new_start + span.start,
                    "new_char_end": chunk.new_start + span.end,
                    "text": span.text,
                    "text_sha256": text_hash,
                    "preceding_anchor": _anchor(old_before, from_end=True),
                    "following_anchor": _anchor(old_after, from_end=False),
                    "state": "pending",
                }
            )
    return units


def validate_restored_chunks(
    old_text: str,
    new_text: str,
    chunks: list[RestoredChunk],
) -> None:
    previous = -1
    rebuilt = old_text
    for chunk in reversed(chunks):
        if chunk.old_start < 0 or chunk.old_start > len(old_text):
            raise ValueError("Recovery override insertion offset is outside old text.")
        rebuilt = rebuilt[: chunk.old_start] + chunk.text + rebuilt[chunk.old_start :]
    for chunk in chunks:
        if chunk.old_start < previous:
            raise ValueError("Recovery override insertions are not in source order.")
        previous = chunk.old_start
    if rebuilt != new_text:
        raise ValueError("Recovery override does not reconstruct regenerated text exactly.")


def pack_recovery_units(
    units: Iterable[dict[str, Any]],
    *,
    campaign_id: str,
    target_chars: int = DEFAULT_TARGET_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_units: int = DEFAULT_MAX_UNITS,
) -> list[dict[str, Any]]:
    if min_chars <= 0 or target_chars < min_chars or max_units <= 0:
        raise ValueError("Invalid recovery document packing bounds.")
    ordered = sorted(
        units,
        key=lambda row: (
            int(row["destination_track_doc_seq"]),
            int(row["new_char_start"]),
            str(row["recovery_unit_id"]),
        ),
    )
    packed: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for unit in ordered:
        unit_chars = len(str(unit["text"]))
        crosses_target = current_chars >= min_chars and current_chars + unit_chars > target_chars
        reaches_unit_limit = len(current) >= max_units
        if current and (crosses_target or reaches_unit_limit):
            packed.append(current)
            current = []
            current_chars = 0
        current.append(unit)
        current_chars += unit_chars
    if current:
        packed.append(current)

    documents: list[dict[str, Any]] = []
    for document_seq, rows in enumerate(packed, start=1):
        documents.append(
            {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "recovery_document_id": f"recovery:{campaign_id}:d{document_seq:06d}",
                "recovery_document_seq": document_seq,
                "character_count": sum(len(str(row["text"])) for row in rows),
                "unit_count": len(rows),
                "recovery_unit_ids": [str(row["recovery_unit_id"]) for row in rows],
            }
        )
    return documents


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}.")
            yield value


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_application_ledger(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for row in rows:
        recovery = row.get("recovery")
        if not isinstance(recovery, dict):
            raise ValueError(f"Final recovery row lacks provenance: {row.get('unit_id')}")
        review = (
            row.get("analysis", {})
            .get("human_review", {})
            .get("yomi_final", {})
        )
        excluded = bool(row.get("excluded"))
        skipped = bool(isinstance(review, dict) and review.get("skip"))
        state = "excluded" if excluded else "skipped" if skipped else "ready_to_apply"
        ledger.append(
            {
                "schema_version": 1,
                "campaign_id": recovery["campaign_id"],
                "recovery_unit_id": recovery["recovery_unit_id"],
                "destination_doc_id": recovery["destination_doc_id"],
                "destination_track_doc_seq": recovery["destination_track_doc_seq"],
                "destination_source_line_no": recovery["destination_source_line_no"],
                "new_char_start": recovery["new_char_start"],
                "new_char_end": recovery["new_char_end"],
                "text": recovery["text"],
                "text_sha256": recovery["text_sha256"],
                "preceding_anchor": recovery.get("preceding_anchor"),
                "following_anchor": recovery.get("following_anchor"),
                "state": state,
                "final_unit_id": row.get("unit_id"),
                "final_yomi_tokens": row.get("final_yomi_tokens", []),
            }
        )
    return ledger


def _anchor(text: str, *, from_end: bool, width: int = 80) -> dict[str, str] | None:
    excerpt = text[-width:] if from_end else text[:width]
    if not excerpt:
        return None
    return {
        "text": excerpt,
        "sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    }


def _cleaner_segments(text: str) -> list[str]:
    segments: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        start = 0
        for match in re.finditer(r"[。！？!?]+", line):
            end = match.end()
            segment = line[start:end].strip()
            if segment:
                segments.append(segment)
            start = end
        tail = line[start:].strip()
        if tail:
            segments.append(tail)
    return segments


def _match_segment_positions(
    segments: list[str],
    text: str,
    *,
    reverse: bool,
) -> list[int]:
    if not reverse:
        positions: list[int] = []
        cursor = 0
        for segment in segments:
            position = text.find(segment, cursor)
            if position < 0:
                raise ValueError("Regenerated text is not an insertion-only change.")
            positions.append(position)
            cursor = position + len(segment)
        return positions

    positions = [0] * len(segments)
    cursor = len(text)
    for index in range(len(segments) - 1, -1, -1):
        segment = segments[index]
        position = text.rfind(segment, 0, cursor)
        if position < 0:
            raise ValueError("Regenerated text is not an insertion-only change.")
        positions[index] = position
        cursor = position
    return positions


def _character_insertion_diff(old_text: str, new_text: str) -> list[RestoredChunk]:
    matcher = difflib.SequenceMatcher(a=old_text, b=new_text, autojunk=False)
    chunks: list[RestoredChunk] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "insert":
            raise ValueError("Regenerated text is not an insertion-only change.")
        candidates = _shifted_insertions(
            old_text=old_text,
            new_text=new_text,
            old_start=old_start,
            new_start=new_start,
            new_end=new_end,
        )
        best_score = max(_insertion_boundary_score(old_text, candidate) for candidate in candidates)
        best = [
            candidate
            for candidate in candidates
            if _insertion_boundary_score(old_text, candidate) == best_score
        ]
        identities = {(row.old_start, row.text) for row in best}
        if len(identities) != 1:
            raise ValueError("Regenerated text has ambiguous insertion anchors.")
        chunks.append(best[0])
    rebuilt = old_text
    for chunk in reversed(chunks):
        rebuilt = rebuilt[: chunk.old_start] + chunk.text + rebuilt[chunk.old_start :]
    if rebuilt != new_text:
        raise ValueError("Character insertion diff did not reconstruct regenerated text exactly.")
    return chunks


def _shifted_insertions(
    *,
    old_text: str,
    new_text: str,
    old_start: int,
    new_start: int,
    new_end: int,
) -> list[RestoredChunk]:
    insertion_length = new_end - new_start
    candidates: list[RestoredChunk] = []
    left = old_start
    while left > 0 and new_start > 0 and old_text[left - 1] == new_text[new_start - 1]:
        left -= 1
        new_start -= 1
        new_end -= 1
    position = left
    start = new_start
    end = new_end
    while True:
        candidates.append(
            RestoredChunk(
                old_start=position,
                new_start=start,
                new_end=end,
                text=new_text[start:end],
            )
        )
        if position >= len(old_text) or end >= len(new_text):
            break
        if old_text[position] != new_text[end]:
            break
        position += 1
        start += 1
        end += 1
    if any(len(row.text) != insertion_length for row in candidates):
        raise AssertionError("Shifted insertion changed length.")
    return candidates


def _insertion_boundary_score(old_text: str, chunk: RestoredChunk) -> tuple[int, int]:
    sentence_ends = "。！？!?"
    starts_at_boundary = chunk.old_start == 0 or old_text[chunk.old_start - 1] in sentence_ends
    ends_at_boundary = bool(chunk.text) and chunk.text[-1] in sentence_ends
    starts_with_boundary = bool(chunk.text) and chunk.text[0] in sentence_ends
    return (
        int(starts_at_boundary) + int(ends_at_boundary) - int(starts_with_boundary),
        int(ends_at_boundary),
    )
