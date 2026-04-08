from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SudachiToken:
    surface: str
    pos: str
    dictionary_form: str
    normalized_form: str
    reading: str


@dataclass(frozen=True)
class DecoderEntry:
    surface: str
    reading: str
    final_order: int
    piece_orders: list[int]


@dataclass(frozen=True)
class DecoderCandidate:
    rank: int
    score: float
    entries: list[DecoderEntry]


@dataclass(frozen=True)
class YomiStrategyResult:
    strategy: str
    rendered: str
    certain: bool
    signals: list[str]
