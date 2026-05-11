from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from yomi_corpus.yomi.strategies import (
    is_punctuation_token,
    span_decoder_entries,
    span_sudachi_tokens,
)
from yomi_corpus.yomi.types import (
    DecoderCandidate,
    DecoderEntry,
    SudachiToken,
)


ALPHABETIC_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]")
KANJI_LIKE_RE = re.compile(r"[\u3400-\u9fff々〆〻]")
KANA_ONLY_RE = re.compile(r"^[ぁ-ゖァ-ヺーゝゞヽヾ]+$")
NUMERIC_ONLY_RE = re.compile(r"^[0-9０-９]+$")


@dataclass(frozen=True)
class EntryInfo:
    surface: str
    reading: str
    piece_orders: list[int]

    @property
    def kana_only(self) -> bool:
        return bool(KANA_ONLY_RE.fullmatch(self.surface))

    @property
    def numeric(self) -> bool:
        return bool(NUMERIC_ONLY_RE.fullmatch(self.surface))

    @property
    def kanji_like(self) -> bool:
        return bool(KANJI_LIKE_RE.search(self.surface))

    @property
    def symbol_only(self) -> bool:
        if not self.surface:
            return False
        return not (
            self.kana_only
            or self.numeric
            or self.kanji_like
            or bool(ALPHABETIC_RE.search(self.surface))
        )

    @property
    def exempt(self) -> bool:
        return self.kana_only or self.numeric or self.symbol_only

    @property
    def char_len(self) -> int:
        return len(self.surface)


@dataclass(frozen=True)
class SpanDiagnostic:
    unit_id: str
    span_index: int
    span_count: int
    passed: bool
    entry_count: int
    checked_entry_count: int
    span_surface: str
    span_rendered: str
    failure_count: int
    failures: list[dict[str, Any]]
    unit_text: str
    unit_rendered: str
    entry_summary: list[dict[str, Any]]
    span_char_count: int
    kanji_like_char_count: int


def analyze_batch_ngram_support(
    *,
    batch_dir: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    batch_path = Path(batch_dir)
    input_path = batch_path / "units.yomi.aligned_hybrid.jsonl"
    if not input_path.exists():
        raise FileNotFoundError(f"Hybrid yomi JSONL not found: {input_path}")

    output_path = Path(output_dir) if output_dir is not None else batch_path / "debug"
    output_path.mkdir(parents=True, exist_ok=True)

    rows = list(iter_jsonl(input_path))
    span_rows: list[SpanDiagnostic] = []
    for row in rows:
        span_rows.extend(analyze_row(row))

    passing_spans = [row for row in span_rows if row.passed]
    passing_units = sorted({row.unit_id for row in passing_spans})
    unit_ids = [str(row.get("unit_id", "")) for row in rows]
    non_alpha_unit_ids = [
        str(row.get("unit_id", ""))
        for row in rows
        if not has_alphabetic(str(row.get("text", "")))
    ]

    summary = summarize_rows(
        unit_ids=unit_ids,
        non_alpha_unit_ids=non_alpha_unit_ids,
        spans=span_rows,
        passing_units=passing_units,
    )

    write_span_tsv(output_path / "ngram_comma_kana_symbol_span_coverage.tsv", span_rows)
    write_span_tsv(output_path / "ngram_comma_kana_symbol_passing_spans.tsv", passing_spans)
    write_passing_units_tsv(
        output_path / "ngram_comma_kana_symbol_passing_units.tsv",
        rows=rows,
        passing_unit_ids=passing_units,
    )
    (output_path / "ngram_comma_kana_symbol_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def analyze_override_without_whitelist(
    *,
    batch_dir: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    batch_path = Path(batch_dir)
    input_path = batch_path / "units.yomi.aligned_hybrid.jsonl"
    if not input_path.exists():
        raise FileNotFoundError(f"Hybrid yomi JSONL not found: {input_path}")

    output_path = Path(output_dir) if output_dir is not None else batch_path / "debug"
    output_path.mkdir(parents=True, exist_ok=True)

    rows = list(iter_jsonl(input_path))
    candidate_rows: list[dict[str, Any]] = []
    affected_units: set[str] = set()
    for row in rows:
        row_candidates = override_candidates_for_row(row)
        candidate_rows.extend(row_candidates)
        affected_units.update(str(candidate["unit_id"]) for candidate in row_candidates)

    summary = {
        "rule": (
            "Diagnostic only: remove the DECODER_OVERRIDE_SURFACES surface whitelist, "
            "but keep exact-span alignment, non-empty decoder reading, final_order >= 2, "
            "Sudachi/decoder reading disagreement, and >=2 winning decoder-candidate votes."
        ),
        "unit_count": len(rows),
        "candidate_count": len(candidate_rows),
        "affected_unit_count": len(affected_units),
    }

    write_override_candidates_tsv(
        output_path / "override_without_whitelist_candidates.tsv",
        candidate_rows,
    )
    (output_path / "override_without_whitelist_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def override_candidates_for_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(row.get("text", ""))
    yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    sudachi_tokens = [
        sudachi_token_from_dict(token)
        for token in yomi.get("sudachi", {}).get("tokens", [])
    ]
    decoder_candidates = [
        decoder_candidate_from_dict(candidate)
        for candidate in yomi.get("ngram_decoder", {}).get("candidates", [])
    ]
    if not sudachi_tokens or not decoder_candidates:
        return []

    sudachi_spans = span_sudachi_tokens(text, sudachi_tokens)
    decoder_spans_by_rank = [span_decoder_entries(text, candidate) for candidate in decoder_candidates]
    top_decoder_by_span = {
        (entry.start, entry.end): entry.entry
        for entry in decoder_spans_by_rank[0]
    }

    rows = []
    for token_span in sudachi_spans:
        token = token_span.token
        if is_punctuation_token(token):
            continue
        exact_entry = top_decoder_by_span.get((token_span.start, token_span.end))
        if exact_entry is None:
            continue
        if not exact_entry.reading:
            continue
        if exact_entry.final_order < 2:
            continue
        if token.reading == exact_entry.reading:
            continue

        votes = votes_for_span(
            all_decoder_spans=decoder_spans_by_rank,
            start=token_span.start,
            end=token_span.end,
        )
        if not votes:
            continue
        winning_reading, winning_votes = max(votes.items(), key=lambda item: (item[1], item[0]))
        if winning_reading != exact_entry.reading or winning_votes < 2:
            continue

        rows.append(
            {
                "unit_id": str(row.get("unit_id", "")),
                "surface": token.surface,
                "sudachi_reading": token.reading,
                "decoder_reading": exact_entry.reading,
                "decoder_final_order": exact_entry.final_order,
                "decoder_piece_orders": ",".join(str(value) for value in exact_entry.piece_orders),
                "winning_votes": winning_votes,
                "votes": votes,
                "text": text,
                "current_rendered": str(yomi.get("rendered", "")),
                "decoder_top_rendered": str(
                    yomi.get("ngram_decoder", {}).get("candidates", [{}])[0].get("rendered", "")
                ),
            }
        )
    return rows


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def sudachi_token_from_dict(token: dict[str, Any]) -> SudachiToken:
    return SudachiToken(
        surface=str(token.get("surface", "")),
        pos=str(token.get("pos", "")),
        dictionary_form=str(token.get("dictionary_form", "")),
        normalized_form=str(token.get("normalized_form", "")),
        reading=str(token.get("reading", "")),
    )


def decoder_candidate_from_dict(candidate: dict[str, Any]) -> DecoderCandidate:
    return DecoderCandidate(
        rank=int(candidate.get("rank", 0)),
        score=float(candidate.get("score", 0.0)),
        entries=[
            DecoderEntry(
                surface=str(entry.get("surface", "")),
                reading=str(entry.get("reading", "")),
                final_order=int(entry.get("final_order", 1)),
                piece_orders=[int(value) for value in entry.get("piece_orders", [])],
            )
            for entry in candidate.get("entries", [])
        ],
    )


def votes_for_span(
    *,
    all_decoder_spans: list[list[Any]],
    start: int,
    end: int,
) -> dict[str, int]:
    votes: dict[str, int] = {}
    for candidate_spans in all_decoder_spans:
        for entry in candidate_spans:
            if entry.start == start and entry.end == end and entry.entry.reading:
                votes[entry.entry.reading] = votes.get(entry.entry.reading, 0) + 1
                break
    return votes


def analyze_row(row: dict[str, Any]) -> list[SpanDiagnostic]:
    unit_id = str(row.get("unit_id", ""))
    unit_text = str(row.get("text", ""))
    if has_alphabetic(unit_text):
        return []

    yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    candidates = yomi.get("ngram_decoder", {}).get("candidates", [])
    if not candidates:
        return []
    entries = [
        EntryInfo(
            surface=str(entry.get("surface", "")),
            reading=str(entry.get("reading", "")),
            piece_orders=[int(value) for value in entry.get("piece_orders", [])],
        )
        for entry in candidates[0].get("entries", [])
    ]

    spans = split_entries_on_comma(entries)
    diagnostics = []
    for index, span in enumerate(spans, start=1):
        diagnostics.append(
            analyze_span(
                unit_id=unit_id,
                span_index=index,
                span_count=len(spans),
                span=span,
                unit_text=unit_text,
                unit_rendered=str(yomi.get("rendered", "")),
            )
        )
    return diagnostics


def split_entries_on_comma(entries: list[EntryInfo]) -> list[list[EntryInfo]]:
    spans: list[list[EntryInfo]] = []
    current: list[EntryInfo] = []
    for entry in entries:
        if entry.surface == "、":
            if current:
                spans.append(current)
                current = []
            continue
        current.append(entry)
    if current:
        spans.append(current)
    return spans


def analyze_span(
    *,
    unit_id: str,
    span_index: int,
    span_count: int,
    span: list[EntryInfo],
    unit_text: str,
    unit_rendered: str,
) -> SpanDiagnostic:
    failures: list[dict[str, Any]] = []
    checked_entry_count = 0
    for index in range(1, len(span)):
        previous = span[index - 1]
        current = span[index]
        if previous.exempt and current.exempt:
            continue
        checked_entry_count += 1
        first_order = current.piece_orders[0] if current.piece_orders else 1
        if first_order < 2:
            failures.append(
                {
                    "index": index,
                    "kind": "unsupported_boundary",
                    "prev": previous.surface,
                    "current": current.surface,
                    "current_orders": ",".join(str(value) for value in current.piece_orders),
                    "prev_exempt": previous.exempt,
                    "current_exempt": current.exempt,
                }
            )

    return SpanDiagnostic(
        unit_id=unit_id,
        span_index=span_index,
        span_count=span_count,
        passed=not failures,
        entry_count=len(span),
        checked_entry_count=checked_entry_count,
        span_surface="".join(entry.surface for entry in span),
        span_rendered=render_entries(span),
        failure_count=len(failures),
        failures=failures,
        unit_text=unit_text,
        unit_rendered=unit_rendered,
        entry_summary=[entry_to_summary(entry) for entry in span],
        span_char_count=sum(entry.char_len for entry in span),
        kanji_like_char_count=sum(entry.char_len for entry in span if entry.kanji_like),
    )


def summarize_rows(
    *,
    unit_ids: list[str],
    non_alpha_unit_ids: list[str],
    spans: list[SpanDiagnostic],
    passing_units: list[str],
) -> dict[str, Any]:
    total_span_chars = sum(row.span_char_count for row in spans)
    passing_span_chars = sum(row.span_char_count for row in spans if row.passed)
    total_kanji_like_chars = sum(row.kanji_like_char_count for row in spans)
    passing_kanji_like_chars = sum(row.kanji_like_char_count for row in spans if row.passed)
    return {
        "rule": (
            "Skip alphabetic units; split decoder top candidate only on Japanese comma "
            "'、'; exempt kana-only, numeric-only, and symbol-only adjacent boundaries; "
            "otherwise require the later entry's first piece order to be >= 2."
        ),
        "unit_count": len(unit_ids),
        "non_alphabetic_unit_count": len(non_alpha_unit_ids),
        "span_count": len(spans),
        "passing_span_count": sum(1 for row in spans if row.passed),
        "passing_span_rate": safe_ratio(sum(1 for row in spans if row.passed), len(spans)),
        "units_with_passing_span_count": len(passing_units),
        "units_with_passing_span_rate_all_units": safe_ratio(len(passing_units), len(unit_ids)),
        "units_with_passing_span_rate_non_alphabetic_units": safe_ratio(
            len(passing_units),
            len(non_alpha_unit_ids),
        ),
        "span_char_count": total_span_chars,
        "passing_span_char_count": passing_span_chars,
        "passing_span_char_rate": safe_ratio(passing_span_chars, total_span_chars),
        "kanji_like_span_char_count": total_kanji_like_chars,
        "passing_kanji_like_span_char_count": passing_kanji_like_chars,
        "passing_kanji_like_span_char_rate": safe_ratio(
            passing_kanji_like_chars,
            total_kanji_like_chars,
        ),
    }


def write_span_tsv(path: Path, rows: list[SpanDiagnostic]) -> None:
    fields = [
        "unit_id",
        "span_index",
        "span_count",
        "pass",
        "entry_count",
        "checked_entry_count",
        "span_char_count",
        "kanji_like_char_count",
        "span_surface",
        "span_rendered",
        "failure_count",
        "failures",
        "unit_text",
        "unit_rendered",
        "entry_summary",
    ]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(fields) + "\n")
        for row in rows:
            values = [
                row.unit_id,
                str(row.span_index),
                str(row.span_count),
                str(row.passed),
                str(row.entry_count),
                str(row.checked_entry_count),
                str(row.span_char_count),
                str(row.kanji_like_char_count),
                row.span_surface,
                row.span_rendered,
                str(row.failure_count),
                json.dumps(row.failures, ensure_ascii=False),
                row.unit_text,
                row.unit_rendered,
                json.dumps(row.entry_summary, ensure_ascii=False),
            ]
            handle.write("\t".join(sanitize_tsv(value) for value in values) + "\n")


def write_passing_units_tsv(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    passing_unit_ids: list[str],
) -> None:
    rows_by_id = {str(row.get("unit_id", "")): row for row in rows}
    with path.open("w", encoding="utf-8") as handle:
        handle.write("unit_id\ttext\trendered\n")
        for unit_id in passing_unit_ids:
            row = rows_by_id.get(unit_id, {})
            yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi", {})
            handle.write(
                "\t".join(
                    sanitize_tsv(value)
                    for value in [
                        unit_id,
                        str(row.get("text", "")),
                        str(yomi.get("rendered", "")),
                    ]
                )
                + "\n"
            )


def write_override_candidates_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "unit_id",
        "surface",
        "sudachi_reading",
        "decoder_reading",
        "decoder_final_order",
        "decoder_piece_orders",
        "winning_votes",
        "votes",
        "text",
        "current_rendered",
        "decoder_top_rendered",
    ]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(fields) + "\n")
        for row in rows:
            values = [
                str(row["unit_id"]),
                str(row["surface"]),
                str(row["sudachi_reading"]),
                str(row["decoder_reading"]),
                str(row["decoder_final_order"]),
                str(row["decoder_piece_orders"]),
                str(row["winning_votes"]),
                json.dumps(row["votes"], ensure_ascii=False),
                str(row["text"]),
                str(row["current_rendered"]),
                str(row["decoder_top_rendered"]),
            ]
            handle.write("\t".join(sanitize_tsv(value) for value in values) + "\n")


def entry_to_summary(entry: EntryInfo) -> dict[str, Any]:
    return {
        "surface": entry.surface,
        "reading": entry.reading,
        "kana_only": entry.kana_only,
        "symbol_only": entry.symbol_only,
        "exempt": entry.exempt,
        "numeric": entry.numeric,
        "kanji_like": entry.kanji_like,
        "piece_orders": entry.piece_orders,
    }


def render_entries(entries: list[EntryInfo]) -> str:
    return " ".join(f"{entry.surface}/{entry.reading}" for entry in entries)


def has_alphabetic(text: str) -> bool:
    return bool(ALPHABETIC_RE.search(text))


def safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def sanitize_tsv(value: str) -> str:
    return value.replace("\t", " ").replace("\r", "\\r").replace("\n", "\\n")
