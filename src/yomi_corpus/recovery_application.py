from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from yomi_corpus.recovery_documents import iter_jsonl
from yomi_corpus.yomi.token_codec import YomiTokenError, yomi_tokens_from_mapping


OUTCOME_FILES = (
    "units.yomi.final.jsonl",
    "units.yomi.skipped.jsonl",
    "units.yomi.excluded.jsonl",
)


class RecoveryApplicationError(RuntimeError):
    pass


def apply_recovery_campaign(
    *,
    root: Path,
    campaign_dir: Path,
    recovery_batch_name: str,
    apply: bool,
) -> dict[str, Any]:
    root = root.resolve()
    campaign_dir = campaign_dir.resolve()
    campaign = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))
    recovery_batch_dir = root / "data" / "units" / recovery_batch_name
    ledger_path = recovery_batch_dir / "recovery_application_ledger.jsonl"
    ledger = list(iter_jsonl(ledger_path))
    if not ledger:
        raise RecoveryApplicationError("Recovery application ledger is empty.")
    campaign_id = str(campaign.get("campaign_id") or "")
    ledger_campaign_ids = {str(row.get("campaign_id") or "") for row in ledger}
    if not campaign_id or ledger_campaign_ids != {campaign_id}:
        raise RecoveryApplicationError("Campaign manifest and application ledger do not match.")

    recovery_final_rows = {
        str(row.get("unit_id") or ""): row
        for row in iter_jsonl(recovery_batch_dir / "units.yomi.final.jsonl")
    }
    ready_rows = [row for row in ledger if row.get("state") in {"ready_to_apply", "applied"}]
    _validate_ledger_rows(ready_rows, recovery_final_rows)

    destination_ids = {str(row["destination_doc_id"]) for row in ready_rows}
    batch_by_doc = _find_destination_batches(root, destination_ids)
    missing = sorted(destination_ids - batch_by_doc.keys())
    if missing:
        raise RecoveryApplicationError(f"Finalized destinations were not found: {missing[:10]}")

    if ready_rows and all(row.get("state") == "applied" for row in ready_rows):
        _verify_applied_rows(root, ready_rows, batch_by_doc)
        return {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "status": "already_applied",
            "changed": False,
            "destination_documents": len(destination_ids),
            "applied_units": len(ready_rows),
            "skipped_units": sum(row.get("state") == "skipped" for row in ledger),
            "excluded_units": sum(row.get("state") == "excluded" for row in ledger),
        }
    if any(row.get("state") == "applied" for row in ready_rows):
        raise RecoveryApplicationError("Partially applied recovery ledgers require manual reconciliation.")

    source_rows = {
        unit_id: row
        for unit_id, row in recovery_final_rows.items()
        if unit_id in {str(item["final_unit_id"]) for item in ready_rows}
    }
    ready_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ready_rows:
        ready_by_doc[str(row["destination_doc_id"])].append(row)

    batch_plans: dict[str, dict[str, list[dict[str, Any]]]] = {}
    document_audits: list[dict[str, Any]] = []
    for batch_name in sorted(set(batch_by_doc.values())):
        batch_dir = root / "data" / "units" / batch_name
        files = {
            filename: (
                list(iter_jsonl(batch_dir / filename))
                if (batch_dir / filename).exists()
                else []
            )
            for filename in OUTCOME_FILES
        }
        original_rows = list(iter_jsonl(batch_dir / "units.jsonl"))
        target_docs = sorted(
            doc_id for doc_id, owner in batch_by_doc.items() if owner == batch_name
        )
        replacements: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for doc_id in target_docs:
            plan, audit = _plan_document_application(
                campaign_id=campaign_id,
                doc_id=doc_id,
                original_rows=[row for row in original_rows if str(row.get("doc_id")) == doc_id],
                outcome_rows={
                    filename: [
                        row for row in rows if str(row.get("doc_id")) == doc_id
                    ]
                    for filename, rows in files.items()
                },
                ledger_rows=ready_by_doc[doc_id],
                recovery_final_rows=source_rows,
            )
            replacements[doc_id] = plan
            document_audits.append({"batch_name": batch_name, **audit})

        batch_plans[batch_name] = {}
        for filename, rows in files.items():
            retained = [row for row in rows if str(row.get("doc_id")) not in replacements]
            for doc_id in target_docs:
                retained.extend(replacements[doc_id][filename])
            retained.sort(key=_row_order_key)
            batch_plans[batch_name][filename] = retained

    summary = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "status": "applied" if apply else "validated",
        "changed": bool(apply),
        "destination_documents": len(destination_ids),
        "destination_batches": len(batch_plans),
        "applied_units": len(ready_rows),
        "skipped_units": sum(row.get("state") == "skipped" for row in ledger),
        "excluded_units": sum(row.get("state") == "excluded" for row in ledger),
        "split_legacy_units": sum(int(row["split_legacy_units"]) for row in document_audits),
        "documents": document_audits,
    }
    if not apply:
        return summary

    for batch_name, files in batch_plans.items():
        batch_dir = root / "data" / "units" / batch_name
        for filename, rows in files.items():
            if not rows and not (batch_dir / filename).exists():
                continue
            _atomic_write_jsonl(batch_dir / filename, rows)

    audit_by_doc = {str(row["destination_doc_id"]): row for row in document_audits}
    for row in ledger:
        if row.get("state") != "ready_to_apply":
            continue
        audit = audit_by_doc[str(row["destination_doc_id"])]
        row["state"] = "applied"
        row["destination_batch_name"] = audit["batch_name"]
        row["destination_revision_before"] = audit["revision_before"]
        row["destination_revision_after"] = audit["revision_after"]
        row["applied_unit_id"] = str(row["recovery_unit_id"])
        row["review_submission_ids"] = _review_submission_ids(
            source_rows[str(row["final_unit_id"])]
        )
    _atomic_write_jsonl(ledger_path, ledger)
    summary_path = recovery_batch_dir / "recovery_application_summary.json"
    _atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    return summary


def _validate_ledger_rows(
    rows: Iterable[dict[str, Any]],
    recovery_final_rows: dict[str, dict[str, Any]],
) -> None:
    seen: set[str] = set()
    for row in rows:
        recovery_unit_id = str(row.get("recovery_unit_id") or "")
        if not recovery_unit_id or recovery_unit_id in seen:
            raise RecoveryApplicationError(f"Duplicate or empty recovery unit ID: {recovery_unit_id}")
        seen.add(recovery_unit_id)
        source = recovery_final_rows.get(str(row.get("final_unit_id") or ""))
        if source is None:
            raise RecoveryApplicationError(f"Final recovery unit is missing: {recovery_unit_id}")
        text = str(row.get("text") or "")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != row.get("text_sha256"):
            raise RecoveryApplicationError(f"Recovery text hash mismatch: {recovery_unit_id}")
        tokens = row.get("final_yomi_tokens")
        if not isinstance(tokens, list) or "".join(str(pair[0]) for pair in tokens) != text:
            raise RecoveryApplicationError(f"Recovery yomi does not preserve text: {recovery_unit_id}")
        canonical = _canonical_tokens(source)
        if canonical != tokens:
            raise RecoveryApplicationError(f"Recovery ledger yomi is stale: {recovery_unit_id}")


def _find_destination_batches(root: Path, destination_ids: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for state_path in sorted((root / "data" / "pipeline" / "batches").glob("*.json")):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("track_name") != "dev" or state.get("current_stage") != "yomi_finalized":
            continue
        if state.get("batch_kind") == "recovery":
            continue
        batch_name = str(state.get("batch_name") or state_path.stem)
        units_path = root / "data" / "units" / batch_name / "units.jsonl"
        if not units_path.exists():
            continue
        for row in iter_jsonl(units_path):
            doc_id = str(row.get("doc_id") or "")
            if doc_id not in destination_ids:
                continue
            previous = result.setdefault(doc_id, batch_name)
            if previous != batch_name:
                raise RecoveryApplicationError(
                    f"Destination occurs in multiple finalized batches: {doc_id}"
                )
        if len(result) == len(destination_ids):
            break
    return result


def _plan_document_application(
    *,
    campaign_id: str,
    doc_id: str,
    original_rows: list[dict[str, Any]],
    outcome_rows: dict[str, list[dict[str, Any]]],
    ledger_rows: list[dict[str, Any]],
    recovery_final_rows: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    original_rows.sort(key=_unit_order_key)
    if not original_rows:
        raise RecoveryApplicationError(f"Original destination units are missing: {doc_id}")
    original_text = "".join(str(row.get("text") or "") for row in original_rows)
    existing: dict[str, tuple[str, dict[str, Any]]] = {}
    for filename, rows in outcome_rows.items():
        for row in rows:
            unit_id = str(row.get("unit_id") or "")
            if unit_id in existing:
                raise RecoveryApplicationError(f"Duplicate finalized unit: {unit_id}")
            existing[unit_id] = (filename, row)
    original_ids = {str(row.get("unit_id") or "") for row in original_rows}
    extras = sorted(set(existing) - original_ids)
    if extras:
        raise RecoveryApplicationError(
            f"Destination already contains non-original units: {doc_id}: {extras[:5]}"
        )
    missing = sorted(original_ids - existing.keys())
    if missing:
        raise RecoveryApplicationError(f"Finalized destination units are missing: {doc_id}: {missing[:5]}")

    insertions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ledger_row in sorted(
        ledger_rows,
        key=lambda row: (int(row["new_char_start"]), str(row["recovery_unit_id"])),
    ):
        offset = _resolve_anchor_offset(original_text, ledger_row)
        source = recovery_final_rows[str(ledger_row["final_unit_id"])]
        insertions[offset].append(
            _destination_recovery_row(source, ledger_row, original_rows[0], campaign_id)
        )

    output: dict[str, list[dict[str, Any]]] = {filename: [] for filename in OUTCOME_FILES}
    ordered: list[tuple[str, dict[str, Any]]] = []
    split_count = 0
    cursor = 0
    consumed_offsets: set[int] = set()
    for original in original_rows:
        unit_id = str(original["unit_id"])
        filename, current = existing[unit_id]
        text = str(original.get("text") or "")
        start, end = cursor, cursor + len(text)
        cut_offsets = sorted(offset for offset in insertions if start < offset < end)
        if cut_offsets:
            if filename != "units.yomi.final.jsonl":
                raise RecoveryApplicationError(
                    f"Recovery insertion would split a non-final unit: {unit_id}"
                )
            pieces = _split_finalized_row(
                current,
                [offset - start for offset in cut_offsets],
                campaign_id=campaign_id,
            )
            split_count += 1
            for index, piece in enumerate(pieces):
                ordered.append((filename, piece))
                if index < len(cut_offsets):
                    offset = cut_offsets[index]
                    ordered.extend(("units.yomi.final.jsonl", row) for row in insertions[offset])
                    consumed_offsets.add(offset)
        else:
            if start in insertions and start not in consumed_offsets:
                ordered.extend(("units.yomi.final.jsonl", row) for row in insertions[start])
                consumed_offsets.add(start)
            ordered.append((filename, copy.deepcopy(current)))
        cursor = end
    if cursor in insertions and cursor not in consumed_offsets:
        ordered.extend(("units.yomi.final.jsonl", row) for row in insertions[cursor])
        consumed_offsets.add(cursor)
    if consumed_offsets != set(insertions):
        raise RecoveryApplicationError(f"Not all insertion offsets were consumed for {doc_id}.")

    char_cursor = 0
    for unit_seq, (filename, row) in enumerate(ordered, start=1):
        row["unit_seq"] = unit_seq
        row["char_start"] = char_cursor
        char_cursor += len(str(row.get("text") or ""))
        row["char_end"] = char_cursor
        output[filename].append(row)
    before = _document_revision(
        row for filename in OUTCOME_FILES for row in outcome_rows[filename]
    )
    after = _document_revision(row for filename in OUTCOME_FILES for row in output[filename])
    return output, {
        "destination_doc_id": doc_id,
        "destination_track_doc_seq": int(original_rows[0].get("track_doc_seq") or 0),
        "revision_before": before,
        "revision_after": after,
        "inserted_units": len(ledger_rows),
        "split_legacy_units": split_count,
    }


def _resolve_anchor_offset(text: str, row: dict[str, Any]) -> int:
    preceding = row.get("preceding_anchor")
    following = row.get("following_anchor")
    candidates = []
    for offset in range(len(text) + 1):
        if isinstance(preceding, dict) and not text[:offset].endswith(str(preceding.get("text") or "")):
            continue
        if isinstance(following, dict) and not text[offset:].startswith(str(following.get("text") or "")):
            continue
        candidates.append(offset)
    if len(candidates) != 1:
        raise RecoveryApplicationError(
            f"Recovery anchors are not unique for {row.get('recovery_unit_id')}: {candidates[:10]}"
        )
    return candidates[0]


def _destination_recovery_row(
    source: dict[str, Any],
    ledger_row: dict[str, Any],
    original_reference: dict[str, Any],
    campaign_id: str,
) -> dict[str, Any]:
    row = copy.deepcopy(source)
    row["unit_id"] = str(ledger_row["recovery_unit_id"])
    row["doc_id"] = str(ledger_row["destination_doc_id"])
    row["track_doc_seq"] = int(ledger_row["destination_track_doc_seq"])
    row["source_file"] = original_reference.get("source_file")
    row["source_line_no"] = int(ledger_row["destination_source_line_no"])
    row.setdefault("analysis", {})["recovery_application"] = {
        "campaign_id": campaign_id,
        "recovery_unit_id": str(ledger_row["recovery_unit_id"]),
        "source_review_unit_id": str(ledger_row["final_unit_id"]),
    }
    row["analysis"]["mechanical"]["yomi"] = {
        "token_schema_version": 1,
        "tokens": copy.deepcopy(ledger_row["final_yomi_tokens"]),
    }
    return row


def _split_finalized_row(
    row: dict[str, Any],
    offsets: list[int],
    *,
    campaign_id: str,
) -> list[dict[str, Any]]:
    text = str(row.get("text") or "")
    tokens = _canonical_tokens(row)
    token_boundaries = {0}
    cursor = 0
    for surface, _reading in tokens:
        cursor += len(surface)
        token_boundaries.add(cursor)
    if cursor != len(text) or any(offset not in token_boundaries for offset in offsets):
        raise RecoveryApplicationError(f"Legacy unit cannot be split safely: {row.get('unit_id')}")
    human = row.get("analysis", {}).get("human_review", {})
    if isinstance(human, dict) and human.get("finalized_corrections"):
        raise RecoveryApplicationError(
            f"Legacy unit with finalized corrections cannot be split automatically: {row.get('unit_id')}"
        )
    llm = row.get("analysis", {}).get("llm", {})
    if isinstance(llm, dict) and llm.get("yomi_strong_repair"):
        raise RecoveryApplicationError(
            f"Legacy unit with strong repair evidence cannot be split automatically: {row.get('unit_id')}"
        )

    pieces: list[dict[str, Any]] = []
    starts = [0, *offsets]
    ends = [*offsets, len(text)]
    token_cursor = 0
    original_id = str(row.get("unit_id") or "")
    for index, (start, end) in enumerate(zip(starts, ends)):
        piece_tokens = []
        while token_cursor < len(tokens):
            surface, reading = tokens[token_cursor]
            token_end = sum(len(item[0]) for item in tokens[: token_cursor + 1])
            if token_end > end:
                break
            piece_tokens.append([surface, reading])
            token_cursor += 1
        piece = copy.deepcopy(row)
        piece["unit_id"] = original_id if index == 0 else f"{original_id}:split:{campaign_id}:{index}"
        piece["text"] = text[start:end]
        piece.setdefault("analysis", {})["mechanical"]["yomi"] = {
            "token_schema_version": 1,
            "tokens": piece_tokens,
        }
        piece["analysis"]["recovery_application_split"] = {
            "campaign_id": campaign_id,
            "original_unit_id": original_id,
            "piece_index": index,
            "piece_count": len(starts),
        }
        review = piece.get("analysis", {}).get("human_review", {}).get("yomi_final")
        if isinstance(review, dict):
            review["item_id"] = piece["unit_id"]
        pieces.append(piece)
    return pieces


def _canonical_tokens(row: dict[str, Any]) -> list[list[str]]:
    yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi")
    if not isinstance(yomi, dict):
        raise RecoveryApplicationError(f"Canonical yomi is missing: {row.get('unit_id')}")
    try:
        return yomi_tokens_from_mapping(yomi, text=str(row.get("text") or ""))
    except YomiTokenError as exc:
        raise RecoveryApplicationError(f"Canonical yomi is invalid: {row.get('unit_id')}") from exc


def _review_submission_ids(row: dict[str, Any]) -> list[str]:
    result: set[str] = set()
    human = row.get("analysis", {}).get("human_review", {})
    if isinstance(human, dict):
        final = human.get("yomi_final")
        if isinstance(final, dict) and final.get("submission_id"):
            result.add(str(final["submission_id"]))
        corrections = human.get("finalized_corrections")
        if isinstance(corrections, list):
            result.update(
                str(item["submission_id"])
                for item in corrections
                if isinstance(item, dict) and item.get("submission_id")
            )
    return sorted(result)


def _verify_applied_rows(
    root: Path,
    rows: list[dict[str, Any]],
    batch_by_doc: dict[str, str],
) -> None:
    by_batch: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_batch[batch_by_doc[str(row["destination_doc_id"])]].add(str(row["applied_unit_id"]))
    for batch_name, wanted in by_batch.items():
        present = {
            str(row.get("unit_id") or "")
            for row in iter_jsonl(root / "data" / "units" / batch_name / "units.yomi.final.jsonl")
        }
        missing = sorted(wanted - present)
        if missing:
            raise RecoveryApplicationError(f"Applied recovery units are missing: {missing[:10]}")


def _document_revision(rows: Iterable[dict[str, Any]]) -> str:
    payload = [
        {
            "unit_id": str(row.get("unit_id") or ""),
            "unit_seq": int(row.get("unit_seq") or 0),
            "text": str(row.get("text") or ""),
            "tokens": _canonical_tokens(row) if row.get("text") else [],
            "excluded": bool(row.get("excluded")),
        }
        for row in sorted(rows, key=_unit_order_key)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _unit_order_key(row: dict[str, Any]) -> tuple[int, str]:
    return int(row.get("unit_seq") or 0), str(row.get("unit_id") or "")


def _row_order_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(row.get("track_doc_seq") or 0),
        int(row.get("unit_seq") or 0),
        str(row.get("unit_id") or ""),
    )


def _atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
