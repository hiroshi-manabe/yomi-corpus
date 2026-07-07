from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from yomi_corpus.paths import resolve_repo_path


DEFAULT_REVIEW_TRANSPORT_CONFIG = "config/review_transport/default.toml"
PUBLISH_MODE_NONE = "none"
PUBLISH_MODE_LOCAL = "local"
PUBLISH_MODE_GH_PAGES = "gh-pages"
PUBLISH_MODES = {
    PUBLISH_MODE_NONE,
    PUBLISH_MODE_LOCAL,
    PUBLISH_MODE_GH_PAGES,
}


@dataclass(frozen=True)
class ReviewTransportConfig:
    repo: str
    pages_url: str | None = None
    publish_mode: str = PUBLISH_MODE_LOCAL


def load_review_transport_config(
    track_name: str,
    path: str | Path = DEFAULT_REVIEW_TRANSPORT_CONFIG,
) -> ReviewTransportConfig:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    tracks = payload.get("tracks", {})
    track = tracks.get(track_name)
    if not isinstance(track, dict):
        raise ValueError(f"Review transport config has no track: {track_name}")
    repo = str(track.get("repo") or "")
    if not repo:
        raise ValueError(f"Review transport config for {track_name} is missing repo.")
    publish_mode = str(track.get("publish_mode") or PUBLISH_MODE_LOCAL)
    if publish_mode not in PUBLISH_MODES:
        raise ValueError(
            f"Unsupported review transport publish_mode for {track_name}: {publish_mode}"
        )
    pages_url = _optional_str(track.get("pages_url"))
    return ReviewTransportConfig(
        repo=repo,
        pages_url=pages_url,
        publish_mode=publish_mode,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
