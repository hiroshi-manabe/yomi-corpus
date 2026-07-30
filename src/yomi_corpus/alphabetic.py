from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
import unicodedata
from typing import Iterable

from yomi_corpus.models import BooleanJudgment
from yomi_corpus.paths import resolve_repo_path
from yomi_corpus.yomi.strategies import span_sudachi_tokens
from yomi_corpus.yomi.types import SudachiToken

TOKEN_CONNECTORS = "-'’.．"
MEASUREMENT_PREFIX_RE = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)?$")
SHORT_UPPERCASE_MAX_LENGTH = 3


@dataclass(frozen=True)
class AlphabeticConfig:
    strict_case_max_length: int
    measurement_units: frozenset[str]
    whitelist: frozenset[str]
    blacklist: frozenset[str]
    case_sensitive_whitelist: frozenset[str]
    case_sensitive_blacklist: frozenset[str]


@dataclass(frozen=True)
class AlphabeticToken:
    text: str
    normalized: str
    start: int
    end: int


@dataclass(frozen=True)
class AlphabeticEntity:
    text: str
    normalized: str
    start: int
    end: int
    component_texts: list[str]
    strict_case: bool


@dataclass(frozen=True)
class AlphabeticOccurrence:
    occurrence_id: str
    doc_id: str
    unit_id: str
    unit_seq: int
    entity_text: str
    entity_key: str
    normalized: str
    char_start: int
    char_end: int
    component_texts: list[str]
    strict_case: bool
    base_list_status: str
    resolved_status: str


@dataclass(frozen=True)
class AlphabeticType:
    entity_key: str
    strict_case: bool
    base_list_status: str
    resolved_status: str
    occurrence_count: int
    unit_count: int
    surface_forms: list[str]
    example_unit_ids: list[str]
    example_texts: list[str]


def load_alphabetic_config(path: str | Path) -> AlphabeticConfig:
    config_path = resolve_repo_path(str(path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    lists = payload["lists"]
    return AlphabeticConfig(
        strict_case_max_length=int(payload.get("strict_case_max_length", 3)),
        measurement_units=frozenset(
            str(unit).lower()
            for unit in payload.get("deterministic", {}).get("measurement_units", [])
        ),
        whitelist=frozenset(_load_word_list(lists["whitelist"], normalize=True)),
        blacklist=frozenset(_load_word_list(lists["blacklist"], normalize=True)),
        case_sensitive_whitelist=frozenset(
            _load_word_list(lists["case_sensitive_whitelist"], normalize=False)
        ),
        case_sensitive_blacklist=frozenset(
            _load_word_list(lists["case_sensitive_blacklist"], normalize=False)
        ),
    )


def extract_alphabetic_tokens(
    text: str, sudachi_tokens: list[SudachiToken]
) -> list[AlphabeticToken]:
    tokens: list[AlphabeticToken] = []
    for span in span_sudachi_tokens(text, sudachi_tokens):
        surface = text[span.start : span.end]
        for match in re.finditer(r"\S+", surface):
            raw_segment = match.group(0)
            leading_punctuation = len(raw_segment) - len(
                raw_segment.lstrip(TOKEN_CONNECTORS + ":")
            )
            segment = raw_segment.strip(TOKEN_CONNECTORS + ":")
            if not segment:
                continue
            segment_start = span.start + match.start() + leading_punctuation
            if _is_wholly_alphabetic_sudachi_segment(segment):
                tokens.append(
                    AlphabeticToken(
                        text=segment,
                        normalized=_normalize_width(segment).lower(),
                        start=segment_start,
                        end=segment_start + len(segment),
                    )
                )
                continue
            tokens.extend(_extract_alphabetic_runs(segment, offset=segment_start))
    return tokens


def _extract_alphabetic_runs(text: str, *, offset: int = 0) -> list[AlphabeticToken]:
    tokens: list[AlphabeticToken] = []
    index = 0
    while index < len(text):
        if not _is_token_atom_start(text[index]):
            index += 1
            continue

        start = index
        index, has_cased_letter = _consume_token_segment(text, index)
        while index < len(text) and text[index] in TOKEN_CONNECTORS:
            next_start = index + 1
            if next_start >= len(text) or not _is_token_atom_start(text[next_start]):
                break
            index, segment_has_cased_letter = _consume_token_segment(text, next_start)
            has_cased_letter = has_cased_letter or segment_has_cased_letter

        if not has_cased_letter:
            continue
        raw = text[start:index]
        tokens.append(
            AlphabeticToken(
                text=raw,
                normalized=_normalize_width(raw).lower(),
                start=offset + start,
                end=offset + index,
            )
        )
    return tokens


def _is_wholly_alphabetic_sudachi_segment(text: str) -> bool:
    has_cased_letter = False
    for char in text:
        if _is_cased_letter(char):
            has_cased_letter = True
            continue
        category = unicodedata.category(char)
        if category.startswith(("M", "N", "P", "S")):
            continue
        return False
    return has_cased_letter


def _consume_token_segment(text: str, start: int) -> tuple[int, bool]:
    index = start
    has_cased_letter = False
    while index < len(text):
        char = text[index]
        if _is_cased_letter(char):
            has_cased_letter = True
            index += 1
            while index < len(text) and unicodedata.category(text[index]).startswith("M"):
                index += 1
            continue
        if _is_unicode_number(char):
            index += 1
            continue
        break
    return index, has_cased_letter


def _is_token_atom_start(char: str) -> bool:
    return _is_cased_letter(char) or _is_unicode_number(char)


def _is_cased_letter(char: str) -> bool:
    return unicodedata.category(char) in {"Lu", "Ll", "Lt"}


def _is_unicode_number(char: str) -> bool:
    return unicodedata.category(char).startswith("N")


def classify_entity(
    entity: AlphabeticEntity, config: AlphabeticConfig, *, source_text: str = ""
) -> str:
    if _is_short_uppercase_initialism(entity, source_text=source_text):
        return "short_uppercase"
    if _is_blacklisted(entity, config):
        return "blacklist"
    if _is_whitelisted(entity, config):
        return "whitelist"
    if _is_measurement_entity(entity, config):
        return "measurement"
    return "unknown"


def entity_key(entity: AlphabeticEntity) -> str:
    return _strict_case_key(entity.text) if entity.strict_case else entity.normalized


def extract_alphabetic_entities(
    text: str,
    config: AlphabeticConfig,
    sudachi_tokens: list[SudachiToken],
) -> list[AlphabeticEntity]:
    tokens = extract_alphabetic_tokens(text, sudachi_tokens)
    entities: list[AlphabeticEntity] = []
    for token in tokens:
        measurement_start = _measurement_prefix_start(text, token, config)
        if measurement_start is not None:
            token = AlphabeticToken(
                text=text[measurement_start : token.end],
                normalized=_normalize_width(
                    text[measurement_start : token.end]
                ).lower(),
                start=measurement_start,
                end=token.end,
            )
        entities.append(_build_entity([token], text, config))
    return entities


def build_occurrences_for_unit(
    unit: dict,
    config: AlphabeticConfig,
    sudachi_tokens: list[SudachiToken],
) -> list[AlphabeticOccurrence]:
    occurrences: list[AlphabeticOccurrence] = []
    entities = extract_alphabetic_entities(unit["text"], config, sudachi_tokens)
    for index, entity in enumerate(entities, start=1):
        base_status = classify_entity(entity, config, source_text=str(unit["text"]))
        occurrences.append(
            AlphabeticOccurrence(
                occurrence_id=f"{unit['unit_id']}:a{index:04d}",
                doc_id=str(unit["doc_id"]),
                unit_id=str(unit["unit_id"]),
                unit_seq=int(unit["unit_seq"]),
                entity_text=entity.text,
                entity_key=entity_key(entity),
                normalized=entity.normalized,
                char_start=entity.start,
                char_end=entity.end,
                component_texts=entity.component_texts,
                strict_case=entity.strict_case,
                base_list_status=base_status,
                resolved_status=base_status,
            )
        )
    return occurrences


def apply_global_decisions(
    occurrences: list[AlphabeticOccurrence], decision_status_by_key: dict[str, str]
) -> list[AlphabeticOccurrence]:
    updated: list[AlphabeticOccurrence] = []
    for occurrence in occurrences:
        if occurrence.base_list_status in {
            "measurement",
            "short_uppercase",
            "single_letter",
        }:
            resolved_status = occurrence.base_list_status
        else:
            resolved_status = decision_status_by_key.get(
                occurrence.entity_key, occurrence.base_list_status
            )
        updated.append(
            AlphabeticOccurrence(
                occurrence_id=occurrence.occurrence_id,
                doc_id=occurrence.doc_id,
                unit_id=occurrence.unit_id,
                unit_seq=occurrence.unit_seq,
                entity_text=occurrence.entity_text,
                entity_key=occurrence.entity_key,
                normalized=occurrence.normalized,
                char_start=occurrence.char_start,
                char_end=occurrence.char_end,
                component_texts=occurrence.component_texts,
                strict_case=occurrence.strict_case,
                base_list_status=occurrence.base_list_status,
                resolved_status=resolved_status,
            )
        )
    return updated


def aggregate_occurrences(
    occurrences: Iterable[AlphabeticOccurrence], *, max_examples: int = 5
) -> list[AlphabeticType]:
    buckets: dict[str, dict] = {}
    for occurrence in occurrences:
        bucket = buckets.setdefault(
            occurrence.entity_key,
            {
                "strict_case": occurrence.strict_case,
                "base_list_status": occurrence.base_list_status,
                "resolved_status": occurrence.resolved_status,
                "occurrence_count": 0,
                "unit_ids": set(),
                "surface_forms": [],
                "surface_seen": set(),
                "example_unit_ids": [],
                "example_texts": [],
                "example_seen": set(),
            },
        )
        bucket["occurrence_count"] += 1
        bucket["unit_ids"].add(occurrence.unit_id)
        if occurrence.entity_text not in bucket["surface_seen"]:
            bucket["surface_seen"].add(occurrence.entity_text)
            bucket["surface_forms"].append(occurrence.entity_text)
        if (
            occurrence.unit_id not in bucket["example_seen"]
            and len(bucket["example_unit_ids"]) < max_examples
        ):
            bucket["example_seen"].add(occurrence.unit_id)
            bucket["example_unit_ids"].append(occurrence.unit_id)

    return [
        AlphabeticType(
            entity_key=token_key_value,
            strict_case=bool(bucket["strict_case"]),
            base_list_status=str(bucket["base_list_status"]),
            resolved_status=str(bucket["resolved_status"]),
            occurrence_count=int(bucket["occurrence_count"]),
            unit_count=len(bucket["unit_ids"]),
            surface_forms=list(bucket["surface_forms"]),
            example_unit_ids=list(bucket["example_unit_ids"]),
            example_texts=list(bucket["example_texts"]),
        )
        for token_key_value, bucket in sorted(buckets.items())
    ]


def attach_examples_to_types(
    types: list[AlphabeticType], unit_text_by_id: dict[str, str], *, max_examples: int = 5
) -> list[AlphabeticType]:
    updated: list[AlphabeticType] = []
    for token_type in types:
        updated.append(
            AlphabeticType(
                entity_key=token_type.entity_key,
                strict_case=token_type.strict_case,
                base_list_status=token_type.base_list_status,
                resolved_status=token_type.resolved_status,
                occurrence_count=token_type.occurrence_count,
                unit_count=token_type.unit_count,
                surface_forms=token_type.surface_forms,
                example_unit_ids=token_type.example_unit_ids[:max_examples],
                example_texts=[
                    unit_text_by_id[unit_id]
                    for unit_id in token_type.example_unit_ids[:max_examples]
                    if unit_id in unit_text_by_id
                ],
            )
        )
    return updated


def project_minor_alphabetic_judgment(
    occurrences: list[AlphabeticOccurrence],
) -> BooleanJudgment:
    if not occurrences:
        return BooleanJudgment(
            value=False,
            certain=True,
            signals=["no_latin_entity_tokens"],
            matches=[],
        )

    blacklisted = _unique_preserve_order(
        [occ.entity_text for occ in occurrences if occ.resolved_status == "blacklist"]
    )
    unknown = _unique_preserve_order(
        [occ.entity_text for occ in occurrences if occ.resolved_status == "unknown"]
    )
    whitelisted = _unique_preserve_order(
        [occ.entity_text for occ in occurrences if occ.resolved_status == "whitelist"]
    )
    short_uppercase = _unique_preserve_order(
        [
            occ.entity_text
            for occ in occurrences
            if occ.resolved_status in {"short_uppercase", "single_letter"}
        ]
    )
    measurements = _unique_preserve_order(
        [occ.entity_text for occ in occurrences if occ.resolved_status == "measurement"]
    )

    if blacklisted:
        return BooleanJudgment(
            value=True,
            certain=True,
            signals=["blacklist_match"],
            matches=blacklisted,
        )

    if not unknown:
        signals = ["all_entities_in_scope"]
        if whitelisted:
            signals.append("entity_level_lookup")
        if short_uppercase:
            signals.append("short_uppercase_initialism_exception")
        if measurements:
            signals.append("measurement_exception")
        return BooleanJudgment(
            value=False,
            certain=True,
            signals=signals,
            matches=whitelisted + short_uppercase + measurements,
        )

    signals = ["unresolved_latin_entity_types"]
    if any(occ.strict_case for occ in occurrences):
        signals.append("strict_case_entity_present")
    return BooleanJudgment(
        value=None,
        certain=False,
        signals=signals,
        matches=unknown,
    )


def project_alphabetic_scope(
    occurrences: list[AlphabeticOccurrence],
    decisions_by_key: dict[str, object] | None = None,
) -> dict:
    decisions_by_key = decisions_by_key or {}
    reasons = []
    unresolved = []
    in_scope = []

    for occurrence in occurrences:
        decision = decisions_by_key.get(occurrence.entity_key)
        source = str(getattr(decision, "source", ""))
        effective_status = _effective_scope_status(occurrence.resolved_status)
        reason = {
            "entity_key": occurrence.entity_key,
            "entity_text": occurrence.entity_text,
            "effective_status": effective_status,
            "resolved_status": occurrence.resolved_status,
            "source": source or _default_scope_source(occurrence),
        }
        if decision is not None:
            reason["decision_status"] = str(getattr(decision, "status", ""))
            note = str(getattr(decision, "note", ""))
            if note:
                reason["note"] = note

        if effective_status == "out_of_scope":
            reasons.append(reason)
        elif effective_status == "unknown":
            unresolved.append(reason)
        else:
            in_scope.append(reason)

    if reasons:
        return {
            "provisional_skip": True,
            "status": "provisional_skip",
            "source": "alphabetic_entity_status",
            "reasons": _dedupe_reason_rows(reasons),
            "unresolved": _dedupe_reason_rows(unresolved),
        }
    if unresolved:
        return {
            "provisional_skip": False,
            "status": "unresolved",
            "source": "alphabetic_entity_status",
            "reasons": [],
            "unresolved": _dedupe_reason_rows(unresolved),
        }
    return {
        "provisional_skip": False,
        "status": "in_scope",
        "source": "alphabetic_entity_status",
        "reasons": [],
        "in_scope": _dedupe_reason_rows(in_scope),
    }


def _is_blacklisted(entity: AlphabeticEntity, config: AlphabeticConfig) -> bool:
    if entity.strict_case:
        return _strict_case_key(entity.text) in config.case_sensitive_blacklist
    return (
        entity.normalized in config.blacklist
        or entity.text in config.case_sensitive_blacklist
        or _strict_case_key(entity.text) in config.case_sensitive_blacklist
    )


def _is_whitelisted(entity: AlphabeticEntity, config: AlphabeticConfig) -> bool:
    if entity.strict_case:
        return _strict_case_key(entity.text) in config.case_sensitive_whitelist
    return (
        entity.normalized in config.whitelist
        or entity.text in config.case_sensitive_whitelist
        or _strict_case_key(entity.text) in config.case_sensitive_whitelist
    )


def _requires_strict_case(token: AlphabeticToken, config: AlphabeticConfig) -> bool:
    return len(_normalize_width(token.text)) <= config.strict_case_max_length


def _is_short_uppercase_initialism(
    entity: AlphabeticEntity, *, source_text: str = ""
) -> bool:
    text = _normalize_width(entity.text)
    if not (
        len(entity.component_texts) == 1
        and 1 <= len(text) <= SHORT_UPPERCASE_MAX_LENGTH
        and text.isascii()
        and text.isalpha()
        and text.isupper()
    ):
        return False
    if not source_text:
        return True
    neighbors = []
    if entity.start > 0:
        neighbors.append(source_text[entity.start - 1])
    if entity.end < len(source_text):
        neighbors.append(source_text[entity.end])
    return not any(_is_ascii_alphanumeric_after_width_normalization(char) for char in neighbors)


def _is_ascii_alphanumeric_after_width_normalization(char: str) -> bool:
    normalized = _normalize_width(char)
    return len(normalized) == 1 and normalized.isascii() and normalized.isalnum()


def _is_measurement_entity(entity: AlphabeticEntity, config: AlphabeticConfig) -> bool:
    if len(entity.component_texts) != 1:
        return False
    match = re.fullmatch(r"[0-9]+(?:\.[0-9]+)?([a-z]+)", entity.normalized)
    return bool(match and match.group(1) in config.measurement_units)


def _build_entity(
    tokens: list[AlphabeticToken], text: str, config: AlphabeticConfig
) -> AlphabeticEntity:
    start = tokens[0].start
    end = tokens[-1].end
    raw_text = text[start:end]
    component_texts = [token.text for token in tokens]
    strict_case = len(tokens) == 1 and _requires_strict_case(tokens[0], config)
    normalized = _normalize_entity_text(raw_text)
    return AlphabeticEntity(
        text=raw_text,
        normalized=normalized,
        start=start,
        end=end,
        component_texts=component_texts,
        strict_case=strict_case,
    )


def _measurement_prefix_start(
    text: str, token: AlphabeticToken, config: AlphabeticConfig
) -> int | None:
    if token.normalized not in config.measurement_units:
        return None
    match = MEASUREMENT_PREFIX_RE.search(text[: token.start])
    if match is None or match.end() != token.start:
        return None
    return match.start()


def _normalize_entity_text(text: str) -> str:
    return _normalize_width(text).lower()


def _strict_case_key(text: str) -> str:
    return _normalize_width(text).strip()


def _normalize_width(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _load_word_list(path_str: str, *, normalize: bool) -> list[str]:
    path = resolve_repo_path(path_str)
    entries: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(line.lower() if normalize else line)
    return entries


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _effective_scope_status(resolved_status: str) -> str:
    if resolved_status == "blacklist":
        return "out_of_scope"
    if resolved_status in {
        "whitelist",
        "short_uppercase",
        "single_letter",
        "measurement",
    }:
        return "in_scope"
    return "unknown"


def _default_scope_source(occurrence: AlphabeticOccurrence) -> str:
    if occurrence.resolved_status == "blacklist" and occurrence.base_list_status == "blacklist":
        return "static_blacklist"
    if occurrence.resolved_status == "whitelist" and occurrence.base_list_status == "whitelist":
        return "static_whitelist"
    if occurrence.resolved_status in {"short_uppercase", "single_letter"}:
        return "short_uppercase_initialism_exception"
    if occurrence.resolved_status == "measurement":
        return "deterministic_measurement"
    return "unknown"


def _dedupe_reason_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for row in rows:
        key = (
            str(row.get("entity_key", "")),
            str(row.get("effective_status", "")),
            str(row.get("source", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped
