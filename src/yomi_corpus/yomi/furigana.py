from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


_FURIGANA_RE = re.compile(r"([^（）]+?)（([^（）]+)）")


def is_han(char: str) -> bool:
    return (
        "\u4e00" <= char <= "\u9fff"
        or "\u3400" <= char <= "\u4dbf"
        or "\uf900" <= char <= "\ufaff"
        or char in {"々", "〆", "〻"}
    )


def has_han(text: str) -> bool:
    return any(is_han(char) for char in text)


def kata_to_hira(text: str) -> str:
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def hira_to_kata(text: str) -> str:
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            chars.append(chr(code + 0x60))
        else:
            chars.append(char)
    return "".join(chars)


@dataclass(frozen=True)
class FuriganaCandidate:
    annotated_surface: str
    score: float
    chunks: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FuriganaResult:
    surface: str
    reading: str
    annotated_surface: str | None
    method: str
    confidence: str
    candidates: tuple[FuriganaCandidate, ...] = ()
    reason: str | None = None

    def to_jsonable(self) -> dict[str, object]:
        return asdict(self)


class FuriganaConverter:
    def __init__(
        self,
        exact_lookup: dict[tuple[str, str], set[str]] | None = None,
        evidence: Counter[tuple[str, str]] | None = None,
        *,
        max_candidates: int = 2000,
    ) -> None:
        self.exact_lookup = exact_lookup or {}
        self.evidence = Counter(evidence or {})
        self.max_candidates = max_candidates

    @classmethod
    def from_tsv(cls, path: Path, *, max_candidates: int = 2000) -> FuriganaConverter:
        return cls.from_tsv_many([path], max_candidates=max_candidates)

    @classmethod
    def from_tsv_many(
        cls,
        paths: Iterable[Path],
        *,
        max_candidates: int = 2000,
    ) -> FuriganaConverter:
        exact_lookup: dict[tuple[str, str], set[str]] = defaultdict(set)
        evidence: Counter[tuple[str, str]] = Counter()
        for path in paths:
            if not path.exists():
                continue
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                required = {"surface", "reading", "annotated_surface"}
                if not required.issubset(reader.fieldnames or set()):
                    raise ValueError(f"{path} must contain columns: {', '.join(sorted(required))}")
                for row in reader:
                    surface = row["surface"]
                    reading = row["reading"]
                    annotated = row["annotated_surface"]
                    if not surface or not reading or not annotated:
                        continue
                    exact_lookup[(surface, reading)].add(annotated)
                    evidence.update(parse_annotated_chunks(annotated))
        return cls(dict(exact_lookup), evidence, max_candidates=max_candidates)

    def convert(self, surface: str, reading: str) -> FuriganaResult:
        normalized_reading = hira_to_kata(reading)
        exact = self.exact_lookup.get((surface, normalized_reading)) or self.exact_lookup.get((surface, reading))
        if exact:
            candidates = tuple(
                FuriganaCandidate(
                    annotated_surface=annotated,
                    score=self._score_chunks(parse_annotated_chunks(annotated)),
                    chunks=tuple(parse_annotated_chunks(annotated)),
                )
                for annotated in sorted(exact)
            )
            if len(candidates) == 1:
                return FuriganaResult(
                    surface=surface,
                    reading=normalized_reading,
                    annotated_surface=candidates[0].annotated_surface,
                    method="exact_lookup",
                    confidence="high",
                    candidates=candidates,
                )
            return FuriganaResult(
                surface=surface,
                reading=normalized_reading,
                annotated_surface=None,
                method="exact_lookup_ambiguous",
                confidence="ambiguous",
                candidates=candidates,
                reason="multiple dictionary forms for the same surface/reading pair",
            )

        if not has_han(surface):
            return FuriganaResult(
                surface=surface,
                reading=normalized_reading,
                annotated_surface=surface,
                method="plain",
                confidence="high",
            )

        alignments = self._enumerate_alignments(surface, normalized_reading)
        if not alignments:
            return FuriganaResult(
                surface=surface,
                reading=normalized_reading,
                annotated_surface=None,
                method="unresolved",
                confidence="none",
                reason="no valid surface/reading alignment",
            )

        candidates = tuple(
            sorted(
                (self._candidate_from_chunks(surface, chunks) for chunks in alignments),
                key=_candidate_sort_key,
            )
        )
        if len(candidates) == 1:
            return FuriganaResult(
                surface=surface,
                reading=normalized_reading,
                annotated_surface=candidates[0].annotated_surface,
                method="unique_alignment",
                confidence="high",
                candidates=candidates,
            )

        best = candidates[0]
        second = candidates[1]
        confidence = "medium"
        if best.score <= 0:
            confidence = "ambiguous"
        elif second.score > 0 and best.score / second.score < 4:
            confidence = "ambiguous"
        if confidence == "ambiguous":
            return FuriganaResult(
                surface=surface,
                reading=normalized_reading,
                annotated_surface=None,
                method="scored_alignment_ambiguous",
                confidence=confidence,
                candidates=candidates[:20],
                reason="multiple alignments remain close after dictionary scoring",
            )
        return FuriganaResult(
            surface=surface,
            reading=normalized_reading,
            annotated_surface=best.annotated_surface,
            method="scored_alignment",
            confidence=confidence,
            candidates=candidates[:20],
        )

    def _candidate_from_chunks(self, surface: str, chunks: tuple[tuple[str, str], ...]) -> FuriganaCandidate:
        annotated_parts: list[str] = []
        chunk_by_start: dict[int, tuple[str, str]] = {}
        index = 0
        for chunk_surface, chunk_reading in chunks:
            start = surface.index(chunk_surface, index)
            chunk_by_start[start] = (chunk_surface, chunk_reading)
            index = start + len(chunk_surface)

        index = 0
        while index < len(surface):
            chunk = chunk_by_start.get(index)
            if chunk is None:
                annotated_parts.append(surface[index])
                index += 1
                continue
            chunk_surface, chunk_reading = chunk
            annotated_parts.append(f"{chunk_surface}（{kata_to_hira(chunk_reading)}）")
            index += len(chunk_surface)
        return FuriganaCandidate(
            annotated_surface="".join(annotated_parts),
            score=self._score_chunks(chunks),
            chunks=chunks,
        )

    def _score_chunks(self, chunks: Iterable[tuple[str, str]]) -> float:
        score = 1.0
        for chunk_surface, chunk_reading in chunks:
            score *= self.evidence[(chunk_surface, kata_to_hira(chunk_reading))] + 0.01
        return score

    def _enumerate_alignments(self, surface: str, reading: str) -> list[tuple[tuple[str, str], ...]]:
        elements = _surface_elements(surface)
        results: list[tuple[tuple[str, str], ...]] = []

        def rec(element_index: int, reading_index: int, chunks: list[tuple[str, str]]) -> None:
            if len(results) >= self.max_candidates:
                return
            if element_index == len(elements):
                if reading_index == len(reading):
                    results.append(tuple(chunks))
                return

            kind, value = elements[element_index]
            if kind == "han":
                remaining_fixed_min = _minimum_remaining_reading(elements[element_index + 1 :])
                max_end = len(reading) - remaining_fixed_min
                for end in range(reading_index + 1, max_end + 1):
                    chunks.append((value, reading[reading_index:end]))
                    rec(element_index + 1, end, chunks)
                    chunks.pop()
                return

            for variant in _fixed_reading_variants(value):
                if variant == "":
                    rec(element_index + 1, reading_index, chunks)
                elif reading.startswith(variant, reading_index):
                    rec(element_index + 1, reading_index + len(variant), chunks)

        rec(0, 0, [])
        return results


def parse_annotated_chunks(annotated_surface: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for match in _FURIGANA_RE.finditer(annotated_surface):
        surface = _trailing_han_run(match.group(1))
        reading = match.group(2)
        if surface:
            chunks.append((surface, reading))
    return chunks


def _trailing_han_run(text: str) -> str:
    end = len(text)
    start = end
    while start > 0 and is_han(text[start - 1]):
        start -= 1
    return text[start:end]


def _surface_elements(surface: str) -> list[tuple[str, str]]:
    elements: list[tuple[str, str]] = []
    index = 0
    while index < len(surface):
        char = surface[index]
        if is_han(char):
            start = index
            while index < len(surface) and is_han(surface[index]):
                index += 1
            elements.append(("han", surface[start:index]))
            continue
        elements.append(("fixed", char))
        index += 1
    return elements


def _fixed_reading_variants(char: str) -> tuple[str, ...]:
    if char in {"ヶ", "ケ"}:
        return ("ケ", "カ", "ガ")
    if char == "ヵ":
        return ("カ", "ガ")
    if _is_kana(char):
        return (hira_to_kata(char),)
    if char == "ー":
        return ("ー",)
    return (char, "")


def _is_kana(char: str) -> bool:
    code = ord(char)
    return 0x3041 <= code <= 0x3096 or 0x30A1 <= code <= 0x30FA


def _minimum_remaining_reading(elements: list[tuple[str, str]]) -> int:
    minimum = 0
    for kind, value in elements:
        if kind == "han":
            minimum += 1
            continue
        variants = _fixed_reading_variants(value)
        minimum += min(len(variant) for variant in variants)
    return minimum


def _candidate_sort_key(candidate: FuriganaCandidate) -> tuple[float, str]:
    return (-candidate.score, candidate.annotated_surface)


def result_to_json_line(result: FuriganaResult) -> str:
    return json.dumps(result.to_jsonable(), ensure_ascii=False)
