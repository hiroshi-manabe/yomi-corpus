from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = ROOT / "config" / "prompts"
OPERATIONAL_TASK_CONFIGS = (
    ROOT / "config" / "llm" / "yomi_reading.toml",
    ROOT / "config" / "llm" / "yomi_repair.toml",
)


def test_root_prompt_directory_contains_only_operational_prompts() -> None:
    referenced: set[Path] = set()
    for config_path in OPERATIONAL_TASK_CONFIGS:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        for key in ("prompt_template", "alphabet_prompt_template"):
            value = config.get(key)
            if value is None:
                continue
            prompt_path = ROOT / str(value)
            assert prompt_path.parent == PROMPT_ROOT
            assert prompt_path.is_file()
            referenced.add(prompt_path.resolve())

    root_prompts = {path.resolve() for path in PROMPT_ROOT.glob("*.txt")}
    assert root_prompts == referenced
