from __future__ import annotations

import csv
import gzip
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


HAN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002ffff\U00030000-\U0003ffff]"
)
BCCWJ_MIN_FIELDS = 25


@dataclass
class LexemeStats:
    token_count: int = 0
    document_count: int = 0
    common_noun_count: int = 0
    common_noun_document_count: int = 0
    readings: Counter[str] = field(default_factory=Counter)
    parts_of_speech: Counter[str] = field(default_factory=Counter)
    written_forms: Counter[str] = field(default_factory=Counter)
    registers: Counter[str] = field(default_factory=Counter)


@dataclass
class CurrentCorpusStats:
    finalized_documents: int = 0
    assigned_documents: int = 0
    token_counts: Counter[str] = field(default_factory=Counter)
    token_document_counts: Counter[str] = field(default_factory=Counter)


def iter_bccwj_rows(directory: Path) -> Iterator[list[str]]:
    for archive_path in sorted(directory.glob("*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            for member in sorted(name for name in archive.namelist() if not name.endswith("/")):
                with archive.open(member) as raw:
                    with io.TextIOWrapper(raw, encoding="utf-8", newline="") as handle:
                        for line_number, raw_line in enumerate(handle, start=1):
                            fields = raw_line.rstrip("\r\n").split("\t")
                            if len(fields) < BCCWJ_MIN_FIELDS:
                                raise ValueError(
                                    f"{archive_path.name}:{member}:{line_number}: "
                                    f"expected at least {BCCWJ_MIN_FIELDS} fields, got {len(fields)}"
                                )
                            yield fields


def build_bccwj_vocabulary(rows: Iterable[list[str]]) -> tuple[dict[str, LexemeStats], int, int]:
    vocabulary: dict[str, LexemeStats] = {}
    current_document = ""
    document_lemmas: set[str] = set()
    document_common_nouns: set[str] = set()
    document_count = 0
    token_count = 0

    def flush_document() -> None:
        nonlocal document_count
        if not current_document:
            return
        document_count += 1
        for lemma in document_lemmas:
            vocabulary[lemma].document_count += 1
        for lemma in document_common_nouns:
            vocabulary[lemma].common_noun_document_count += 1

    for fields in rows:
        register = fields[0]
        sample_id = fields[1]
        document_id = f"{register}:{sample_id}"
        if document_id != current_document:
            flush_document()
            current_document = document_id
            document_lemmas = set()
            document_common_nouns = set()

        lemma = fields[12].strip()
        if not lemma:
            continue
        reading = fields[13].strip()
        part_of_speech = fields[16].strip()
        written_form = fields[21].strip() or fields[22].strip() or fields[23].strip()
        stats = vocabulary.setdefault(lemma, LexemeStats())
        stats.token_count += 1
        stats.readings[reading] += 1
        stats.parts_of_speech[part_of_speech] += 1
        stats.written_forms[written_form] += 1
        stats.registers[register] += 1
        document_lemmas.add(lemma)
        if part_of_speech.startswith("名詞-普通名詞"):
            stats.common_noun_count += 1
            document_common_nouns.add(lemma)
        token_count += 1

    flush_document()
    return vocabulary, token_count, document_count


def collect_current_document_texts(root: Path, track: str) -> tuple[dict[str, str], set[str]]:
    state_root = root / "data" / "pipeline" / "batches"
    unit_root = root / "data" / "units"
    texts: dict[str, list[tuple[int, str]]] = {}
    finalized_documents: set[str] = set()

    for state_path in sorted(state_root.glob("*.json")):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("track_name") != track:
            continue
        batch_name = str(state.get("batch_name") or state_path.stem)
        batch_dir = unit_root / batch_name
        finalized_path = batch_dir / "units.yomi.final.jsonl"
        is_finalized = state.get("current_stage") == "yomi_finalized" and finalized_path.exists()
        source_path = finalized_path if is_finalized else batch_dir / "units.jsonl"
        if not source_path.exists():
            continue
        for row in iter_jsonl(source_path):
            doc_id = str(row.get("doc_id") or "")
            text = row.get("text")
            if not doc_id or not isinstance(text, str):
                continue
            texts.setdefault(doc_id, []).append((int(row.get("unit_seq") or 0), text))
            if is_finalized:
                finalized_documents.add(doc_id)

    return {
        doc_id: "".join(text for _, text in sorted(units))
        for doc_id, units in texts.items()
    }, finalized_documents


def build_current_corpus_stats(
    documents: dict[str, str],
    *,
    finalized_documents: set[str],
    sudachi_dictionary: str = "full",
    sudachi_split_mode: str = "A",
) -> CurrentCorpusStats:
    from sudachipy import dictionary, tokenizer

    sudachi = dictionary.Dictionary(dict=sudachi_dictionary).create()
    mode = getattr(tokenizer.Tokenizer.SplitMode, sudachi_split_mode)
    stats = CurrentCorpusStats(
        finalized_documents=len(finalized_documents),
        assigned_documents=len(documents),
    )
    for doc_id, text in documents.items():
        seen: set[str] = set()
        for token in sudachi.tokenize(text, mode):
            values = {token.surface(), token.dictionary_form()}
            values.discard("")
            for value in values:
                stats.token_counts[value] += 1
                seen.add(value)
        stats.token_document_counts.update(seen)
    return stats


def candidate_rows(
    vocabulary: dict[str, LexemeStats],
    current: CurrentCorpusStats,
    *,
    min_bccwj_count: int,
    max_current_count: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for lemma, stats in vocabulary.items():
        if stats.common_noun_count < min_bccwj_count or not HAN_RE.search(lemma):
            continue
        forms = {lemma, *(form for form in stats.written_forms if form)}
        current_count = max((current.token_counts[form] for form in forms), default=0)
        if current_count > max_current_count:
            continue
        current_document_count = max(
            (current.token_document_counts[form] for form in forms),
            default=0,
        )
        rows.append(
            {
                "lemma": lemma,
                "bccwj_count": stats.token_count,
                "bccwj_document_count": stats.document_count,
                "bccwj_common_noun_count": stats.common_noun_count,
                "bccwj_common_noun_document_count": stats.common_noun_document_count,
                "current_count": current_count,
                "current_document_count": current_document_count,
                "readings": compact_counter(stats.readings),
                "parts_of_speech": compact_counter(stats.parts_of_speech),
                "written_forms": compact_counter(stats.written_forms),
                "registers": compact_counter(stats.registers),
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["bccwj_common_noun_document_count"]),
            -int(row["bccwj_common_noun_count"]),
            str(row["lemma"]),
        )
    )
    return rows


def compact_counter(counter: Counter[str]) -> str:
    return "|".join(
        f"{value}:{count}"
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if value
    )


def vocabulary_rows(vocabulary: dict[str, LexemeStats]) -> Iterator[dict[str, object]]:
    for lemma, stats in sorted(
        vocabulary.items(),
        key=lambda item: (-item[1].token_count, item[0]),
    ):
        yield {
            "lemma": lemma,
            "token_count": stats.token_count,
            "document_count": stats.document_count,
            "common_noun_count": stats.common_noun_count,
            "common_noun_document_count": stats.common_noun_document_count,
            "readings": compact_counter(stats.readings),
            "parts_of_speech": compact_counter(stats.parts_of_speech),
            "written_forms": compact_counter(stats.written_forms),
            "registers": compact_counter(stats.registers),
        }


def current_corpus_rows(current: CurrentCorpusStats) -> Iterator[dict[str, object]]:
    for value, count in current.token_counts.most_common():
        yield {
            "surface_or_dictionary_form": value,
            "token_count": count,
            "document_count": current.token_document_counts[value],
        }


def write_tsv(path: Path, rows: Iterable[dict[str, object]], fieldnames: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else Path.open
    if path.suffix == ".gz":
        handle_context = opener(path, "wt", encoding="utf-8", newline="")
    else:
        handle_context = opener(path, "w", encoding="utf-8", newline="")
    with handle_context as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
