# Source-Surface Preservation Migration

## Goal

Every canonical yomi token stream must preserve the source document exactly:

- every token has a non-empty `surface`
- concatenating token surfaces reproduces the original unit text code point for
  code point
- whitespace remains a separate, readingless token and no ruby-bearing token
  crosses it
- normalization performed for Sudachi, the decoder, prompts, or editors never
  changes canonical token surfaces

For example, an ASCII space remains an ASCII space:

```json
[["今日", "キョウ"], [" ", ""], ["東京", "トウキョウ"]]
```

An original NBSP, full-width space, tab, or other whitespace character is kept
as that exact character. Structured JSON token arrays are authoritative.
Escapes such as `\s`, `\u00a0`, `\u3000`, `\/`, and `\\` belong only to the
editable text projection.

## Future Processing

Sudachi and the decoder may require a temporary analysis representation in
which ASCII spaces are replaced by NBSP. The pipeline must retain the original
text beside that representation and maintain a character-boundary mapping from
analysis offsets to source offsets. Immediately after each external tool
returns, token surfaces are reconstructed by slicing the original text. Only
readings, segmentation boundaries, and analysis metadata come from the tool.

The adapter must reject output when boundaries cannot be mapped monotonically
and unambiguously. It must not repair a mismatch by copying normalized forms or
by inserting, deleting, or reordering source characters.

Sudachi can expose one compatibility character as a visible morpheme followed
by zero-width normalized morphemes. Observed examples include `...`-like
compatibility punctuation, `℃`, and parenthesized digits. These morphemes may
inform a reading, but they must be collapsed into the one original source
surface before leaving the adapter. Empty canonical surfaces are forbidden.

Exact validation is required after:

1. Sudachi and decoder adaptation.
2. Hybrid strategy and deterministic repairs.
3. LLM result application.
4. Human-review import.
5. Finalization and corpus export.
6. Learned-lexicon and decoder-corpus harvesting.

An error should identify the track, batch, document, unit, stage, source offset,
and nearby source and reconstructed text. ASCII space and NBSP are not
equivalent at these boundaries. Equivalence is allowed only inside the private
analysis-to-source mapping layer.

## Existing Data

Migration is artifact-specific:

- Valid finalized structured token arrays are retained unchanged.
- Structured intermediate artifacts with normalized whitespace are restored
  from the row's original `text` using exact monotonic alignment.
- Legacy `surface/reading` strings are converted to structured arrays only when
  alignment is unambiguous.
- Rows with empty surfaces, ambiguous serialization, or failed alignment are
  regenerated from the earliest affected deterministic stage.

Human and LLM decisions may be retained only when all referenced source spans
still map exactly. Otherwise, only the affected downstream work is invalidated
and regenerated. Migration records the source artifact checksum, migration
version, action, and validation result. Replaced artifacts remain backed up
until a complete post-migration audit succeeds.

After canonical migration, regenerate review packs, safety projections, learned
reading candidates, decoder-corpus additions, corpus exports, and search
indexes. Rebuild N-gram models only from exports that pass exact validation.

## Rollout

1. Add strict validators and the source-boundary mapping abstraction.
2. Switch new mechanical processing to source-restored surfaces.
3. Add regression coverage for ASCII space, NBSP, full-width space, tabs,
   slashes, backslashes, compatibility punctuation, `℃`, parenthesized digits,
   and supplementary-plane kanji.
4. Compare old and new processing on representative batches, separating source
   preservation differences from reading or segmentation differences.
5. Run the migration tool in report-only mode, migrate dev artifacts, and
   verify generated review pages.
6. Rebuild derived data and run one stable dev cycle.
7. Remove obsolete whitespace-equivalence and legacy-output paths only after
   the stable cycle.
