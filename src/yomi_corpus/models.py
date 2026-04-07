from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BooleanJudgment:
    value: bool | None
    certain: bool
    signals: list[str] = field(default_factory=list)
    matches: list[str] = field(default_factory=list)


@dataclass
class MechanicalYomi:
    rendered: str
    certain: bool
    sudachi: dict[str, Any] = field(default_factory=dict)
    ngram_decoder: dict[str, Any] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)


@dataclass
class MechanicalAnalysis:
    classical_japanese: BooleanJudgment
    minor_alphabetic_sequence: BooleanJudgment
    yomi: MechanicalYomi


@dataclass
class UnitAnalysis:
    mechanical: MechanicalAnalysis
    llm: dict[str, Any] = field(
        default_factory=lambda: {
            "classical_japanese": None,
            "minor_alphabetic_sequence": None,
            "yomi_is_correct": None,
            "yomi_repair": None,
        }
    )
    human_review: dict[str, Any] = field(
        default_factory=lambda: {
            "pass1": None,
            "pass2": None,
            "final_edit": None,
        }
    )


@dataclass
class UnitRecord:
    doc_id: str
    unit_id: str
    unit_seq: int
    char_start: int
    char_end: int
    text: str
    source_file: str
    source_line_no: int
    analysis: UnitAnalysis

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_analysis() -> UnitAnalysis:
    return UnitAnalysis(
        mechanical=MechanicalAnalysis(
            classical_japanese=BooleanJudgment(value=None, certain=False),
            minor_alphabetic_sequence=BooleanJudgment(value=None, certain=False),
            yomi=MechanicalYomi(rendered="", certain=False),
        )
    )
