from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import json
import re
import shutil
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import DEV_TRACK, WORKING_TRACK
from yomi_corpus.yomi.final_review import (
    manual_correction_required,
    manual_correction_state,
    yomi_tokens_ruby_tokens,
)
from yomi_corpus.yomi.numeric_surfaces import (
    allows_optional_japanese_numeral_reading,
    is_numeric_only_surface,
)
from yomi_corpus.yomi.token_codec import (
    YomiTokenError,
    editable_rendered_to_yomi_tokens,
    legacy_rendered_to_yomi_tokens,
    yomi_tokens_from_mapping,
    yomi_tokens_to_editable_rendered,
)


# Corpus Map loads summaries from the index and fetches one document on demand.
ARCHIVE_SHARD_SIZE = 1
_ACTIVE_REVIEW_PUBLISH_LOCKS: set[Path] = set()


@contextmanager
def review_site_publish_lock(docs_dir: str | Path):
    """Serialize generation and publication of the shared review tree."""
    docs_root = Path(docs_dir).resolve()
    lock_path = docs_root.parent / "data" / "state" / "review_site" / "publish.lock"
    if lock_path in _ACTIVE_REVIEW_PUBLISH_LOCKS:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _ACTIVE_REVIEW_PUBLISH_LOCKS.add(lock_path)
        try:
            yield
        finally:
            _ACTIVE_REVIEW_PUBLISH_LOCKS.discard(lock_path)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_review_pack(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_review_pack_entries(
    review_pack_root: str | Path,
    *,
    finalized_document_keys: set[tuple[int, str]] | None = None,
) -> list[dict]:
    root = Path(review_pack_root)
    entries: list[dict] = []
    if not root.exists():
        return entries

    for path in sorted(root.rglob("*.json")):
        payload = load_review_pack(path)
        track_name = infer_track_name(payload, path)
        if track_name != DEV_TRACK:
            continue
        pack_id = str(payload["pack_id"])
        title = build_pack_title(payload, path)
        entry = {
            "pack_id": pack_id,
            "title": title,
            "review_stage": str(payload["review_stage"]),
            "queue_id": str(payload.get("queue_id") or payload["review_stage"]),
            "track_name": track_name,
            "batch_name": str(payload.get("batch_name") or ""),
            "created_at_epoch": int(payload.get("created_at_epoch", 0)),
            "item_count": int(payload.get("item_count", len(payload.get("items", [])))),
            "document_count": int(
                payload.get("summary", {}).get(
                    "document_count", len(payload.get("documents", []))
                )
            ),
            "selectable_document_count": int(
                payload.get("summary", {}).get("selectable_document_count", 0)
            ),
            "document_state_counts": payload.get("summary", {}).get(
                "document_state_counts", {}
            ),
            "source_path": path,
            "site_filename": f"{pack_id}.json",
        }
        if finalized_document_keys is not None:
            pending_doc_ids = []
            for doc in payload.get("documents", []):
                doc_id = str(doc.get("doc_id") or "")
                try:
                    track_doc_seq = int(doc.get("track_doc_seq") or doc.get("doc_seq") or 0)
                except (TypeError, ValueError):
                    continue
                if (
                    doc_id
                    and (track_doc_seq, doc_id) not in finalized_document_keys
                    and document_belongs_to_pending_pack(doc, entry["review_stage"])
                ):
                    pending_doc_ids.append(doc_id)
            entry["pending_doc_ids"] = pending_doc_ids
            entry["pending_document_count"] = len(pending_doc_ids)
        entries.append(entry)
    return entries


def document_belongs_to_pending_pack(doc: dict, review_stage: str) -> bool:
    workflow_queue_stage = str(doc.get("workflow_queue_stage") or "")
    if workflow_queue_stage:
        return workflow_queue_stage == review_stage
    has_strong_repairs = (
        int(doc.get("strong_repair_item_count") or 0) > 0
        or (review_stage == "yomi_strong_repair_review" and int(doc.get("item_count") or 0) > 0)
    )
    if review_stage == "yomi_strong_repair_review":
        return has_strong_repairs
    if review_stage == "yomi_final_review":
        return not has_strong_repairs
    return False


def build_review_manifest(entries: list[dict]) -> dict:
    stages: dict[str, dict] = {}
    for entry in entries:
        stage_id = entry["review_stage"]
        stage_bucket = stages.setdefault(
            stage_id,
            {
                "review_stage": stage_id,
                "label": humanize_stage_label(stage_id),
                "latest_pack_id": None,
                "latest_pack_ids_by_track": {},
                "packs": [],
            },
        )
        stage_bucket["packs"].append(
            {
                "pack_id": entry["pack_id"],
                "title": entry["title"],
                "path": f"./packs/{entry['site_filename']}",
                "track_name": entry.get("track_name", WORKING_TRACK),
                "batch_name": entry.get("batch_name", ""),
                "created_at_epoch": entry["created_at_epoch"],
                "item_count": entry["item_count"],
                "document_count": int(entry.get("document_count", 0)),
                "selectable_document_count": int(entry.get("selectable_document_count", 0)),
                "document_state_counts": entry.get("document_state_counts", {}),
                "pending_doc_ids": entry.get("pending_doc_ids"),
                "pending_document_count": entry.get("pending_document_count"),
                "queue_id": entry.get("queue_id", stage_id),
                "status": "archived",
            }
        )

    ordered_stage_ids = sorted(stages)
    current_tracks: dict[str, dict] = {}
    for stage_id in ordered_stage_ids:
        packs = stages[stage_id]["packs"]
        packs.sort(key=lambda row: (row["created_at_epoch"], row["pack_id"]))
        latest_by_track: dict[str, dict] = {}
        for pack in packs:
            latest_by_track[pack["track_name"]] = pack
        if latest_by_track:
            stages[stage_id]["latest_pack_ids_by_track"] = {
                track_name: pack["pack_id"] for track_name, pack in sorted(latest_by_track.items())
            }
            default_pack = latest_by_track.get(WORKING_TRACK)
            if default_pack is None:
                default_pack = max(packs, key=lambda row: (row["created_at_epoch"], row["pack_id"]))
            stages[stage_id]["latest_pack_id"] = default_pack["pack_id"]
            for pack in packs:
                if (
                    pack["track_name"] == WORKING_TRACK
                    and WORKING_TRACK in latest_by_track
                    and pack["pack_id"] == latest_by_track[WORKING_TRACK]["pack_id"]
                ):
                    pack["status"] = "active-working"
                elif (
                    pack["track_name"] == DEV_TRACK
                    and DEV_TRACK in latest_by_track
                    and pack["pack_id"] == latest_by_track[DEV_TRACK]["pack_id"]
                ):
                    pack["status"] = "active-dev"
            for track_name, pack in latest_by_track.items():
                current = current_tracks.get(track_name)
                if current is None or (pack["created_at_epoch"], pack["pack_id"]) > (
                    current["created_at_epoch"],
                    current["pack_id"],
                ):
                    current_tracks[track_name] = {
                        "track_name": track_name,
                        "review_stage": stage_id,
                        "label": stages[stage_id]["label"],
                        "pack_id": pack["pack_id"],
                        "title": pack["title"],
                        "path": pack["path"],
                        "created_at_epoch": pack["created_at_epoch"],
                        "item_count": pack["item_count"],
                    }

    default_current = latest_current_track(current_tracks)
    current_queues = build_current_review_queues(stages)
    return {
        "schema_version": 1,
        "default_stage": (
            default_current["review_stage"]
            if default_current is not None
            else ordered_stage_ids[0] if ordered_stage_ids else None
        ),
        "current_tracks": current_tracks,
        "current_review_queues": current_queues,
        "stages": {stage_id: stages[stage_id] for stage_id in ordered_stage_ids},
    }


def build_current_review_queues(stages: dict[str, dict]) -> list[dict]:
    queues: list[dict] = []
    preferred_stage_order = {
        "yomi_final_review": 0,
        "yomi_strong_repair_review": 1,
    }
    for stage_id, stage in stages.items():
        latest_by_batch: dict[str, dict] = {}
        for pack in stage.get("packs", []):
            if pack.get("track_name") != DEV_TRACK:
                continue
            batch_key = str(pack.get("batch_name") or pack.get("pack_id") or "")
            current = latest_by_batch.get(batch_key)
            if current is None or (
                int(pack.get("created_at_epoch") or 0),
                str(pack.get("pack_id") or ""),
            ) > (
                int(current.get("created_at_epoch") or 0),
                str(current.get("pack_id") or ""),
            ):
                latest_by_batch[batch_key] = pack
        for pack in latest_by_batch.values():
            if pack.get("pending_document_count") is not None:
                has_working_documents = int(pack["pending_document_count"]) > 0
            else:
                has_working_documents = int(pack.get("selectable_document_count") or 0) > 0
            if not has_working_documents:
                continue
            queues.append(
                {
                    "track_name": DEV_TRACK,
                    "batch_name": pack.get("batch_name", ""),
                    "review_stage": stage_id,
                    "label": stage.get("label", stage_id),
                    "pack_id": pack["pack_id"],
                    "title": pack["title"],
                    "path": pack["path"],
                    "created_at_epoch": pack["created_at_epoch"],
                    "item_count": pack["item_count"],
                    "document_count": pack.get("document_count", 0),
                    "selectable_document_count": pack.get("selectable_document_count", 0),
                    "document_state_counts": pack.get("document_state_counts", {}),
                    "pending_doc_ids": pack.get("pending_doc_ids"),
                    "pending_document_count": pack.get("pending_document_count"),
                    "queue_id": pack.get("queue_id", stage_id),
                    "status": "active-dev",
                }
            )
    queues.sort(
        key=lambda row: (
            preferred_stage_order.get(str(row.get("review_stage")), 99),
            int(row.get("created_at_epoch") or 0),
            str(row.get("pack_id", "")),
        )
    )
    return queues


def latest_current_track(current_tracks: dict[str, dict]) -> dict | None:
    if not current_tracks:
        return None
    return max(
        current_tracks.values(),
        key=lambda row: (
            int(row.get("created_at_epoch", 0)),
            1 if row.get("track_name") == WORKING_TRACK else 0,
            str(row.get("pack_id", "")),
        ),
    )


def publish_review_site(
    *,
    web_review_dir: str | Path,
    docs_dir: str | Path,
    review_pack_root: str | Path,
    project_root: str | Path | None = None,
) -> dict:
    with review_site_publish_lock(docs_dir):
        return _publish_review_site_unlocked(
            web_review_dir=web_review_dir,
            docs_dir=docs_dir,
            review_pack_root=review_pack_root,
            project_root=project_root,
        )


def publish_issue_acknowledgments(
    *,
    docs_dir: str | Path,
    acknowledgment_path: str | Path,
) -> dict[str, Any]:
    """Publish watcher state without regenerating packs or the archive."""
    docs_root = Path(docs_dir)
    source = Path(acknowledgment_path)
    review_dir = docs_root / "review"
    manifest_path = review_dir / "manifest.json"
    with review_site_publish_lock(docs_root):
        if not manifest_path.exists():
            raise FileNotFoundError(
                "Review manifest is missing; run ./publish-review before enabling issue-watch."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        destination = review_dir / "issue-acknowledgments.json"
        shutil.copy2(source, destination)
        manifest["issue_acknowledgments"] = {
            "path": "./issue-acknowledgments.json"
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "status": "generated",
        "manifest_json": str(manifest_path),
        "acknowledgment_json": str(destination),
    }


def issue_acknowledgments_need_publish(
    *,
    docs_dir: str | Path,
    acknowledgment_path: str | Path,
) -> bool:
    review_dir = Path(docs_dir) / "review"
    source = Path(acknowledgment_path)
    destination = review_dir / "issue-acknowledgments.json"
    if _file_fingerprint(source) != _file_fingerprint(destination):
        return True
    manifest_path = review_dir / "manifest.json"
    if not manifest_path.exists():
        return True
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return manifest.get("issue_acknowledgments", {}).get("path") != "./issue-acknowledgments.json"


def _file_fingerprint(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _publish_review_site_unlocked(
    *,
    web_review_dir: str | Path,
    docs_dir: str | Path,
    review_pack_root: str | Path,
    project_root: str | Path | None = None,
) -> dict:
    web_root = Path(web_review_dir)
    docs_root = Path(docs_dir)
    review_root = Path(review_pack_root)

    review_output_dir = docs_root / "review"
    pack_output_dir = review_output_dir / "packs"

    clear_directory(review_output_dir)
    review_output_dir.mkdir(parents=True, exist_ok=True)
    pack_output_dir.mkdir(parents=True, exist_ok=True)

    sync_directory(web_root, review_output_dir)
    rewrite_index_asset_versions(review_output_dir)
    write_root_redirect(docs_root / "index.html")

    finalized_document_keys = None
    if project_root is not None:
        finalized_document_keys = {
            (int(doc["track_doc_seq"]), str(doc.get("doc_id") or ""))
            for doc in collect_finalized_archive_documents(Path(project_root), DEV_TRACK)
        }
    entries = collect_review_pack_entries(
        review_root,
        finalized_document_keys=finalized_document_keys,
    )
    manifest = build_review_manifest(entries)
    if project_root is not None:
        runtime_source = (
            Path(project_root)
            / "data"
            / "state"
            / "review_sync"
            / "dev.runtime_status.json"
        )
        if runtime_source.exists():
            shutil.copy2(runtime_source, review_output_dir / "runtime-status.json")
            manifest["runtime_status"] = {"path": "./runtime-status.json"}
        acknowledgment_source = (
            Path(project_root)
            / "data"
            / "state"
            / "issue_watch"
            / "dev.acknowledgments.json"
        )
        if acknowledgment_source.exists():
            shutil.copy2(
                acknowledgment_source,
                review_output_dir / "issue-acknowledgments.json",
            )
            manifest["issue_acknowledgments"] = {
                "path": "./issue-acknowledgments.json"
            }
        archive_manifest = publish_review_archive(
            project_root=project_root,
            review_output_dir=review_output_dir,
            review_pack_entries=entries,
        )
        manifest["archive"] = archive_manifest

    for entry in entries:
        shutil.copy2(entry["source_path"], pack_output_dir / entry["site_filename"])

    (review_output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def publish_review_archive(
    *,
    project_root: str | Path,
    review_output_dir: str | Path,
    shard_size: int = ARCHIVE_SHARD_SIZE,
    review_pack_entries: list[dict] | None = None,
) -> dict:
    root = Path(project_root)
    output_root = Path(review_output_dir) / "archive"
    clear_directory(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    tracks: dict[str, dict[str, Any]] = {}
    for track_name in [DEV_TRACK]:
        documents = collect_finalized_archive_documents(root, track_name)
        track_dir = output_root / track_name
        track_dir.mkdir(parents=True, exist_ok=True)
        shards = write_archive_shards(
            documents,
            output_root=track_dir,
            url_prefix=f"./archive/{track_name}",
            shard_size=shard_size,
        )
        search_path = write_archive_search_index(
            documents,
            shards=shards,
            output_root=track_dir,
            url_prefix=f"./archive/{track_name}",
            supplemental_records=collect_pending_review_search_records(
                review_pack_entries or [],
                track_name=track_name,
                finalized_documents=documents,
            ),
        )
        document_summaries = [
            archive_document_summary(doc, shards=shards) for doc in documents
        ]
        tracks[track_name] = {
            "track_name": track_name,
            "document_count": len(documents),
            "finalized_track_doc_seq_ranges": compact_integer_ranges(
                int(doc["track_doc_seq"]) for doc in documents
            ),
            "manual_correction_required_count": sum(
                int(doc.get("manual_correction_required_count") or 0)
                for doc in documents
            ),
            "shard_size": shard_size,
            "shards": shards,
            "documents": document_summaries,
            "search_path": search_path,
        }

    index = {
        "schema_version": 1,
        "description": "Finalized read-only review archive. Active work queues are published separately.",
        "tracks": tracks,
    }
    (output_root / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "index_path": "./archive/index.json",
        "tracks": {
            track_name: {
                "document_count": track["document_count"],
                "manual_correction_required_count": track[
                    "manual_correction_required_count"
                ],
                "finalized_track_doc_seq_ranges": track[
                    "finalized_track_doc_seq_ranges"
                ],
                "shard_count": len(track["shards"]),
            }
            for track_name, track in tracks.items()
        },
    }


def compact_integer_ranges(values: Any) -> list[list[int]]:
    numbers = sorted({int(value) for value in values})
    if not numbers:
        return []
    ranges: list[list[int]] = []
    start = end = numbers[0]
    for number in numbers[1:]:
        if number == end + 1:
            end = number
            continue
        ranges.append([start, end])
        start = end = number
    ranges.append([start, end])
    return ranges


def archive_document_summary(doc: dict, *, shards: list[dict]) -> dict:
    track_doc_seq = int(doc["track_doc_seq"])
    shard_path = next(
        (
            str(shard["path"])
            for shard in shards
            if int(shard["start_track_doc_seq"])
            <= track_doc_seq
            <= int(shard["end_track_doc_seq"])
        ),
        "",
    )
    return {
        "track_name": str(doc.get("track_name") or ""),
        "track_doc_seq": track_doc_seq,
        "doc_id": str(doc.get("doc_id") or ""),
        "batch_name": str(doc.get("batch_name") or ""),
        "unit_count": int(doc.get("unit_count") or 0),
        "finalized_correction_count": int(
            doc.get("finalized_correction_count") or 0
        ),
        "finalized_correction_sentence_count": int(
            doc.get("finalized_correction_sentence_count") or 0
        ),
        "manual_correction_required_count": int(
            doc.get("manual_correction_required_count") or 0
        ),
        "skipped_unit_count": int(doc.get("skipped_unit_count") or 0),
        "excluded_unit_count": int(doc.get("excluded_unit_count") or 0),
        "text_preview": str(doc.get("text_preview") or ""),
        "archive_revision": str(doc.get("archive_revision") or ""),
        "applied_review_submission_ids": list(
            doc.get("applied_review_submission_ids") or []
        ),
        "applied_finalized_correction_submission_ids": list(
            doc.get("applied_finalized_correction_submission_ids") or []
        ),
        "shard_path": shard_path,
    }


def collect_finalized_archive_documents(root: Path, track_name: str) -> list[dict]:
    documents: dict[tuple[int, str], dict] = {}
    for batch_name in finalized_batch_names(root, track_name):
        batch_dir = root / "data" / "units" / batch_name
        source_paths = (
            batch_dir / "units.yomi.final.jsonl",
            batch_dir / "units.yomi.skipped.jsonl",
            batch_dir / "units.yomi.excluded.jsonl",
        )
        for row in (
            row
            for source_path in source_paths
            if source_path.exists()
            for row in iter_jsonl(source_path)
        ):
            doc_id = str(row.get("doc_id") or "")
            if not doc_id:
                continue
            track_doc_seq = row.get("track_doc_seq")
            if track_doc_seq is None:
                track_doc_seq = infer_track_doc_seq_from_doc_id(doc_id)
            try:
                display_seq = int(track_doc_seq)
            except (TypeError, ValueError):
                continue
            key = (display_seq, doc_id)
            doc = documents.setdefault(
                key,
                {
                    "track_name": track_name,
                    "track_doc_seq": display_seq,
                    "doc_id": doc_id,
                    "batch_name": batch_name,
                    "unit_count": 0,
                    "finalized_correction_count": 0,
                    "finalized_correction_sentence_count": 0,
                    "manual_correction_required_count": 0,
                    "skipped_unit_count": 0,
                    "excluded_unit_count": 0,
                    "_review_submission_ids": set(),
                    "_finalized_correction_submission_ids": set(),
                    "text_preview": "",
                    "units": [],
                },
            )
            unit = archive_unit_from_row(row)
            if unit is None:
                continue
            doc["units"].append(unit)
            doc["unit_count"] = len(doc["units"])
            correction_ids = finalized_correction_submission_ids(row)
            doc["_review_submission_ids"].update(review_submission_ids(row))
            doc["_finalized_correction_submission_ids"].update(correction_ids)
            doc["finalized_correction_count"] = len(doc["_finalized_correction_submission_ids"])
            if correction_ids:
                doc["finalized_correction_sentence_count"] += 1
            if unit.get("manual_correction_required"):
                doc["manual_correction_required_count"] += 1
            if unit.get("skipped"):
                doc["skipped_unit_count"] += 1
            if unit.get("excluded"):
                doc["excluded_unit_count"] += 1
            if not doc["text_preview"]:
                doc["text_preview"] = str(unit.get("text") or "")[:120]
    result = []
    for key in sorted(documents):
        doc = documents[key]
        doc["units"].sort(
            key=lambda unit: (
                int(unit.get("unit_seq") or 0),
                str(unit.get("unit_id") or ""),
            )
        )
        doc["applied_finalized_correction_submission_ids"] = sorted(
            doc.pop("_finalized_correction_submission_ids", set())
        )
        doc["applied_review_submission_ids"] = sorted(
            doc.pop("_review_submission_ids", set())
        )
        doc["archive_revision"] = finalized_archive_document_revision(doc)
        result.append(doc)
    return result


def finalized_archive_document_revision(doc: dict) -> str:
    revision_payload = {
        "track_name": str(doc.get("track_name") or ""),
        "track_doc_seq": int(doc.get("track_doc_seq") or 0),
        "doc_id": str(doc.get("doc_id") or ""),
        "applied_review_submission_ids": list(
            doc.get("applied_review_submission_ids") or []
        ),
        "applied_finalized_correction_submission_ids": list(
            doc.get("applied_finalized_correction_submission_ids") or []
        ),
        "units": [
            {
                "unit_id": str(unit.get("unit_id") or ""),
                "unit_seq": int(unit.get("unit_seq") or 0),
                "text": str(unit.get("text") or ""),
                "rendered_yomi": str(unit.get("rendered_yomi") or ""),
                "strong_repair_evidence": list(unit.get("strong_repair_evidence") or []),
                "applied_finalized_correction_submission_ids": list(
                    unit.get("applied_finalized_correction_submission_ids") or []
                ),
                "manual_correction_required": bool(
                    unit.get("manual_correction_required")
                ),
                "skipped": bool(unit.get("skipped")),
                "excluded": bool(unit.get("excluded")),
            }
            for unit in doc.get("units", [])
        ],
    }
    encoded = json.dumps(
        revision_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()[:16]


def finalized_batch_names(root: Path, track_name: str) -> list[str]:
    batch_root = root / "data" / "pipeline" / "batches"
    if not batch_root.exists():
        return []
    names: list[str] = []
    for path in sorted(batch_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("track_name") != track_name:
            continue
        if payload.get("current_stage") != "yomi_finalized":
            continue
        names.append(str(payload.get("batch_name") or path.stem))
    return sorted(names)


def archive_unit_from_row(row: dict) -> dict | None:
    if row.get("excluded"):
        confirmation_submission_id = str(row.get("confirmation_submission_id") or "")
        return {
            "unit_id": str(row.get("unit_id") or ""),
            "unit_seq": int(row.get("unit_seq") or 0),
            "text": "",
            "yomi_tokens": [],
            "rendered_yomi": "",
            "ruby_tokens": [],
            "finalized_correction_count": 0,
            "manual_correction_required": False,
            "manual_correction": {},
            "skipped": False,
            "excluded": True,
            "tombstone_label": "Removed",
            "reason_category": str(row.get("reason_category") or "sensitive_content"),
            "applied_finalized_correction_submission_ids": (
                [confirmation_submission_id] if confirmation_submission_id else []
            ),
        }
    text = str(row.get("text") or "")
    review = (
        row.get("analysis", {})
        .get("human_review", {})
        .get("yomi_final", {})
    )
    skipped = bool(isinstance(review, dict) and review.get("skip"))
    yomi_tokens = archive_yomi_tokens(row)
    rendered_yomi = yomi_tokens_to_editable_rendered(yomi_tokens) if yomi_tokens else ""
    if not text and not yomi_tokens:
        return None
    unit = {
        "unit_id": str(row.get("unit_id") or ""),
        "unit_seq": int(row.get("unit_seq") or 0),
        "text": text,
        "yomi_tokens": yomi_tokens,
        "rendered_yomi": rendered_yomi,
        "ruby_tokens": yomi_tokens_ruby_tokens(yomi_tokens) if yomi_tokens else [],
        "strong_repair_evidence": archive_strong_repair_evidence(row),
        "finalized_correction_count": finalized_correction_count(row),
        "manual_correction_required": manual_correction_required(row),
        "manual_correction": manual_correction_state(row),
        "skipped": skipped,
        "excluded": False,
        "skip_provenance": archive_skip_provenance(row) if skipped else None,
        "applied_finalized_correction_submission_ids": sorted(
            finalized_correction_submission_ids(row)
        ),
    }
    return unit


def archive_strong_repair_evidence(row: dict) -> list[dict]:
    repairs = (
        row.get("analysis", {})
        .get("llm", {})
        .get("yomi_strong_repair", {})
        .get("repairs", [])
    )
    evidence: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    sources = [
        item
        for repair in repairs if isinstance(repairs, list)
        if isinstance(repair, dict)
        for item in (repair.get("evidence", []) or [])
    ]
    human_evidence = (
        row.get("analysis", {}).get("human_review", {}).get("strong_repair_evidence", [])
    )
    if isinstance(human_evidence, list):
        sources.extend(human_evidence)
    for item in sources:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("surface") or "")
        comment = str(item.get("comment") or "").strip()
        region_id = str(item.get("region_id") or "")
        key = (region_id, surface, comment)
        if not surface or not comment or key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "region_id": region_id,
                "surface": surface,
                "comment": comment,
                "used_web_search": bool(item.get("used_web_search")),
                "surface_occurrence_index": item.get("surface_occurrence_index"),
            }
        )
    return evidence


def archive_skip_provenance(row: dict) -> dict:
    analysis = row.get("analysis", {})
    review = analysis.get("human_review", {}).get("yomi_final", {})
    scope = analysis.get("llm", {}).get("scope_triage", {})
    alphabetic_scope = analysis.get("mechanical", {}).get("alphabetic_scope", {})
    return {
        "confirmed": True,
        "submission_id": str(review.get("submission_id") or "")
        if isinstance(review, dict)
        else "",
        "source": str(scope.get("source") or "human")
        if isinstance(scope, dict)
        else "human",
        "reasons": list(alphabetic_scope.get("reasons") or [])
        if isinstance(alphabetic_scope, dict)
        else [],
    }


def finalized_correction_count(row: dict) -> int:
    return len(finalized_correction_submission_ids(row))


def finalized_correction_submission_ids(row: dict) -> set[str]:
    corrections = (
        row.get("analysis", {})
        .get("human_review", {})
        .get("finalized_corrections")
    )
    if not isinstance(corrections, list):
        return set()
    submission_ids: set[str] = set()
    unit_id = str(row.get("unit_id") or "unknown-unit")
    for index, correction in enumerate(corrections):
        submission_id = str(correction.get("submission_id") or "") if isinstance(correction, dict) else ""
        submission_ids.add(submission_id or f"legacy-correction:{unit_id}:{index}")
    return submission_ids


def review_submission_ids(row: dict) -> set[str]:
    review = (
        row.get("analysis", {})
        .get("human_review", {})
        .get("yomi_final")
    )
    if not isinstance(review, dict):
        return set()
    submission_id = str(review.get("submission_id") or "")
    return {submission_id} if submission_id else set()


def archive_rendered_yomi(row: dict) -> str:
    tokens = archive_yomi_tokens(row)
    return yomi_tokens_to_editable_rendered(tokens) if tokens else ""


def archive_yomi_tokens(row: dict) -> list[list[str]]:
    direct = row.get("rendered_yomi")
    if isinstance(direct, str) and direct:
        tokens = legacy_rendered_to_yomi_tokens(direct, text=str(row.get("text") or ""))
        return normalize_archive_yomi_tokens(tokens)
    analysis = row.get("analysis")
    if not isinstance(analysis, dict):
        return []
    mechanical = analysis.get("mechanical")
    if not isinstance(mechanical, dict):
        return []
    yomi = mechanical.get("yomi")
    if isinstance(yomi, dict):
        tokens = yomi_tokens_from_mapping(yomi, text=str(row.get("text") or ""))
        return normalize_archive_yomi_tokens(tokens)
    return []


def normalize_archive_yomi_tokens(tokens: list[list[str]]) -> list[list[str]]:
    return [
        [
            surface,
            ""
            if is_numeric_only_surface(surface)
            and not allows_optional_japanese_numeral_reading(surface)
            else reading,
        ]
        for surface, reading in tokens
    ]


def normalize_archive_rendered_yomi(rendered: str) -> str:
    tokens: list[str] = []
    for token in rendered_yomi_tokens_for_archive(rendered):
        surface, reading = split_rendered_yomi_token_for_archive(token)
        if (
            is_numeric_only_surface(surface)
            and not allows_optional_japanese_numeral_reading(surface)
        ):
            tokens.append(f"{surface}/")
        else:
            tokens.append(f"{surface}/{reading}")
    return " ".join(tokens)


def rendered_yomi_tokens_for_archive(rendered: str) -> list[str]:
    return [token for token in re.split(r"[ \t\r\n]+", str(rendered or "").strip()) if token]


def split_rendered_yomi_token_for_archive(token: str) -> tuple[str, str]:
    separator = token.rfind("/")
    if separator < 0:
        return token, ""
    return token[:separator], token[separator + 1 :]


def write_archive_shards(
    documents: list[dict],
    *,
    output_root: Path,
    url_prefix: str,
    shard_size: int,
) -> list[dict]:
    shards: list[dict] = []
    if shard_size <= 0:
        raise ValueError("shard_size must be positive")
    for index, start in enumerate(range(0, len(documents), shard_size), start=1):
        shard_docs = documents[start : start + shard_size]
        if not shard_docs:
            continue
        start_seq = int(shard_docs[0]["track_doc_seq"])
        end_seq = int(shard_docs[-1]["track_doc_seq"])
        filename = f"docs_{start_seq:06d}_{end_seq:06d}.json"
        payload = {
            "schema_version": 1,
            "shard_index": index,
            "start_track_doc_seq": start_seq,
            "end_track_doc_seq": end_seq,
            "document_count": len(shard_docs),
            "documents": shard_docs,
        }
        (output_root / filename).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        shards.append(
            {
                "path": f"{url_prefix}/{filename}",
                "start_track_doc_seq": start_seq,
                "end_track_doc_seq": end_seq,
                "document_count": len(shard_docs),
                "batch_names": sorted({str(doc.get("batch_name") or "") for doc in shard_docs}),
            }
        )
    return shards


def write_archive_search_index(
    documents: list[dict],
    *,
    shards: list[dict],
    output_root: Path,
    url_prefix: str,
    supplemental_records: list[dict] | None = None,
) -> str:
    records = []
    for doc in documents:
        track_doc_seq = int(doc["track_doc_seq"])
        shard_path = next(
            (
                str(shard["path"])
                for shard in shards
                if int(shard["start_track_doc_seq"]) <= track_doc_seq <= int(shard["end_track_doc_seq"])
            ),
            "",
        )
        records.append(
            {
                "track_doc_seq": track_doc_seq,
                "doc_id": str(doc.get("doc_id") or ""),
                "shard_path": shard_path,
                "units": [
                    archive_search_unit(unit)
                    for unit in doc.get("units", [])
                    if not unit.get("skipped") and not unit.get("excluded")
                ],
            }
        )
    records.extend(supplemental_records or [])
    records.sort(key=lambda row: (int(row["track_doc_seq"]), str(row.get("doc_id") or "")))
    filename = "search.json"
    payload = {
        "schema_version": 3,
        "document_count": len(records),
        "documents": records,
    }
    (output_root / filename).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return f"{url_prefix}/{filename}"


def archive_search_unit(unit: dict) -> dict:
    text = str(unit.get("text") or "")
    tokens = normalize_archive_yomi_tokens(list(unit.get("yomi_tokens") or []))
    if not tokens and text:
        tokens = [[text, ""]]
    return {
        "unit_seq": int(unit.get("unit_seq") or 0),
        "yomi_tokens": tokens,
        "ruby_tokens": yomi_tokens_ruby_tokens(tokens) if tokens else [],
    }


def collect_pending_review_search_records(
    entries: list[dict],
    *,
    track_name: str,
    finalized_documents: list[dict],
) -> list[dict]:
    finalized_keys = {
        (int(doc["track_doc_seq"]), str(doc.get("doc_id") or ""))
        for doc in finalized_documents
    }
    latest_by_batch: dict[str, dict] = {}
    for entry in entries:
        if (
            entry.get("track_name") != track_name
            or entry.get("review_stage") != "yomi_final_review"
        ):
            continue
        batch_name = str(entry.get("batch_name") or "")
        current = latest_by_batch.get(batch_name)
        if current is None or (
            int(entry.get("created_at_epoch") or 0),
            str(entry.get("pack_id") or ""),
        ) > (
            int(current.get("created_at_epoch") or 0),
            str(current.get("pack_id") or ""),
        ):
            latest_by_batch[batch_name] = entry

    records: dict[tuple[int, str], dict] = {}
    for entry in latest_by_batch.values():
        payload = load_review_pack(entry["source_path"])
        units_by_doc: dict[str, list[dict]] = {}
        for item in payload.get("items", []):
            doc_id = str(item.get("doc_id") or "")
            if not doc_id:
                continue
            text = str(item.get("text") or "")
            rendered_yomi = str(item.get("rendered_yomi") or "")
            try:
                tokens = (
                    normalize_archive_yomi_tokens(
                        editable_rendered_to_yomi_tokens(rendered_yomi, text=text)
                    )
                    if rendered_yomi
                    else []
                )
            except YomiTokenError:
                tokens = []
            if not tokens and text:
                tokens = [[text, ""]]
            units_by_doc.setdefault(doc_id, []).append(
                {
                    "unit_seq": int(item.get("unit_seq") or item.get("seq") or 0),
                    "yomi_tokens": tokens,
                    "ruby_tokens": yomi_tokens_ruby_tokens(tokens) if tokens else [],
                }
            )
        for doc in payload.get("documents", []):
            doc_id = str(doc.get("doc_id") or "")
            try:
                track_doc_seq = int(doc.get("track_doc_seq") or 0)
            except (TypeError, ValueError):
                continue
            key = (track_doc_seq, doc_id)
            if not doc_id or track_doc_seq <= 0 or key in finalized_keys:
                continue
            units = units_by_doc.get(doc_id, [])
            records[key] = {
                "track_doc_seq": track_doc_seq,
                "doc_id": doc_id,
                "pack_path": f"./packs/{entry['site_filename']}",
                "units": sorted(units, key=lambda unit: int(unit["unit_seq"])),
            }
    return [records[key] for key in sorted(records)]


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def infer_track_doc_seq_from_doc_id(doc_id: str) -> int:
    match = re.search(r":0*([0-9]+)$", doc_id)
    if not match:
        raise ValueError(f"Cannot infer document sequence from doc_id: {doc_id}")
    return int(match.group(1))


def sync_directory(source_dir: Path, dest_dir: Path) -> None:
    for path in sorted(source_dir.rglob("*")):
        relative = path.relative_to(source_dir)
        target = dest_dir / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def rewrite_index_asset_versions(review_output_dir: Path) -> None:
    index_path = review_output_dir / "index.html"
    if not index_path.exists():
        return
    html = index_path.read_text(encoding="utf-8")
    replacements = {
        "./style.css": versioned_asset_url(review_output_dir / "style.css", "./style.css"),
        "./app.js": versioned_asset_url(review_output_dir / "app.js", "./app.js"),
    }
    for plain, versioned in replacements.items():
        html = re.sub(rf"{re.escape(plain)}(?:\?v=[A-Za-z0-9._-]+)?", versioned, html)
    index_path.write_text(html, encoding="utf-8")


def versioned_asset_url(path: Path, url: str) -> str:
    if not path.exists():
        return url
    digest = sha256(path.read_bytes()).hexdigest()[:12]
    return f"{url}?v={digest}"


def clear_directory(path: Path, *, max_attempts: int = 5) -> None:
    if not path.exists():
        return
    for attempt in range(max_attempts):
        for child in list(path.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY or attempt + 1 == max_attempts:
                    raise
        if not any(path.iterdir()):
            return
        time.sleep(0.05 * (attempt + 1))
    raise OSError(errno.ENOTEMPTY, "review output directory remained non-empty", path)


def write_root_redirect(path: Path) -> None:
    path.write_text(
        """<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="refresh" content="0; url=./review/" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>yomi-corpus レビュー</title>
  </head>
  <body>
    <main>
      <p>レビュー画面に移動しています…</p>
      <p><a href="./review/">レビュー画面を開く</a></p>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )


def build_pack_title(payload: dict, path: Path) -> str:
    batch_match = re.search(r"(dev_batch_\d+|batch_\d+)", path.stem)
    batch_label = batch_match.group(1) if batch_match else None
    if payload.get("review_stage") == "alphabetic_candidate_review" and batch_label:
        version = path.stem.split("_")[-1]
        return f"英字候補 / {batch_label} / {version}"
    if payload.get("review_stage") == "yomi_final_review" and batch_label:
        version = path.stem.split("_")[-1]
        return f"一括レビュー / {batch_label} / {version}"
    if payload.get("review_stage") == "yomi_strong_repair_review" and batch_label:
        version = path.stem.split("_")[-1]
        return f"詳細修正 / {batch_label} / {version}"
    return str(payload["pack_id"])


def humanize_stage_label(stage_id: str) -> str:
    if stage_id == "alphabetic_candidate_review":
        return "英字候補レビュー"
    if stage_id == "yomi_final_review":
        return "一括レビュー"
    if stage_id == "yomi_strong_repair_review":
        return "詳細修正"
    return stage_id.replace("_", " ").title()


def infer_track_name(payload: dict, path: Path) -> str:
    explicit = payload.get("track_name")
    if explicit in {WORKING_TRACK, DEV_TRACK}:
        return str(explicit)
    pack_id = str(payload.get("pack_id", ""))
    if pack_id.startswith("dev_batch_") or "dev_batch_" in path.stem:
        return DEV_TRACK
    return WORKING_TRACK
