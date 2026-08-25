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


PARENTHESIZED_SEMANTIC_TOKENS = {
    "(笑)": (("(", "("), ("笑", "ワライ"), (")", ")")),
    "（笑）": (("（", "（"), ("笑", "ワライ"), ("）", "）")),
    "(株)": (("(", "("), ("株", "カブ"), (")", ")")),
    "（株）": (("（", "（"), ("株", "カブ"), ("）", "）")),
    "(有)": (("(", "("), ("有", "ユウ"), (")", ")")),
    "（有）": (("（", "（"), ("有", "ユウ"), ("）", "）")),
    "(社)": (("(", "("), ("社", "シャ"), (")", ")")),
    "（社）": (("（", "（"), ("社", "シャ"), ("）", "）")),
    "(財)": (("(", "("), ("財", "ザイ"), (")", ")")),
    "（財）": (("（", "（"), ("財", "ザイ"), ("）", "）")),
    "(涙)": (("(", "("), ("涙", "ナミダ"), (")", ")")),
    "（涙）": (("（", "（"), ("涙", "ナミダ"), ("）", "）")),
    "(汗)": (("(", "("), ("汗", "アセ"), (")", ")")),
    "（汗）": (("（", "（"), ("汗", "アセ"), ("）", "）")),
    "(泣)": (("(", "("), ("泣", "ナキ"), (")", ")")),
    "（泣）": (("（", "（"), ("泣", "ナキ"), ("）", "）")),
    "(苦笑)": (("(", "("), ("苦笑", "ニガワライ"), (")", ")")),
    "（苦笑）": (("（", "（"), ("苦笑", "ニガワライ"), ("）", "）")),
}

PARENTHESIZED_WEEKDAY_READINGS = {
    "月": "ゲツ",
    "火": "カ",
    "水": "スイ",
    "木": "モク",
    "金": "キン",
    "土": "ド",
    "日": "ニチ",
}

CANONICAL_COMPOUND_TOKEN_SEQUENCES = {
    (("皆", "ミナ"), ("様", "サマ")): ("皆様", "ミナサマ"),
    (("皆", "ミナ"), ("さま", "サマ")): ("皆さま", "ミナサマ"),
    (("みな", "ミナ"), ("様", "サマ")): ("みな様", "ミナサマ"),
    (("みな", "ミナ"), ("さま", "サマ")): ("みなさま", "ミナサマ"),
    (("皆", "ミナ"), ("さん", "サン")): ("皆さん", "ミナサン"),
    (("みな", "ミナ"), ("さん", "サン")): ("みなさん", "ミナサン"),
}

for _opening, _closing in (("(", ")"), ("（", "）")):
    for _weekday, _reading in PARENTHESIZED_WEEKDAY_READINGS.items():
        PARENTHESIZED_SEMANTIC_TOKENS[f"{_opening}{_weekday}{_closing}"] = (
            (_opening, _opening),
            (_weekday, _reading),
            (_closing, _closing),
        )


def normalize_parenthesized_semantic_tokens(
    tokens: list[list[str]],
) -> list[list[str]]:
    """Normalize canonical tokens, including stale pre-repair artifacts."""
    output: list[list[str]] = []
    for surface, reading in tokens:
        replacement = PARENTHESIZED_SEMANTIC_TOKENS.get(surface)
        if replacement is None:
            output.append([surface, reading])
            continue
        output.extend([part_surface, part_reading] for part_surface, part_reading in replacement)
    normalized, _count = normalize_parenthesized_weekday_sequence(output)
    return normalized


def normalize_canonical_compound_tokens(
    tokens: list[list[str]],
) -> list[list[str]]:
    """Join explicitly selected lexical units without general boundary guessing."""
    output: list[list[str]] = []
    index = 0
    while index < len(tokens):
        matched = False
        for source, replacement in CANONICAL_COMPOUND_TOKEN_SEQUENCES.items():
            end = index + len(source)
            candidate = tuple(
                (str(surface), str(reading))
                for surface, reading in tokens[index:end]
            )
            if candidate != source:
                continue
            output.append([replacement[0], replacement[1]])
            index = end
            matched = True
            break
        if matched:
            continue
        output.append([str(tokens[index][0]), str(tokens[index][1])])
        index += 1
    return output


def normalize_parenthesized_weekday_sequence(
    tokens: list[list[str]],
) -> tuple[list[list[str]], int]:
    output = [list(token) for token in tokens]
    count = 0
    for index in range(1, len(output) - 1):
        opening = output[index - 1][0]
        surface = output[index][0]
        closing = output[index + 1][0]
        reading = PARENTHESIZED_WEEKDAY_READINGS.get(surface)
        if (opening, closing) not in {("(", ")"), ("（", "）")} or reading is None:
            continue
        if output[index][1] != reading:
            output[index][1] = reading
            count += 1
    return output, count


def normalize_parenthesized_semantic_tokens_rendered(rendered: str) -> YomiRepairResult:
    """Keep punctuation unannotated and read known semantic parentheticals."""
    output: list[str] = []
    count = 0
    # ASCII spaces delimit rendered tokens. NBSP is used inside a token to
    # preserve source spaces, including spaces within Sudachi kaomoji tokens.
    for token in rendered.split(" "):
        if not token:
            continue
        separator = token.rfind("/")
        surface = token[:separator] if separator >= 0 else token
        replacement = PARENTHESIZED_SEMANTIC_TOKENS.get(surface)
        if replacement is None:
            output.append(token)
            continue
        output.extend(f"{part_surface}/{part_reading}" for part_surface, part_reading in replacement)
        count += 1
    surfaces = [rendered_token_surface(token) for token in output]
    for index in range(1, len(output) - 1):
        reading = PARENTHESIZED_WEEKDAY_READINGS.get(surfaces[index])
        if (
            (surfaces[index - 1], surfaces[index + 1])
            not in {("(", ")"), ("（", "）")}
            or reading is None
        ):
            continue
        replacement = f"{surfaces[index]}/{reading}"
        if output[index] != replacement:
            output[index] = replacement
            count += 1
    normalized = " ".join(output)
    if not count:
        return YomiRepairResult(rendered=normalized, metadata={})
    return YomiRepairResult(
        rendered=normalized,
        metadata={
            "rule_id": "normalize_parenthesized_semantic_tokens",
            "count": count,
        },
    )


def rendered_token_surface(token: str) -> str:
    separator = token.rfind("/")
    return token[:separator] if separator >= 0 else token


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
