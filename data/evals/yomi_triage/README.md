# Yomi Triage Eval Data

This directory contains eval rows for the first-pass yomi LLM triage task.

`ok_review_skip_draft_v1.jsonl` is a draft set built from early corpus output plus
raw skip-candidate source files.

Each row has the fields consumed by `config/llm/yomi_triage.toml`:

- `unit_id`
- `text`
- `rendered`
- `expected_status`

Additional fields such as `label_source`, `note`, and `source_artifact` are
for dataset review and prompt-error analysis. They are not included in the
production prompt.

Label policy:

- `OK` means the current annotation is acceptable as final. This includes
  stable reading variants that context cannot reliably disambiguate, such as
  `日本/ニッポン` or `私/ワタクシ`, even if another reading is more common.
- `Review` means the unit is target Japanese but should not be accepted
  automatically. This includes clear reading errors, malformed yomi, and
  locally unresolved ambiguity, even when the current reading is one possible
  reading. For example, an isolated `辛いね` should be `Review` if the unit
  itself does not decide between `カライ` and `ツライ`.
- `Skip` means non-target text that should not be yomi-repaired.

`Skip` examples should not be selected by isolated markers, but longer
non-target quotations are excluded aggressively. Modern Japanese remains in
scope when old kana, old kanji, kanbun, Chinese, or foreign text appears only in
compact titles, names, bibliographic metadata, proverbs, fixed expressions, or
short quoted phrases. If a unit contains even one full sentence of non-target
running text, label the whole unit `Skip`; modern Japanese is abundant enough
that losing the surrounding frame is preferable to creating a later review
burden over whether text is target or non-target.

If an acceptable variant repeatedly attracts unnecessary LLM criticism, prefer
mechanical normalization or a post-hybrid repair rule before triage rather than
labeling the variant as `Review`.

The current draft uses these label sources:

- `heuristic_ok_sudachi_decoder_exact_agreement`: likely OK rows where Sudachi
  and the n-gram decoder agree exactly, with obvious stale digit/Latin cases
  excluded.
- `known_or_suspected_mechanical_error`: natural early-corpus rows with known
  or high-confidence suspected reading errors.
- `synthetic_bad_reading_injected`: real early-corpus text with one intentionally
  corrupted yomi annotation. These rows exist to give the prompt optimizer more
  Review cases before enough natural reviewed examples accumulate.
- `hard_raw_skip_source_sample`: sentence-like snippets sampled from
  `raw_skip_sources/*.txt` and labeled `Skip`. The sampler prefers mixed or
  otherwise non-trivial examples and avoids easy mechanical cues such as `ゐ`
  / `ゑ`, pure all-kanji strings, or ordinary modern Japanese that merely
  mentions China.
- `targeted_unresolved_context_ambiguity`: manually curated corpus-inspired
  examples where the local unit has enough context to be nontrivial but still
  cannot safely decide the reading; these are labeled `Review`.
- `targeted_context_resolved_ambiguity_ok`: manually curated examples where the
  context resolves an ambiguous surface strongly enough that the current yomi
  should be accepted as `OK`.
- `targeted_inherently_acceptable_variant`: manually curated examples where a
  variant reading such as `日本/ニッポン` or `私/ワタクシ` should be accepted as
  `OK` rather than flagged for review.

This file should not be treated as final gold until the rows have been reviewed.

The raw skip-source files are tracked in `raw_skip_sources/` so the current
draft can be regenerated and audited.
