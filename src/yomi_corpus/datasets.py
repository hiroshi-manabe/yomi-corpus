from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from yomi_corpus.paths import resolve_repo_path


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    source_path: Path


def load_dataset_config(path: str | Path) -> DatasetConfig:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    return DatasetConfig(
        name=str(payload["name"]),
        source_path=resolve_repo_path(str(payload["source_path"])),
    )
