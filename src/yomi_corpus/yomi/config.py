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
    decoder_model_dir: str | None = None
    post_hybrid_repair_rules: str | None = None
    corpus_frequency_source_corpus: str | None = None
    corpus_frequency_source_corpus_version: str | None = None
    corpus_frequency_stats_artifact: str | None = None
    corpus_frequency_manifest: str | None = None
    corpus_frequency_surface_filter: str = "target"
    corpus_frequency_min_count: int = 5
    corpus_frequency_min_share: float = 0.95


def load_yomi_generation_config(path: str | Path) -> YomiGenerationConfig:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)

    sudachi = payload.get("sudachi", {})
    decoder = payload.get("decoder", {})
    strategy = payload.get("strategy", {})
    repairs = payload.get("post_hybrid_repairs", {})
    corpus_frequency = payload.get("corpus_frequency", {})
    corpus_frequency_safety = corpus_frequency.get("safety", {})

    return YomiGenerationConfig(
        sudachi_command=str(sudachi["command"]),
        sudachi_args=tuple(str(arg) for arg in sudachi.get("args", [])),
        decoder_python=str(decoder.get("python", "python")),
        decoder_script=str(resolve_config_path(config_path, str(decoder["script"]))),
        decoder_config=str(resolve_config_path(config_path, str(decoder["config"]))),
        decoder_beam=_optional_int(decoder.get("beam")),
        decoder_nbest=int(decoder.get("nbest", 5)),
        default_strategy=str(strategy.get("default", "agreement_prefer_decoder_v1")),
        decoder_model_dir=_optional_path(config_path, decoder.get("model_dir")),
        post_hybrid_repair_rules=_optional_path(config_path, repairs.get("rules")),
        corpus_frequency_source_corpus=_optional_path(config_path, corpus_frequency.get("source_corpus")),
        corpus_frequency_source_corpus_version=_optional_str(
            corpus_frequency.get("source_corpus_version")
        ),
        corpus_frequency_stats_artifact=_optional_path(config_path, corpus_frequency.get("stats_artifact")),
        corpus_frequency_manifest=_optional_path(config_path, corpus_frequency.get("manifest")),
        corpus_frequency_surface_filter=str(corpus_frequency.get("surface_filter", "target")),
        corpus_frequency_min_count=int(corpus_frequency_safety.get("min_count", 5)),
        corpus_frequency_min_share=float(corpus_frequency_safety.get("min_share", 0.95)),
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


def _optional_path(config_path: Path, value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return str(resolve_config_path(config_path, text))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
