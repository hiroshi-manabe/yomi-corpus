from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


def load_review_pack(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_review_pack_entries(review_pack_root: str | Path) -> list[dict]:
    root = Path(review_pack_root)
    entries: list[dict] = []
    if not root.exists():
        return entries

    for path in sorted(root.rglob("*.json")):
        payload = load_review_pack(path)
        pack_id = str(payload["pack_id"])
        title = build_pack_title(payload, path)
        entries.append(
            {
                "pack_id": pack_id,
                "title": title,
                "review_stage": str(payload["review_stage"]),
                "created_at_epoch": int(payload.get("created_at_epoch", 0)),
                "item_count": int(payload.get("item_count", len(payload.get("items", [])))),
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
                "packs": [],
            },
        )
        stage_bucket["packs"].append(
            {
                "pack_id": entry["pack_id"],
                "title": entry["title"],
                "path": f"./packs/{entry['site_filename']}",
                "created_at_epoch": entry["created_at_epoch"],
                "item_count": entry["item_count"],
                "status": "archived",
            }
        )

    ordered_stage_ids = sorted(stages)
    for stage_id in ordered_stage_ids:
        packs = stages[stage_id]["packs"]
        packs.sort(key=lambda row: (row["created_at_epoch"], row["pack_id"]))
        if packs:
            packs[-1]["status"] = "active"
            stages[stage_id]["latest_pack_id"] = packs[-1]["pack_id"]

    return {
        "schema_version": 1,
        "default_stage": ordered_stage_ids[0] if ordered_stage_ids else None,
        "stages": {stage_id: stages[stage_id] for stage_id in ordered_stage_ids},
    }


def publish_review_site(
    *,
    web_review_dir: str | Path,
    docs_dir: str | Path,
    review_pack_root: str | Path,
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
    write_root_redirect(docs_root / "index.html")

    entries = collect_review_pack_entries(review_root)
    manifest = build_review_manifest(entries)

    for entry in entries:
        shutil.copy2(entry["source_path"], pack_output_dir / entry["site_filename"])

    (review_output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def sync_directory(source_dir: Path, dest_dir: Path) -> None:
    for path in sorted(source_dir.rglob("*")):
        relative = path.relative_to(source_dir)
        target = dest_dir / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


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
    batch_match = re.search(r"(batch_\d+)", path.stem)
    batch_label = batch_match.group(1) if batch_match else None
    if payload.get("review_stage") == "alphabetic_candidate_review" and batch_label:
        version = path.stem.split("_")[-1]
        return f"Alphabetic candidates / {batch_label} / {version}"
    return str(payload["pack_id"])


def humanize_stage_label(stage_id: str) -> str:
    if stage_id == "alphabetic_candidate_review":
        return "Alphabetic Promotion Candidates"
    return stage_id.replace("_", " ").title()
