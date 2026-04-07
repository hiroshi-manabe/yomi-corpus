from __future__ import annotations

from pathlib import Path

from yomi_corpus.paths import resolve_repo_path


def load_prompt_template(path: str | Path) -> str:
    template_path = resolve_repo_path(str(path))
    text = template_path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n"


def render_prompt(template: str, variables: dict[str, object]) -> str:
    normalized = {key: _stringify(value) for key, value in variables.items()}
    try:
        return template.format_map(normalized)
    except KeyError as exc:
        raise KeyError(f"Missing prompt variable: {exc.args[0]}") from exc


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
