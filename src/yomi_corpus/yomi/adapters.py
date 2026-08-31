from __future__ import annotations

import json
import subprocess
import unicodedata

from yomi_corpus.yomi.config import YomiGenerationConfig
from yomi_corpus.yomi.furigana import is_variation_selector
from yomi_corpus.yomi.source_mapping import SourceSurfaceMappingError, SourceTextMapping
from yomi_corpus.yomi.types import DecoderCandidate, DecoderEntry, SudachiToken


def run_sudachi(
    text: str,
    config: YomiGenerationConfig,
    *,
    source_text: str | None = None,
) -> list[SudachiToken]:
    command = [config.sudachi_command, *config.sudachi_args]
    completed = subprocess.run(
        command,
        input=f"{text}\n",
        text=True,
        capture_output=True,
        check=True,
    )
    tokens = parse_sudachi_output(completed.stdout)
    if source_text is not None:
        tokens = restore_sudachi_source_surfaces(
            tokens,
            source_text=source_text,
            analysis_text=text,
        )
    return tokens


def run_sudachi_many(
    texts: list[str],
    config: YomiGenerationConfig,
    *,
    source_texts: list[str] | None = None,
) -> list[list[SudachiToken]]:
    if not texts:
        return []
    if any("\n" in text or "\r" in text for text in texts):
        raise ValueError("Sudachi batch input must contain one physical line per text")
    command = [config.sudachi_command, *config.sudachi_args]
    completed = subprocess.run(
        command,
        input="".join(f"{text}\n" for text in texts),
        text=True,
        capture_output=True,
        check=True,
    )
    documents = parse_sudachi_documents(completed.stdout)
    if len(documents) != len(texts):
        raise ValueError(
            f"Sudachi returned {len(documents)} documents for {len(texts)} input texts"
        )
    if source_texts is None:
        return documents
    if len(source_texts) != len(texts):
        raise ValueError("Sudachi source-text count must match analysis-text count")
    return [
        restore_sudachi_source_surfaces(
            tokens,
            source_text=source_text,
            analysis_text=analysis_text,
        )
        for tokens, source_text, analysis_text in zip(
            documents, source_texts, texts, strict=True
        )
    ]


def parse_sudachi_output(stdout: str) -> list[SudachiToken]:
    documents = parse_sudachi_documents(stdout)
    return [token for document in documents for token in document]


def parse_sudachi_documents(stdout: str) -> list[list[SudachiToken]]:
    documents: list[list[SudachiToken]] = []
    tokens: list[SudachiToken] = []
    # Sudachi records are LF-delimited. str.splitlines() also splits on source
    # characters such as U+0085, U+2028, and U+2029, corrupting their tokens.
    for line in stdout.split("\n"):
        if not line:
            continue
        if line == "EOS":
            documents.append(collapse_empty_surface_sudachi_tokens(tokens))
            tokens = []
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        tokens.append(
            SudachiToken(
                surface=parts[0],
                pos=parts[1],
                dictionary_form=parts[2],
                normalized_form=parts[3],
                reading=parts[4],
            )
        )
    if tokens:
        documents.append(collapse_empty_surface_sudachi_tokens(tokens))
    return documents


def collapse_empty_surface_sudachi_tokens(tokens: list[SudachiToken]) -> list[SudachiToken]:
    """Collapse source modifiers and morphemes with no independent surface."""
    collapsed: list[SudachiToken] = []
    for token in tokens:
        if token.surface and all(is_variation_selector(char) for char in token.surface):
            if not collapsed:
                raise SourceSurfaceMappingError(
                    "Sudachi returned a variation selector before any source-bearing token"
                )
            previous = collapsed[-1]
            collapsed[-1] = SudachiToken(
                surface=previous.surface + token.surface,
                pos=previous.pos,
                dictionary_form=previous.dictionary_form,
                normalized_form=previous.normalized_form,
                reading=previous.reading,
                normalization_locked=previous.normalization_locked,
            )
            continue
        if token.surface:
            collapsed.append(token)
            continue
        if not collapsed:
            raise SourceSurfaceMappingError(
                "Sudachi returned an empty surface before any source-bearing token"
            )
        previous = collapsed[-1]
        if is_compatibility_expansion_continuation(previous, token):
            meaningful_reading = token.reading not in {"", "キゴウ"}
            if meaningful_reading:
                reading = (
                    token.reading
                    if previous.reading in {"", "キゴウ"}
                    else previous.reading + token.reading
                )
            else:
                reading = previous.reading
            collapsed[-1] = SudachiToken(
                surface=previous.surface,
                pos=token.pos if meaningful_reading else previous.pos,
                dictionary_form=previous.dictionary_form + token.dictionary_form,
                normalized_form=previous.normalized_form + token.normalized_form,
                reading=reading,
                normalization_locked=previous.normalization_locked,
            )
            continue
        if token.reading in {"", "キゴウ"}:
            continue
        if not previous.pos.startswith("補助記号,"):
            raise SourceSurfaceMappingError(
                "Sudachi returned an unsupported meaningful empty-surface morpheme "
                f"after {previous.surface!r}: reading={token.reading!r}"
            )
        collapsed[-1] = SudachiToken(
            surface=previous.surface,
            pos=token.pos,
            dictionary_form=token.dictionary_form,
            normalized_form=token.normalized_form,
            reading=token.reading,
            normalization_locked=previous.normalization_locked,
        )
    return collapsed


def is_compatibility_expansion_continuation(
    previous: SudachiToken,
    continuation: SudachiToken,
) -> bool:
    """Recognize source-less pieces emitted from one compatibility character."""
    if not continuation.normalized_form:
        return False
    expanded_surface = unicodedata.normalize("NFKC", previous.surface)
    combined = previous.normalized_form + continuation.normalized_form
    return len(combined) > len(previous.normalized_form) and expanded_surface.startswith(
        combined
    )


def restore_sudachi_source_surfaces(
    tokens: list[SudachiToken],
    *,
    source_text: str,
    analysis_text: str,
) -> list[SudachiToken]:
    restored = SourceTextMapping(
        source_text=source_text,
        analysis_text=analysis_text,
    ).restore_partition(
        [token.surface for token in tokens],
        stage="Sudachi",
    )
    return [
        SudachiToken(
            surface=surface,
            pos=token.pos,
            dictionary_form=token.dictionary_form,
            normalized_form=token.normalized_form,
            reading=token.reading,
            normalization_locked=token.normalization_locked,
        )
        for token, surface in zip(tokens, restored, strict=True)
    ]


def run_decoder(
    text: str,
    config: YomiGenerationConfig,
    *,
    source_text: str | None = None,
) -> list[DecoderCandidate]:
    command = [
        config.decoder_python,
        config.decoder_script,
        "--config",
        config.decoder_config,
        "--json",
        f"--text={text}",
        "--nbest",
        str(config.decoder_nbest),
    ]
    if config.decoder_model_dir:
        command.extend(["--model-dir", config.decoder_model_dir])
    if config.decoder_beam is not None:
        command.extend(["--beam", str(config.decoder_beam)])

    completed = subprocess.run(command, text=True, capture_output=True, check=True)
    candidates = parse_decoder_output(completed.stdout)
    if source_text is not None:
        candidates = restore_decoder_source_surfaces(
            candidates,
            source_text=source_text,
            analysis_text=text,
        )
    return candidates


def restore_decoder_source_surfaces(
    candidates: list[DecoderCandidate],
    *,
    source_text: str,
    analysis_text: str,
) -> list[DecoderCandidate]:
    mapping = SourceTextMapping(source_text=source_text, analysis_text=analysis_text)
    restored_candidates: list[DecoderCandidate] = []
    for candidate in candidates:
        restored = mapping.restore_partition(
            [entry.surface for entry in candidate.entries],
            stage=f"decoder candidate {candidate.rank}",
        )
        restored_candidates.append(
            DecoderCandidate(
                rank=candidate.rank,
                score=candidate.score,
                entries=[
                    DecoderEntry(
                        surface=surface,
                        reading=entry.reading,
                        final_order=entry.final_order,
                        piece_orders=entry.piece_orders,
                    )
                    for entry, surface in zip(candidate.entries, restored, strict=True)
                ],
            )
        )
    return restored_candidates


def parse_decoder_output(stdout: str) -> list[DecoderCandidate]:
    payload = json.loads(stdout)
    results = payload.get("results", [])
    candidates: list[DecoderCandidate] = []
    for row in results:
        candidates.append(
            DecoderCandidate(
                rank=int(row["rank"]),
                score=float(row["score"]),
                entries=collapse_variation_selector_decoder_entries([
                    DecoderEntry(
                        surface=str(entry["surface"]),
                        reading=str(entry["reading"]),
                        final_order=int(entry.get("final_order", 0)),
                        piece_orders=[int(value) for value in entry.get("piece_orders", [])],
                    )
                    for entry in row.get("entries", [])
                ]),
            )
        )
    return candidates


def collapse_variation_selector_decoder_entries(
    entries: list[DecoderEntry],
) -> list[DecoderEntry]:
    collapsed: list[DecoderEntry] = []
    for entry in entries:
        if entry.surface and all(is_variation_selector(char) for char in entry.surface):
            if not collapsed:
                raise SourceSurfaceMappingError(
                    "Decoder returned a variation selector before any source-bearing entry"
                )
            previous = collapsed[-1]
            collapsed[-1] = DecoderEntry(
                surface=previous.surface + entry.surface,
                reading=previous.reading,
                final_order=max(previous.final_order, entry.final_order),
                piece_orders=[*previous.piece_orders, *entry.piece_orders],
            )
            continue
        collapsed.append(entry)
    return collapsed
