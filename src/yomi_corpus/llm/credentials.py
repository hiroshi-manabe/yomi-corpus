from __future__ import annotations

import os
from pathlib import Path

DEFAULT_OPENAI_API_KEY_PATH = Path("~/.config/api_keys/openai/default.txt").expanduser()


def resolve_openai_api_key(
    *,
    api_key: str | None = None,
    api_key_file: str | Path | None = None,
) -> tuple[str | None, str | None]:
    if api_key:
        return api_key.strip(), "explicit"

    env_api_key = os.environ.get("OPENAI_API_KEY")
    if env_api_key:
        return env_api_key.strip(), "env:OPENAI_API_KEY"

    env_api_key_file = os.environ.get("OPENAI_API_KEY_FILE")
    if env_api_key_file:
        file_key = _read_api_key_file(Path(env_api_key_file).expanduser())
        if file_key:
            return file_key, "env:OPENAI_API_KEY_FILE"

    if api_key_file:
        file_key = _read_api_key_file(Path(api_key_file).expanduser())
        if file_key:
            return file_key, "flag:api_key_file"

    default_key = _read_api_key_file(DEFAULT_OPENAI_API_KEY_PATH)
    if default_key:
        return default_key, f"file:{DEFAULT_OPENAI_API_KEY_PATH}"

    return None, None


def _read_api_key_file(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None
