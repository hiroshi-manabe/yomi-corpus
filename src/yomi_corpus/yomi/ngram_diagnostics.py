from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from yomi_corpus.yomi.token_codec import split_ascii_rendered_tokens
from yomi_corpus.yomi.strategies import (
    SpannedDecoderEntry,
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
TWO_KANJI_RE = re.compile(r"^[\u4e00-\u9fff]{2}$")
RENDERED_PAIR_RE = re.compile(r"(.+)/(.*)")
DEFAULT_DECODER_LEXICON_PATH = Path("../yomi-decoder/data/generated/core_SUW_lexicon.jsonl")
DEFAULT_RAW_SUDACHI_DICT_DIR = Path("data/external/sudachidict/raw/20251022/text")


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


@dataclass(frozen=True)
class RenderedEntry:
    surface: str
    reading: str


@dataclass(frozen=True)
class SpannedRenderedEntry:
    entry: RenderedEntry
    start: int
    end: int


@dataclass(frozen=True)
class StableTwoKanjiJudgment:
    value: bool
    reason: str


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


def analyze_hybrid_stable_two_kanji_support(
    *,
    batch_dir: str | Path,
    output_dir: str | Path | None = None,
    decoder_lexicon_path: str | Path | None = None,
    raw_sudachi_dict_dir: str | Path | None = None,
) -> dict[str, Any]:
    batch_path = Path(batch_dir)
    input_path = batch_path / "units.yomi.aligned_hybrid.jsonl"
    if not input_path.exists():
        raise FileNotFoundError(f"Hybrid yomi JSONL not found: {input_path}")

    output_path = Path(output_dir) if output_dir is not None else batch_path / "debug"
    output_path.mkdir(parents=True, exist_ok=True)

    rows = list(iter_jsonl(input_path))
    stable_checker = StableTwoKanjiChecker(
        rows=rows,
        decoder_lexicon_path=(
            Path(decoder_lexicon_path)
            if decoder_lexicon_path is not None
            else DEFAULT_DECODER_LEXICON_PATH
        ),
        raw_sudachi_dict_dir=(
            Path(raw_sudachi_dict_dir)
            if raw_sudachi_dict_dir is not None
            else DEFAULT_RAW_SUDACHI_DICT_DIR
        ),
    )
    span_rows: list[dict[str, Any]] = []
    unit_rows: list[dict[str, Any]] = []
    for row in rows:
        unit_result = analyze_hybrid_stable_two_kanji_row(row, stable_checker=stable_checker)
        span_rows.extend(unit_result["spans"])
        if unit_result["spans"]:
            unit_rows.append(
                {
                    "unit_id": str(row.get("unit_id", "")),
                    "baseline_pass": all(span["baseline_pass"] for span in unit_result["spans"]),
                    "relaxed_pass": all(span["relaxed_pass"] for span in unit_result["spans"]),
                }
            )

    newly_passing_spans = [span for span in span_rows if span["newly_pass"]]
    stable_counts: dict[str, int] = {}
    for span in newly_passing_spans:
        for item in span["forgiven"]:
            key = f"{item['surface']}/{item['reading']}"
            stable_counts[key] = stable_counts.get(key, 0) + 1

    summary = {
        "rule": (
            "Use hybrid rendered tokens as decision units. Project decoder top "
            "entries onto each hybrid token span. Baseline support requires exact "
            "same-span same-reading decoder support, or decoder subentries that "
            "cover the same span with concatenated reading and supported internal "
            "boundaries. The relaxed rule additionally accepts an unsupported "
            "hybrid token only when that same token is a stable two-kanji compound "
            "with exactly one reading in the raw SudachiDict CSV inventory, including "
            "component-only entries. A stable previous token does not forgive the "
            "boundary into a following non-stable token."
        ),
        "unit_count_non_alpha": len(unit_rows),
        "span_count": len(span_rows),
        "baseline_passing_spans": sum(1 for span in span_rows if span["baseline_pass"]),
        "relaxed_passing_spans": sum(1 for span in span_rows if span["relaxed_pass"]),
        "newly_passing_spans": len(newly_passing_spans),
        "baseline_passing_units_all_spans": sum(1 for row in unit_rows if row["baseline_pass"]),
        "relaxed_passing_units_all_spans": sum(1 for row in unit_rows if row["relaxed_pass"]),
        "newly_passing_units_all_spans": sum(
            1 for row in unit_rows if not row["baseline_pass"] and row["relaxed_pass"]
        ),
        "forgiven_top": sorted(stable_counts.items(), key=lambda item: (-item[1], item[0]))[:50],
    }

    write_dict_tsv(
        output_path / "hybrid_stable_two_kanji_span_experiment.tsv",
        span_rows,
        [
            "unit_id",
            "span_index",
            "span_count",
            "baseline_pass",
            "relaxed_pass",
            "newly_pass",
            "span_rendered",
            "baseline_failures",
            "relaxed_failures",
            "forgiven",
            "text",
        ],
    )
    write_dict_tsv(
        output_path / "hybrid_stable_two_kanji_newly_passing_spans.tsv",
        newly_passing_spans,
        [
            "unit_id",
            "span_index",
            "span_count",
            "baseline_pass",
            "relaxed_pass",
            "newly_pass",
            "span_rendered",
            "baseline_failures",
            "relaxed_failures",
            "forgiven",
            "text",
        ],
    )
    (output_path / "hybrid_stable_two_kanji_summary.json").write_text(
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
            "Supported decoder override candidates: exact-span alignment, non-empty "
            "decoder reading, final_order >= 2, and Sudachi/decoder reading "
            "disagreement. N-best votes are reported only as context."
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
        decoder_reading_votes = votes.get(exact_entry.reading, 0)

        rows.append(
            {
                "unit_id": str(row.get("unit_id", "")),
                "surface": token.surface,
                "sudachi_reading": token.reading,
                "decoder_reading": exact_entry.reading,
                "decoder_final_order": exact_entry.final_order,
                "decoder_piece_orders": ",".join(str(value) for value in exact_entry.piece_orders),
                "decoder_reading_votes": decoder_reading_votes,
                "votes": votes,
                "text": text,
                "current_rendered": str(yomi.get("rendered", "")),
                "decoder_top_rendered": str(
                    yomi.get("ngram_decoder", {}).get("candidates", [{}])[0].get("rendered", "")
                ),
            }
        )
    return rows


class StableTwoKanjiChecker:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        decoder_lexicon_path: Path,
        raw_sudachi_dict_dir: Path = DEFAULT_RAW_SUDACHI_DICT_DIR,
    ) -> None:
        self.raw_sudachi_readings = load_raw_sudachi_two_kanji_readings(raw_sudachi_dict_dir)
        self.bad_pairs = load_post_hybrid_repair_bad_pairs()
        self.cache: dict[tuple[str, str], StableTwoKanjiJudgment] = {}

    def judge(self, surface: str, reading: str) -> StableTwoKanjiJudgment:
        key = (surface, reading)
        if key in self.cache:
            return self.cache[key]
        judgment = self._judge_uncached(surface, reading)
        self.cache[key] = judgment
        return judgment

    def _judge_uncached(self, surface: str, reading: str) -> StableTwoKanjiJudgment:
        if not TWO_KANJI_RE.fullmatch(surface):
            return StableTwoKanjiJudgment(False, "not_two_kanji")
        if f"{surface}/{reading}" in self.bad_pairs:
            return StableTwoKanjiJudgment(False, "known_bad_repair_pair")
        raw_readings = self.raw_sudachi_readings.get(surface, set())
        if not raw_readings:
            return StableTwoKanjiJudgment(False, "missing_raw_sudachi_reading")
        if len(raw_readings) > 1:
            return StableTwoKanjiJudgment(
                False,
                "multi_reading_raw_sudachi:" + "|".join(sorted(raw_readings)),
            )
        if reading not in raw_readings:
            return StableTwoKanjiJudgment(
                False,
                "reading_mismatch_raw_sudachi:" + "|".join(sorted(raw_readings)),
            )
        return StableTwoKanjiJudgment(True, "stable_two_kanji_unique_raw_sudachi")


def analyze_hybrid_stable_two_kanji_row(
    row: dict[str, Any],
    *,
    stable_checker: Any,
) -> dict[str, list[dict[str, Any]]]:
    text = str(row.get("text", ""))
    if has_alphabetic(text):
        return {"spans": []}

    yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    decoder_candidates = [
        decoder_candidate_from_dict(candidate)
        for candidate in yomi.get("ngram_decoder", {}).get("candidates", [])[:1]
    ]
    if not decoder_candidates:
        return {"spans": []}

    try:
        hybrid_spans = split_spanned_rendered_entries_on_comma(
            span_rendered_entries(text, parse_rendered_pairs(str(yomi.get("rendered", ""))))
        )
        decoder_spans = split_spanned_decoder_entries_on_comma(
            span_decoder_entries(text, decoder_candidates[0])
        )
    except ValueError:
        return {"spans": []}

    span_count = len(hybrid_spans)
    rows: list[dict[str, Any]] = []
    for span_index, hybrid_span in enumerate(hybrid_spans, start=1):
        decoder_span = decoder_spans[span_index - 1] if span_index - 1 < len(decoder_spans) else []
        baseline_failures: list[dict[str, Any]] = []
        relaxed_failures: list[dict[str, Any]] = []
        forgiven: list[dict[str, Any]] = []
        previous_decoder_tail: SpannedDecoderEntry | None = None

        for token_index, hybrid_token in enumerate(hybrid_span):
            evidence = decoder_evidence_for_hybrid_token(
                hybrid_token=hybrid_token,
                decoder_span=decoder_span,
                is_first_token=token_index == 0,
                previous_decoder_tail=previous_decoder_tail,
            )
            previous_decoder_tail = evidence["tail"]
            if evidence["baseline_ok"]:
                continue

            failure = {
                "surface": hybrid_token.entry.surface,
                "reading": hybrid_token.entry.reading,
                "reason": evidence["reason"],
            }
            baseline_failures.append(failure)
            stable = stable_checker.judge(hybrid_token.entry.surface, hybrid_token.entry.reading)
            if stable.value:
                forgiven.append(
                    {
                        "surface": hybrid_token.entry.surface,
                        "reading": hybrid_token.entry.reading,
                        "reason": stable.reason,
                    }
                )
                continue
            relaxed_failure = dict(failure)
            relaxed_failure["stable_reason"] = stable.reason
            relaxed_failures.append(relaxed_failure)

        baseline_pass = not baseline_failures
        relaxed_pass = not relaxed_failures
        rows.append(
            {
                "unit_id": str(row.get("unit_id", "")),
                "span_index": span_index,
                "span_count": span_count,
                "baseline_pass": baseline_pass,
                "relaxed_pass": relaxed_pass,
                "newly_pass": (not baseline_pass) and relaxed_pass,
                "span_rendered": " ".join(rendered_pair(entry.entry) for entry in hybrid_span),
                "baseline_failures": baseline_failures,
                "relaxed_failures": relaxed_failures,
                "forgiven": forgiven,
                "text": text,
            }
        )
    return {"spans": rows}


def decoder_evidence_for_hybrid_token(
    *,
    hybrid_token: SpannedRenderedEntry,
    decoder_span: list[SpannedDecoderEntry],
    is_first_token: bool,
    previous_decoder_tail: SpannedDecoderEntry | None,
) -> dict[str, Any]:
    surface = hybrid_token.entry.surface
    if is_rendered_exempt(hybrid_token.entry):
        return {"baseline_ok": True, "reason": "exempt", "tail": previous_decoder_tail}

    covering = [
        entry
        for entry in decoder_span
        if entry.start >= hybrid_token.start and entry.end <= hybrid_token.end
    ]
    if not covering:
        return {"baseline_ok": False, "reason": "missing_decoder_span", "tail": previous_decoder_tail}
    if covering[0].start != hybrid_token.start or covering[-1].end != hybrid_token.end:
        return {"baseline_ok": False, "reason": "partial_decoder_span", "tail": covering[-1]}
    cursor = hybrid_token.start
    for entry in covering:
        if entry.start != cursor:
            return {"baseline_ok": False, "reason": "gapped_decoder_span", "tail": covering[-1]}
        cursor = entry.end

    decoder_surface = "".join(entry.entry.surface for entry in covering)
    decoder_reading = "".join(entry.entry.reading for entry in covering)
    if decoder_surface != surface or decoder_reading != hybrid_token.entry.reading:
        return {"baseline_ok": False, "reason": "decoder_surface_or_reading_disagreement", "tail": covering[-1]}

    boundary_ok = decoder_boundary_supported(
        first_entry=covering[0].entry,
        is_first_token=is_first_token,
    )
    if not boundary_ok:
        return {"baseline_ok": False, "reason": "unsupported_token_boundary", "tail": covering[-1]}
    for inner in covering[1:]:
        if not decoder_entry_has_previous_support(inner.entry):
            return {"baseline_ok": False, "reason": "unsupported_decoder_internal_boundary", "tail": covering[-1]}
    if any(not decoder_entry_has_ngram_support(entry.entry) for entry in covering):
        return {"baseline_ok": False, "reason": "decoder_unigram_fallback", "tail": covering[-1]}
    return {"baseline_ok": True, "reason": "decoder_projected_support", "tail": covering[-1]}


def decoder_boundary_supported(*, first_entry: DecoderEntry, is_first_token: bool) -> bool:
    if is_first_token:
        return first_entry.final_order >= 2
    return decoder_entry_has_previous_support(first_entry)


def decoder_entry_has_ngram_support(entry: DecoderEntry) -> bool:
    return entry.final_order >= 2


def decoder_entry_has_previous_support(entry: DecoderEntry) -> bool:
    return bool(entry.piece_orders) and entry.piece_orders[0] >= 2


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


def parse_rendered_pairs(rendered: str) -> list[RenderedEntry]:
    pairs: list[RenderedEntry] = []
    for raw_pair in split_ascii_rendered_tokens(rendered):
        match = RENDERED_PAIR_RE.fullmatch(raw_pair)
        if match is None:
            pairs.append(RenderedEntry(surface=raw_pair, reading=""))
            continue
        pairs.append(RenderedEntry(surface=match.group(1), reading=match.group(2)))
    return pairs


def span_rendered_entries(text: str, entries: list[RenderedEntry]) -> list[SpannedRenderedEntry]:
    spans: list[SpannedRenderedEntry] = []
    cursor = 0
    for entry in entries:
        start = text.find(entry.surface, cursor)
        if start < 0:
            raise ValueError(f"Could not align rendered surface {entry.surface!r} in text {text!r}")
        end = start + len(entry.surface)
        spans.append(SpannedRenderedEntry(entry=entry, start=start, end=end))
        cursor = end
    return spans


def split_spanned_rendered_entries_on_comma(
    entries: list[SpannedRenderedEntry],
) -> list[list[SpannedRenderedEntry]]:
    spans: list[list[SpannedRenderedEntry]] = []
    current: list[SpannedRenderedEntry] = []
    for entry in entries:
        if entry.entry.surface == "、":
            if current:
                spans.append(current)
                current = []
            continue
        current.append(entry)
    if current:
        spans.append(current)
    return spans


def split_spanned_decoder_entries_on_comma(
    entries: list[SpannedDecoderEntry],
) -> list[list[SpannedDecoderEntry]]:
    spans: list[list[SpannedDecoderEntry]] = []
    current: list[SpannedDecoderEntry] = []
    for entry in entries:
        if entry.entry.surface == "、":
            if current:
                spans.append(current)
                current = []
            continue
        current.append(entry)
    if current:
        spans.append(current)
    return spans


def rendered_pair(entry: RenderedEntry) -> str:
    return f"{entry.surface}/{entry.reading}"


def is_rendered_exempt(entry: RenderedEntry) -> bool:
    surface = entry.surface
    if not surface:
        return False
    return (
        bool(KANA_ONLY_RE.fullmatch(surface))
        or bool(NUMERIC_ONLY_RE.fullmatch(surface))
        or not (
            bool(KANJI_LIKE_RE.search(surface))
            or bool(ALPHABETIC_RE.search(surface))
        )
    )


def load_raw_sudachi_two_kanji_readings(directory: Path) -> dict[str, set[str]]:
    readings: dict[str, set[str]] = {}
    if not directory.exists():
        return readings
    for path in sorted(directory.glob("*_lex.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) <= 11:
                    continue
                surface = row[0]
                reading = row[11]
                if TWO_KANJI_RE.fullmatch(surface) and reading:
                    readings.setdefault(surface, set()).add(reading)
    return readings


def load_decoder_two_kanji_readings(path: Path) -> dict[str, set[str]]:
    readings: dict[str, set[str]] = {}
    if not path.exists():
        return readings
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            surface = str(row.get("surface", ""))
            reading = str(row.get("reading", ""))
            if not TWO_KANJI_RE.fullmatch(surface) or not reading:
                continue
            readings.setdefault(surface, set()).add(reading)
    return readings


def collect_observed_two_kanji_readings(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    readings: dict[str, set[str]] = {}
    for row in rows:
        yomi = row.get("analysis", {}).get("mechanical", {}).get("yomi", {})
        for token in yomi.get("sudachi", {}).get("tokens", []):
            add_two_kanji_reading(
                readings,
                surface=str(token.get("surface", "")),
                reading=str(token.get("reading", "")),
            )
        for candidate in yomi.get("ngram_decoder", {}).get("candidates", [])[:5]:
            for entry in candidate.get("entries", []):
                add_two_kanji_reading(
                    readings,
                    surface=str(entry.get("surface", "")),
                    reading=str(entry.get("reading", "")),
                )
    return readings


def add_two_kanji_reading(readings: dict[str, set[str]], *, surface: str, reading: str) -> None:
    if TWO_KANJI_RE.fullmatch(surface) and reading:
        readings.setdefault(surface, set()).add(reading)


def load_post_hybrid_repair_bad_pairs() -> set[str]:
    path = Path("config/yomi/post_hybrid_repairs.tsv")
    bad_pairs: set[str] = set()
    if not path.exists():
        return bad_pairs
    import csv

    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("status") != "active":
                continue
            match = re.fullmatch(r"\(\?<!\\S\)(.+)\(\?!\\S\)", str(row.get("pattern", "")))
            if match is not None:
                bad_pairs.add(match.group(1))
    return bad_pairs


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
        "decoder_reading_votes",
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
                str(row["decoder_reading_votes"]),
                json.dumps(row["votes"], ensure_ascii=False),
                str(row["text"]),
                str(row["current_rendered"]),
                str(row["decoder_top_rendered"]),
            ]
            handle.write("\t".join(sanitize_tsv(value) for value in values) + "\n")


def write_dict_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(fields) + "\n")
        for row in rows:
            values = []
            for field in fields:
                value = row.get(field, "")
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                values.append(str(value))
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
