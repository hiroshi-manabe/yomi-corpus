from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import re
from typing import Any


@dataclass(frozen=True)
class YomiRepairRule:
    rule_id: str
    pattern: str
    replacement: str
    status: str
    source: str
    note: str


@dataclass(frozen=True)
class YomiRepairResult:
    rendered: str
    metadata: dict[str, Any]


PARENTHESIZED_LAUGHTER = {
    "(笑)": (("(", "("), ("笑", "ワライ"), (")", ")")),
    "（笑）": (("（", "（"), ("笑", "ワライ"), ("）", "）")),
}


def normalize_parenthesized_laughter_tokens(
    tokens: list[list[str]],
) -> list[list[str]]:
    """Normalize canonical tokens, including stale pre-repair artifacts."""
    output: list[list[str]] = []
    for surface, reading in tokens:
        replacement = PARENTHESIZED_LAUGHTER.get(surface)
        if replacement is None:
            output.append([surface, reading])
            continue
        output.extend([part_surface, part_reading] for part_surface, part_reading in replacement)
    return output


def normalize_parenthesized_laughter(rendered: str) -> YomiRepairResult:
    """Keep punctuation unannotated and assign ワライ only to 笑."""
    output: list[str] = []
    count = 0
    for token in rendered.split():
        separator = token.rfind("/")
        surface = token[:separator] if separator >= 0 else token
        replacement = PARENTHESIZED_LAUGHTER.get(surface)
        if replacement is None:
            output.append(token)
            continue
        output.extend(f"{part_surface}/{part_reading}" for part_surface, part_reading in replacement)
        count += 1
    normalized = " ".join(output)
    if not count:
        return YomiRepairResult(rendered=normalized, metadata={})
    return YomiRepairResult(
        rendered=normalized,
        metadata={
            "rule_id": "normalize_parenthesized_laughter",
            "count": count,
        },
    )


def apply_post_hybrid_repairs(
    rendered: str,
    *,
    rules_path: str | Path | None,
) -> YomiRepairResult:
    if rules_path is None:
        return YomiRepairResult(rendered=rendered, metadata={})

    path = Path(rules_path)
    rules = load_yomi_repair_rules(path)
    current = rendered
    applications: list[dict[str, Any]] = []
    for rule in rules:
        pattern = compile_nonempty_pattern(rule)
        matches = [match.group(0) for match in pattern.finditer(current)]
        if not matches:
            continue
        current, count = pattern.subn(rule.replacement, current)
        if count:
            applications.append(
                {
                    "rule_id": rule.rule_id,
                    "match": matches[0],
                    "replacement": rule.replacement,
                    "count": count,
                    "source": rule.source,
                }
            )

    if not applications:
        return YomiRepairResult(rendered=current, metadata={})
    return YomiRepairResult(
        rendered=current,
        metadata={
            "rule_set": str(path),
            "applied_rule_ids": [application["rule_id"] for application in applications],
            "applications": applications,
        },
    )


def load_yomi_repair_rules(path: Path) -> list[YomiRepairRule]:
    if not path.exists():
        return []

    rules: list[YomiRepairRule] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            status = str(row.get("status", ""))
            if status != "active":
                continue
            rule = YomiRepairRule(
                rule_id=str(row.get("rule_id", "")),
                pattern=str(row.get("pattern", "")),
                replacement=str(row.get("replacement", "")),
                status=status,
                source=str(row.get("source", "")),
                note=str(row.get("note", "")),
            )
            if not rule.rule_id:
                raise ValueError(f"Yomi repair rule without rule_id in {path}")
            rules.append(rule)
    return rules


def compile_nonempty_pattern(rule: YomiRepairRule) -> re.Pattern[str]:
    pattern = re.compile(rule.pattern)
    if pattern.match("") is not None:
        raise ValueError(f"Yomi repair rule {rule.rule_id} can match an empty string")
    return pattern
