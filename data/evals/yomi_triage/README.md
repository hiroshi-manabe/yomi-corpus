# Yomi Triage Eval Data

This directory contains eval rows for the first-pass yomi LLM triage task.

`ok_fix_draft_v1.jsonl` is a draft set built from early corpus output plus
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
- `FIX` means the unit should remain in the repair/review path. This includes
  clear reading errors and locally resolvable ambiguity, even when the current
  reading is one possible reading. For example, an isolated `辛いね` should be
  `FIX` if the unit itself does not decide between `カライ` and `ツライ`.
- `SKIP` means non-target text that should not be yomi-repaired.

If an acceptable variant repeatedly attracts unnecessary LLM criticism, prefer
mechanical normalization or a post-hybrid repair rule before triage rather than
labeling the variant as `FIX`.

The current draft uses four label sources:

- `heuristic_ok_sudachi_decoder_exact_agreement`: likely OK rows where Sudachi
  and the n-gram decoder agree exactly, with obvious stale digit/Latin cases
  excluded.
- `known_or_suspected_mechanical_error`: natural early-corpus rows with known
  or high-confidence suspected reading errors.
- `synthetic_bad_reading_injected`: real early-corpus text with one intentionally
  corrupted yomi annotation. These rows exist to give the prompt optimizer more
  FIX cases before enough natural reviewed FIX cases accumulate.
- `hard_raw_skip_source_sample`: sentence-like snippets sampled from
  `raw_skip_sources/*.txt` and labeled `SKIP`. The sampler prefers mixed or
  otherwise non-trivial examples and avoids easy mechanical cues such as `ゐ`
  / `ゑ`, pure all-kanji strings, or ordinary modern Japanese that merely
  mentions China.

This file should not be treated as final gold until the rows have been reviewed.

The raw skip-source files are tracked in `raw_skip_sources/` so the current
draft can be regenerated and audited.
