from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from yomi_corpus.pipeline import PipelineWorkspace, STAGE_YOMI_FINALIZED, normalize_track_name
from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.decoder_corpus import export_decoder_corpus_file


DEFAULT_YOMI_CONFIG_PATH = "config/yomi/default.toml"


@dataclass(frozen=True)
class DecoderModelRefreshSummary:
    track_name: str
    finalized_batches: list[str]
    exported_corpora: list[str]
    model_dir: str
    build_script: str
    base_corpus: str
    skip_kenlm: bool
    track_state_path: str


def refresh_decoder_model(
    *,
    root: str | Path,
    track_name: str,
    model_id: str | None = None,
    yomi_config_path: str | Path = DEFAULT_YOMI_CONFIG_PATH,
    decoder_build_script: str | Path | None = None,
    corpus_root: str | Path | None = None,
    model_root: str | Path | None = None,
    base_corpus: str | Path | None = None,
    skip_kenlm: bool = False,
) -> DecoderModelRefreshSummary:
    project_root = Path(root).resolve()
    normalized_track = normalize_track_name(track_name)
    workspace = PipelineWorkspace(project_root)
    finalized_batches = list_finalized_batches(workspace, normalized_track)
    if not finalized_batches:
        raise ValueError(f"No finalized batches found for track {normalized_track}.")

    corpus_base = resolve_project_path(project_root, corpus_root, "data/decoder_corpora")
    model_base = resolve_project_path(project_root, model_root, "data/decoder_models")
    corpus_dir = corpus_base / normalized_track
    chosen_model_id = model_id or timestamp_model_id()
    model_dir = model_base / normalized_track / chosen_model_id
    if base_corpus is not None:
        chosen_base_corpus = resolve_project_path(project_root, base_corpus, "")
    else:
        yomi_config = load_yomi_generation_config(resolve_project_path(project_root, yomi_config_path, ""))
        chosen_base_corpus = (
            Path(yomi_config.corpus_frequency_source_corpus)
            if yomi_config.corpus_frequency_source_corpus
            else None
        )
    if chosen_base_corpus is None:
        raise ValueError("Base corpus is not configured. Pass --base-corpus.")
    build_script = resolve_project_path(
        project_root,
        decoder_build_script,
        "../yomi-decoder/scripts/build_model.py",
    )

    exported_corpora = []
    for batch_name in finalized_batches:
        input_jsonl = project_root / "data" / "units" / batch_name / "units.yomi.final.jsonl"
        output_txt = corpus_dir / f"{batch_name}.txt"
        manifest_json = output_txt.with_suffix(output_txt.suffix + ".manifest.json")
        export_decoder_corpus_file(
            input_jsonl=input_jsonl,
            output_txt=output_txt,
            manifest_json=manifest_json,
            source_name=batch_name,
        )
        exported_corpora.append(str(output_txt))

    run_build_model(
        build_script=build_script,
        base_corpus=chosen_base_corpus,
        extra_corpora=[Path(path) for path in exported_corpora],
        output_dir=model_dir,
        skip_kenlm=skip_kenlm,
    )

    track_state = workspace.load_track_state(normalized_track)
    track_state.decoder_model_dir = str(model_dir)
    track_state.updated_at = now_iso()
    workspace.save_track_state(track_state)
    summary = DecoderModelRefreshSummary(
        track_name=normalized_track,
        finalized_batches=finalized_batches,
        exported_corpora=exported_corpora,
        model_dir=str(model_dir),
        build_script=str(build_script),
        base_corpus=str(chosen_base_corpus),
        skip_kenlm=skip_kenlm,
        track_state_path=str(workspace.track_state_path(normalized_track)),
    )
    write_refresh_manifest(model_dir, summary)
    return summary


def list_finalized_batches(workspace: PipelineWorkspace, track_name: str) -> list[str]:
    rows: list[tuple[str, str]] = []
    batches_root = workspace.batches_root()
    if not batches_root.exists():
        return []
    for path in sorted(batches_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(payload.get("track_name")) != track_name:
            continue
        if str(payload.get("current_stage")) != STAGE_YOMI_FINALIZED:
            continue
        batch_name = str(payload.get("batch_name") or path.stem)
        final_path = workspace.batch_dir(batch_name) / "units.yomi.final.jsonl"
        if not final_path.exists():
            continue
        rows.append((batch_name, str(payload.get("updated_at", ""))))
    return [batch_name for batch_name, _ in sorted(rows)]


def run_build_model(
    *,
    build_script: Path,
    base_corpus: Path,
    extra_corpora: list[Path],
    output_dir: Path,
    skip_kenlm: bool,
) -> None:
    command = [
        sys.executable,
        str(build_script),
        "--base-corpus",
        str(base_corpus),
        "--output-dir",
        str(output_dir),
    ]
    for path in extra_corpora:
        command.extend(["--extra-corpus", str(path)])
    if skip_kenlm:
        command.append("--skip-kenlm")
    subprocess.run(command, check=True)


def resolve_project_path(project_root: Path, path: str | Path | None, default: str) -> Path:
    raw = Path(path) if path is not None else Path(default)
    return raw if raw.is_absolute() else project_root / raw


def write_refresh_manifest(model_dir: Path, summary: DecoderModelRefreshSummary) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "yomi_corpus_refresh.json").write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def timestamp_model_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
