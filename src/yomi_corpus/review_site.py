from __future__ import annotations

import json
import re
import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any

from yomi_corpus.pipeline import DEV_TRACK, WORKING_TRACK
from yomi_corpus.yomi.final_review import (
    manual_correction_required,
    manual_correction_state,
    yomi_tokens_ruby_tokens,
)
from yomi_corpus.yomi.numeric_surfaces import is_numeric_only_surface
from yomi_corpus.yomi.token_codec import (
    legacy_rendered_to_yomi_tokens,
    yomi_tokens_from_mapping,
    yomi_tokens_to_editable_rendered,
)


ARCHIVE_SHARD_SIZE = 1000


def load_review_pack(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_review_pack_entries(review_pack_root: str | Path) -> list[dict]:
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
        entries.append(
            {
                "pack_id": pack_id,
                "title": title,
                "review_stage": str(payload["review_stage"]),
                "queue_id": str(payload.get("queue_id") or payload["review_stage"]),
                "track_name": track_name,
                "batch_name": str(payload.get("batch_name") or ""),
                "created_at_epoch": int(payload.get("created_at_epoch", 0)),
                "item_count": int(payload.get("item_count", len(payload.get("items", [])))),
                "document_count": int(
                    payload.get("summary", {}).get("document_count", len(payload.get("documents", [])))
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
        )
    return entries


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
            if int(pack.get("selectable_document_count") or 0) <= 0:
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

    entries = collect_review_pack_entries(review_root)
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
        archive_manifest = publish_review_archive(
            project_root=project_root,
            review_output_dir=review_output_dir,
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
        )
        tracks[track_name] = {
            "track_name": track_name,
            "document_count": len(documents),
            "shard_size": shard_size,
            "shards": shards,
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
                "shard_count": len(track["shards"]),
            }
            for track_name, track in tracks.items()
        },
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
        doc["applied_finalized_correction_submission_ids"] = sorted(
            doc.pop("_finalized_correction_submission_ids", set())
        )
        doc["archive_revision"] = finalized_archive_document_revision(doc)
        result.append(doc)
    return result


def finalized_archive_document_revision(doc: dict) -> str:
    revision_payload = {
        "track_name": str(doc.get("track_name") or ""),
        "track_doc_seq": int(doc.get("track_doc_seq") or 0),
        "doc_id": str(doc.get("doc_id") or ""),
        "applied_finalized_correction_submission_ids": list(
            doc.get("applied_finalized_correction_submission_ids") or []
        ),
        "units": [
            {
                "unit_id": str(unit.get("unit_id") or ""),
                "unit_seq": int(unit.get("unit_seq") or 0),
                "text": str(unit.get("text") or ""),
                "rendered_yomi": str(unit.get("rendered_yomi") or ""),
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
            "applied_finalized_correction_submission_ids": [],
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
        [surface, "" if is_numeric_only_surface(surface) else reading]
        for surface, reading in tokens
    ]


def normalize_archive_rendered_yomi(rendered: str) -> str:
    tokens: list[str] = []
    for token in rendered_yomi_tokens_for_archive(rendered):
        surface, reading = split_rendered_yomi_token_for_archive(token)
        if is_numeric_only_surface(surface):
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
                "text": "\n".join(
                    str(unit.get("text") or "")
                    for unit in doc.get("units", [])
                    if not unit.get("skipped") and not unit.get("excluded")
                ),
            }
        )
    filename = "search.json"
    payload = {
        "schema_version": 1,
        "document_count": len(records),
        "documents": records,
    }
    (output_root / filename).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return f"{url_prefix}/{filename}"


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


def clear_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def write_root_redirect(path: Path) -> None:
    path.write_text(
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="refresh" content="0; url=./review/" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>yomi-corpus review</title>
  </head>
  <body>
    <main>
      <p>Redirecting to the review workspace…</p>
      <p><a href="./review/">Open review workspace</a></p>
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
        return f"Alphabetic candidates / {batch_label} / {version}"
    if payload.get("review_stage") == "yomi_final_review" and batch_label:
        version = path.stem.split("_")[-1]
        return f"Yomi final review / {batch_label} / {version}"
    if payload.get("review_stage") == "yomi_strong_repair_review" and batch_label:
        version = path.stem.split("_")[-1]
        return f"Yomi strong repair review / {batch_label} / {version}"
    return str(payload["pack_id"])


def humanize_stage_label(stage_id: str) -> str:
    if stage_id == "alphabetic_candidate_review":
        return "Alphabetic Promotion Candidates"
    if stage_id == "yomi_final_review":
        return "Yomi Final Review"
    if stage_id == "yomi_strong_repair_review":
        return "Yomi Strong Repair Review"
    return stage_id.replace("_", " ").title()


def infer_track_name(payload: dict, path: Path) -> str:
    explicit = payload.get("track_name")
    if explicit in {WORKING_TRACK, DEV_TRACK}:
        return str(explicit)
    pack_id = str(payload.get("pack_id", ""))
    if pack_id.startswith("dev_batch_") or "dev_batch_" in path.stem:
        return DEV_TRACK
    return WORKING_TRACK
