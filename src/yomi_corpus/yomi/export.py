from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys

from yomi_corpus.paths import resolve_repo_path
from yomi_corpus.yomi.config import YomiGenerationConfig, load_yomi_generation_config
from yomi_corpus.yomi.runtime import generate_mechanical_yomi


@dataclass(frozen=True)
class YomiExportVariant:
    name: str
    strategy_name: str | None
    output_jsonl_filename: str
    output_txt_filename: str


@dataclass
class ProgressBar:
    label: str
    total: int
    stream: object = sys.stderr
    width: int = 28
    current: int = 0

    def update(self, step: int = 1) -> None:
        self.current += step
        completed = min(self.current, self.total) if self.total else self.current
        ratio = 1.0 if self.total == 0 else min(completed / self.total, 1.0)
        filled = int(self.width * ratio)
        bar = "#" * filled + "-" * (self.width - filled)
        percent = ratio * 100.0
        self.stream.write(
            f"\r[{bar}] {completed}/{self.total} {percent:5.1f}% {self.label}"
        )
        self.stream.flush()

    def finish(self) -> None:
        if self.current < self.total:
            self.update(self.total - self.current)
        self.stream.write("\n")
        self.stream.flush()


VARIANTS = {
    "aligned_hybrid": YomiExportVariant(
        name="aligned_hybrid",
        strategy_name="aligned_hybrid_v1",
        output_jsonl_filename="units.yomi.aligned_hybrid.jsonl",
        output_txt_filename="units.yomi.aligned_hybrid.txt",
    ),
    "sudachi_only": YomiExportVariant(
        name="sudachi_only",
        strategy_name="sudachi_only_v1",
        output_jsonl_filename="units.yomi.sudachi_only.jsonl",
        output_txt_filename="units.yomi.sudachi_only.txt",
    ),
}


def available_export_variant_names() -> list[str]:
    return sorted(VARIANTS)


def resolve_export_variant(name: str) -> YomiExportVariant:
    try:
        return VARIANTS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown yomi export variant: {name}") from exc


def count_nonempty_lines(path: str | Path) -> int:
    input_path = resolve_repo_path(str(path))
    with input_path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def export_jsonl_yomi(
    *,
    input_jsonl: str | Path,
    output_jsonl: str | Path,
    config: YomiGenerationConfig,
    strategy_name: str | None,
    progress_label: str | None = None,
) -> dict[str, object]:
    input_path = resolve_repo_path(str(input_jsonl))
    output_path = resolve_repo_path(str(output_jsonl))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    last_unit_id: str | None = None
    progress = None
    if progress_label is not None:
        progress = ProgressBar(label=progress_label, total=count_nonempty_lines(input_path))
    with input_path.open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            row["analysis"]["mechanical"]["yomi"] = generate_mechanical_yomi(
                row["text"],
                config=config,
                strategy_name=strategy_name,
            ).__dict__
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
            last_unit_id = str(row["unit_id"])
            if progress is not None:
                progress.update()
    if progress is not None:
        progress.finish()

    return {
        "written": count,
        "output_jsonl": str(output_path),
        "last_unit_id": last_unit_id,
        "strategy_name": strategy_name,
    }


def export_plaintext_yomi(
    *,
    input_jsonl: str | Path,
    output_txt: str | Path,
    config: YomiGenerationConfig,
    strategy_name: str | None,
    progress_label: str | None = None,
) -> dict[str, object]:
    input_path = resolve_repo_path(str(input_jsonl))
    output_path = resolve_repo_path(str(output_txt))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    last_unit_id: str | None = None
    progress = None
    if progress_label is not None:
        progress = ProgressBar(label=progress_label, total=count_nonempty_lines(input_path))
    with input_path.open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            rendered = (
                row.get("analysis", {})
                .get("mechanical", {})
                .get("yomi", {})
                .get("rendered")
            )
            if not rendered:
                rendered = generate_mechanical_yomi(
                    row["text"],
                    config=config,
                    strategy_name=strategy_name,
                ).rendered
            dst.write(f"{row['unit_id']}\t{rendered}\n")
            count += 1
            last_unit_id = str(row["unit_id"])
            if progress is not None:
                progress.update()
    if progress is not None:
        progress.finish()

    return {
        "written": count,
        "output_txt": str(output_path),
        "last_unit_id": last_unit_id,
        "strategy_name": strategy_name,
    }


def export_named_variant(
    *,
    variant_name: str,
    batch_dir: str | Path,
    config_path: str | Path,
    formats: list[str] | tuple[str, ...],
    show_progress: bool = False,
) -> dict[str, object]:
    variant = resolve_export_variant(variant_name)
    batch_path = resolve_repo_path(str(batch_dir))
    config = load_yomi_generation_config(config_path)
    variant_jsonl_path = batch_path / variant.output_jsonl_filename

    summary: dict[str, object] = {
        "variant_name": variant.name,
        "strategy_name": variant.strategy_name,
    }
    if "jsonl" in formats:
        summary["jsonl"] = export_jsonl_yomi(
            input_jsonl=batch_path / "units.jsonl",
            output_jsonl=variant_jsonl_path,
            config=config,
            strategy_name=variant.strategy_name,
            progress_label=f"{variant.name} jsonl" if show_progress else None,
        )
    if "txt" in formats:
        summary["txt"] = export_plaintext_yomi(
            input_jsonl=variant_jsonl_path if "jsonl" in formats else batch_path / "units.jsonl",
            output_txt=batch_path / variant.output_txt_filename,
            config=config,
            strategy_name=variant.strategy_name,
            progress_label=f"{variant.name} txt" if show_progress else None,
        )
    return summary
