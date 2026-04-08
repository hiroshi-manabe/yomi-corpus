from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from yomi_corpus.paths import resolve_repo_path


@dataclass(frozen=True)
class YomiGenerationConfig:
    sudachi_command: str
    sudachi_args: tuple[str, ...]
    decoder_python: str
    decoder_script: str
    decoder_config: str
    decoder_beam: int | None
    decoder_nbest: int
    default_strategy: str


def load_yomi_generation_config(path: str | Path) -> YomiGenerationConfig:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)

    sudachi = payload.get("sudachi", {})
    decoder = payload.get("decoder", {})
    strategy = payload.get("strategy", {})

    return YomiGenerationConfig(
        sudachi_command=str(sudachi["command"]),
        sudachi_args=tuple(str(arg) for arg in sudachi.get("args", [])),
        decoder_python=str(decoder.get("python", "python")),
        decoder_script=str(resolve_config_path(config_path, str(decoder["script"]))),
        decoder_config=str(resolve_config_path(config_path, str(decoder["config"]))),
        decoder_beam=_optional_int(decoder.get("beam")),
        decoder_nbest=int(decoder.get("nbest", 5)),
        default_strategy=str(strategy.get("default", "agreement_prefer_decoder_v1")),
    )


def resolve_config_path(config_path: Path, relative_or_absolute: str) -> Path:
    path = Path(relative_or_absolute)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
