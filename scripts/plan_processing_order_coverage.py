#!/usr/bin/env python3
"""Plan a processing-order window that improves target-headword coverage."""

from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BCCWJ_TARGETS = (
    ROOT / "data/analysis/reordering/bccwj_rare_sudachi_common_nouns_min5_headwords.txt"
)
DEFAULT_SUPPLEMENTAL_TARGETS = ROOT / "data/analysis/reordering/supplemental_headwords.txt"
DEFAULT_ORDER = ROOT / "data/pipeline/processing_order/dev.u32"
DEFAULT_ORDER_MANIFEST = ROOT / "data/pipeline/processing_order/dev.json"
DEFAULT_OUTPUT_DIR = ROOT / "data/analysis/reordering/slots_2001_4000_coverage"
KATAKANA_RE = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
KATAKANA_WORD_RE = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f]+")


@dataclass(frozen=True)
class Candidate:
    source_line_no: int
    current_slot: int
    text_chars: int
    targets: tuple[str, ...]
    preview: str


class FixedStringMatcher:
    """Small Unicode Aho-Corasick matcher for reporting matched patterns."""

    def __init__(
        self,
        patterns: list[str],
        *,
        isolated_katakana_patterns: set[str] | None = None,
    ) -> None:
        self.isolated_katakana_patterns = isolated_katakana_patterns or set()
        self.transitions: list[dict[str, int]] = [{}]
        self.failures = [0]
        self.outputs: list[list[str]] = [[]]
        for pattern in patterns:
            state = 0
            for char in pattern:
                next_state = self.transitions[state].get(char)
                if next_state is None:
                    next_state = len(self.transitions)
                    self.transitions[state][char] = next_state
                    self.transitions.append({})
                    self.failures.append(0)
                    self.outputs.append([])
                state = next_state
            self.outputs[state].append(pattern)

        queue = deque(self.transitions[0].values())
        while queue:
            state = queue.popleft()
            for char, next_state in self.transitions[state].items():
                queue.append(next_state)
                failure = self.failures[state]
                while failure and char not in self.transitions[failure]:
                    failure = self.failures[failure]
                self.failures[next_state] = self.transitions[failure].get(char, 0)
                self.outputs[next_state].extend(self.outputs[self.failures[next_state]])

    def find(self, text: str) -> set[str]:
        found: set[str] = set()
        state = 0
        for index, char in enumerate(text):
            while state and char not in self.transitions[state]:
                state = self.failures[state]
            state = self.transitions[state].get(char, 0)
            for pattern in self.outputs[state]:
                if pattern in self.isolated_katakana_patterns:
                    start = index - len(pattern) + 1
                    before = text[start - 1] if start > 0 else ""
                    after = text[index + 1] if index + 1 < len(text) else ""
                    if KATAKANA_RE.fullmatch(before) or KATAKANA_RE.fullmatch(after):
                        continue
                found.add(pattern)
        return found


def read_targets(path: Path) -> tuple[list[str], list[str]]:
    retained: list[str] = []
    excluded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()
        if not value:
            continue
        if value.startswith("."):
            if len(value) > 1:
                excluded.append(value[1:])
            continue
        retained.append(value)
    return retained, excluded


def read_order(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) % 4:
        raise ValueError(f"Processing-order file has a partial entry: {path}")
    return [value[0] for value in struct.iter_unpack("<I", data)]


def scan_candidates(
    *,
    source_path: Path,
    order: list[int],
    first_mutable_slot: int,
    targets: list[str],
    isolated_katakana_patterns: set[str],
) -> tuple[list[Candidate], int]:
    source_to_slot = {source_line: slot for slot, source_line in enumerate(order, start=1)}
    matcher = FixedStringMatcher(
        targets,
        isolated_katakana_patterns=isolated_katakana_patterns,
    )
    candidates: list[Candidate] = []
    matched_output_lines = 0

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as patterns_file:
        patterns_file.write("\n".join(targets) + "\n")
        patterns_path = Path(patterns_file.name)

    gzip_process = subprocess.Popen(
        ["gzip", "-dc", str(source_path)],
        stdout=subprocess.PIPE,
    )
    assert gzip_process.stdout is not None
    rg_process = subprocess.Popen(
        [
            "rg",
            "--text",
            "--line-number",
            "--no-filename",
            "--fixed-strings",
            "--file",
            str(patterns_path),
            "-",
        ],
        stdin=gzip_process.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    gzip_process.stdout.close()
    assert rg_process.stdout is not None
    try:
        for output_line in rg_process.stdout:
            matched_output_lines += 1
            line_number_text, raw_json = output_line.split(":", 1)
            source_line_no = int(line_number_text)
            current_slot = source_to_slot.get(source_line_no)
            if current_slot is None or current_slot < first_mutable_slot:
                continue
            payload = json.loads(raw_json)
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            matched = matcher.find(text)
            if not matched:
                continue
            preview = " ".join(text.split())[:240]
            candidates.append(
                Candidate(
                    source_line_no=source_line_no,
                    current_slot=current_slot,
                    text_chars=len(text),
                    targets=tuple(sorted(matched)),
                    preview=preview,
                )
            )
            if len(candidates) % 100_000 == 0:
                print(
                    f"scan candidates={len(candidates):,} source_line={source_line_no:,}",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        rg_process.stdout.close()
        stderr = rg_process.stderr.read() if rg_process.stderr is not None else ""
        rg_returncode = rg_process.wait()
        gzip_returncode = gzip_process.wait()
        patterns_path.unlink(missing_ok=True)
    if rg_returncode not in (0, 1):
        raise RuntimeError(f"ripgrep scan failed ({rg_returncode}): {stderr.strip()}")
    if gzip_returncode != 0:
        raise RuntimeError(f"gzip scan failed ({gzip_returncode})")
    return candidates, matched_output_lines


def select_candidates(
    candidates: list[Candidate],
    *,
    target_count: int,
    slot_count: int,
) -> tuple[list[int], Counter[str], Counter[str]]:
    availability: Counter[str] = Counter()
    for candidate in candidates:
        availability.update(candidate.targets)
    weights = {
        target: 1.0 / max(target_count, available)
        for target, available in availability.items()
    }
    selected_coverage: Counter[str] = Counter()
    selected: list[int] = []
    used: set[int] = set()
    heap: list[tuple[float, int, int, int]] = []

    def score(candidate: Candidate) -> float:
        coverage = sum(
            weights[target]
            for target in candidate.targets
            if selected_coverage[target] < target_count
        )
        length_penalty = math.sqrt(max(1.0, candidate.text_chars / 5000.0))
        return coverage / length_penalty

    for candidate_id, candidate in enumerate(candidates):
        initial_score = score(candidate)
        heapq.heappush(
            heap,
            (-initial_score, candidate.text_chars, candidate.source_line_no, candidate_id),
        )

    while heap and len(selected) < slot_count:
        negative_old_score, _, _, candidate_id = heapq.heappop(heap)
        if candidate_id in used:
            continue
        candidate = candidates[candidate_id]
        current_score = score(candidate)
        old_score = -negative_old_score
        if current_score <= 0:
            # A stale high-scoring entry can become exhausted while useful
            # lower-scoring candidates still remain deeper in the heap.
            continue
        if not math.isclose(current_score, old_score, rel_tol=1e-12, abs_tol=1e-15):
            heapq.heappush(
                heap,
                (-current_score, candidate.text_chars, candidate.source_line_no, candidate_id),
            )
            continue
        used.add(candidate_id)
        selected.append(candidate_id)
        for target in candidate.targets:
            if selected_coverage[target] < target_count:
                selected_coverage[target] += 1

    if len(selected) < slot_count:
        remaining = sorted(
            (candidate_id for candidate_id in range(len(candidates)) if candidate_id not in used),
            key=lambda candidate_id: candidates[candidate_id].current_slot,
        )
        selected.extend(remaining[: slot_count - len(selected)])
    return selected, availability, selected_coverage


def write_reports(
    *,
    output_dir: Path,
    candidates: list[Candidate],
    selected_ids: list[int],
    targets: list[str],
    target_sources: dict[str, list[str]],
    availability: Counter[str],
    selected_coverage: Counter[str],
    slot_start: int,
    target_count: int,
    metadata: dict[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = [candidates[candidate_id] for candidate_id in selected_ids]

    coverage_path = output_dir / "target_coverage.tsv"
    with coverage_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["target", "source_lists", "available_documents", "selected_documents", "goal_met"]
        )
        for target in targets:
            writer.writerow(
                [
                    target,
                    "|".join(target_sources[target]),
                    availability[target],
                    selected_coverage[target],
                    "yes" if selected_coverage[target] >= target_count else "no",
                ]
            )

    documents_path = output_dir / "selected_documents.tsv"
    with documents_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "assigned_slot",
                "source_line_no",
                "current_slot",
                "text_chars",
                "matched_target_count",
                "matched_targets",
                "preview",
            ]
        )
        for offset, candidate in enumerate(selected):
            writer.writerow(
                [
                    slot_start + offset,
                    candidate.source_line_no,
                    candidate.current_slot,
                    candidate.text_chars,
                    len(candidate.targets),
                    "|".join(candidate.targets),
                    candidate.preview,
                ]
            )

    plan = {
        **metadata,
        "selected_source_line_nos": [candidate.source_line_no for candidate in selected],
        "selected_current_slots": [candidate.current_slot for candidate in selected],
    }
    (output_dir / "selection_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bccwj-targets", type=Path, default=DEFAULT_BCCWJ_TARGETS)
    parser.add_argument("--supplemental-targets", type=Path, default=DEFAULT_SUPPLEMENTAL_TARGETS)
    parser.add_argument("--order", type=Path, default=DEFAULT_ORDER)
    parser.add_argument("--order-manifest", type=Path, default=DEFAULT_ORDER_MANIFEST)
    parser.add_argument("--slot-start", type=int, default=2001)
    parser.add_argument("--slot-end", type=int, default=4000)
    parser.add_argument("--target-count", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if args.slot_start < 1 or args.slot_end < args.slot_start:
        parser.error("invalid slot range")
    order_manifest = json.loads(args.order_manifest.read_text(encoding="utf-8"))
    if int(order_manifest["cursor"]) > args.slot_start:
        parser.error("slot range starts before the processing-order cursor")
    if order_manifest.get("reservation") is not None:
        parser.error("processing order has an active reservation")

    bccwj_targets, bccwj_excluded = read_targets(args.bccwj_targets)
    supplemental_targets, supplemental_excluded = read_targets(args.supplemental_targets)
    target_sources: dict[str, list[str]] = {}
    for source_name, source_targets in (
        ("bccwj", bccwj_targets),
        ("supplemental", supplemental_targets),
    ):
        for target in source_targets:
            target_sources.setdefault(target, []).append(source_name)
    targets = sorted(target_sources)
    if not targets:
        parser.error("target lists are empty")

    order = read_order(args.order)
    if len(order) != int(order_manifest["document_count"]):
        parser.error("processing-order binary and manifest disagree")
    source_path = Path(order_manifest["source_path"])
    print(
        f"scanning targets={len(targets):,} documents={len(order):,} source={source_path}",
        file=sys.stderr,
        flush=True,
    )
    candidates, matched_output_lines = scan_candidates(
        source_path=source_path,
        order=order,
        first_mutable_slot=args.slot_start,
        targets=targets,
        isolated_katakana_patterns={
            target
            for target in supplemental_targets
            if KATAKANA_WORD_RE.fullmatch(target)
        },
    )
    print(f"selecting from candidates={len(candidates):,}", file=sys.stderr, flush=True)
    slot_count = args.slot_end - args.slot_start + 1
    selected_ids, availability, selected_coverage = select_candidates(
        candidates,
        target_count=args.target_count,
        slot_count=slot_count,
    )
    if len(selected_ids) < slot_count:
        parser.error(f"only {len(selected_ids)} matching documents available for {slot_count} slots")

    goal_met = sum(selected_coverage[target] >= args.target_count for target in targets)
    unavailable = sum(availability[target] == 0 for target in targets)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "dry_run": True,
        "slot_start": args.slot_start,
        "slot_end": args.slot_end,
        "slot_count": slot_count,
        "target_document_count": args.target_count,
        "bccwj_target_count": len(bccwj_targets),
        "bccwj_excluded_count": len(bccwj_excluded),
        "supplemental_target_count": len(supplemental_targets),
        "supplemental_excluded_count": len(supplemental_excluded),
        "distinct_target_count": len(targets),
        "ripgrep_matching_line_count": matched_output_lines,
        "eligible_candidate_document_count": len(candidates),
        "targets_meeting_goal": goal_met,
        "targets_below_goal": len(targets) - goal_met,
        "targets_unavailable": unavailable,
        "processing_order_generation": order_manifest["order_generation"],
        "processing_order_cursor": order_manifest["cursor"],
        "source_path": str(source_path),
    }
    write_reports(
        output_dir=args.output_dir,
        candidates=candidates,
        selected_ids=selected_ids,
        targets=targets,
        target_sources=target_sources,
        availability=availability,
        selected_coverage=selected_coverage,
        slot_start=args.slot_start,
        target_count=args.target_count,
        metadata=metadata,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
