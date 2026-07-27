# Skipped Unit Hybrid-Yomi Migration

## Decision

Skip controls corpus inclusion and review effort. It must not prevent cheap
mechanical analysis.

Every unit, including a provisional or confirmed skip, receives the normal
Sudachi and hybrid-decoder analysis. The resulting canonical yomi tokens,
candidate metadata, model identity, and skip provenance remain attached to the
unit through review and finalized archive generation.

Before human confirmation, paid reading LLM calls and ordinary Bulk Review
preparation are identical for `Keep`, `Skip`, and `Exclude`. Confirmed skips
remain excluded from final corpus export and decoder training.

This document's recoverable `Skip` state is not the terminal `Exclude` state
used for sensitive material. A machine-proposed exclusion follows the same
cheap-analysis path only until human confirmation. Once confirmed, its original
content is removed from published review/archive/search artifacts and it cannot
be restored through Corpus Map. Finalized browsing may retain a content-free
`Removed` tombstone containing stable identity, order, reason category, and
confirmation provenance only.

## Forward Pipeline Invariants

1. Scope triage annotates a unit with a provisional skip decision but does not
   remove it from the input to mechanical/hybrid yomi generation.
2. Yomi generation emits one analyzed row for every scope-triaged input row.
3. Reading queue builders never inspect scope status. They apply only normal
   deterministic safety and auto-accept rules.
4. Bulk Review retains provisional skips with `Skip` preselected and renders
   their hybrid ruby like any other sentence.
5. A confirmed skip becomes a durable skipped archive record. It retains its
   hybrid yomi but is excluded from finalized corpus and decoder-training
   exports.
6. Skip provenance and hybrid-analysis provenance are independent. Restoring a
   unit does not erase the historical skip event or the model version that
   produced its starting yomi.

## Corpus Map Restoration

Corpus Map renders a confirmed skipped unit as ordinary ruby text with subdued
styling and a `Skipped` badge. It does not expose raw analysis by default.

The normal `Edit` action is replaced with `Restore and Edit` for skipped units.
That action opens the existing finalized-correction editor, initialized from
the preserved hybrid canonical tokens. Cancelling is non-destructive. Saving
creates a correction payload containing both the edited canonical yomi and
`skip: false`.

Applying that correction atomically moves the unit from the skipped archive to
finalized corpus data. It does not return the unit to Bulk Review. The explicit
human restoration and source-level correction are the review event. Repeated
issue synchronization must be idempotent, and the archive must retain the
original skip plus restoration provenance.

## Schema Requirements

A skipped archive unit must retain at least:

- document and unit identity, sequence, source location, and text
- canonical hybrid yomi tokens and ruby-rendering tokens
- hybrid-analysis/model provenance sufficient to identify the generated
  starting point
- effective skip state, provisional reasons, and confirmation provenance
- later restoration/correction submission IDs and application history

The browser correction payload must distinguish an ordinary finalized-yomi
edit from restoration by carrying the requested skip state explicitly. Server
validation remains authoritative and applies the same canonical-token checks
used by ordinary finalized corrections.

## Historical Backfill

Historical scope-triage skips predate this invariant. They remain in
`units.scope_triaged.jsonl` but originally did not reach
`units.yomi.aligned_hybrid.jsonl`. Historical human-confirmed skips were
likewise removed from finalized output without being retained in a skipped
archive. The migration must recover both classes.

Backfill procedure:

1. Enumerate scope-triage skips by stable `unit_id`. For finalized batches,
   replay the saved review pack and all submissions with the normal replay
   implementation to derive the effective human skip decisions.
2. Run the current mechanical/Sudachi/hybrid path for those rows without paid
   LLM calls.
3. Prefer the reviewed unit artifact for a human-confirmed skip so its winning
   submission ID, timestamp, target decisions, and source batch metadata are
   preserved. Use aligned hybrid output as the fallback source.
4. Add or update skipped archive records without modifying finalized
   non-skipped rows.
5. Write a migration manifest containing source counts, generated counts,
   model provenance, failures, and per-unit dispositions.
6. Treat a scope-skipped unit that a later human action restored into the final
   corpus as restored, not as a conflict or a candidate for re-skipping.
7. Make reruns idempotent: a matching generated record is unchanged, while a
   conflicting record is reported rather than silently replaced.

Punctuation-only and other low-value historical skips remain present for
document-order fidelity but may use unobtrusive rendering.

## Delivery Order

1. Introduce forward-compatible skipped-unit and restoration schemas.
2. Stop filtering skipped rows before hybrid generation.
3. Preserve hybrid yomi in review packs and skipped archive records.
4. Add Corpus Map `Restore and Edit` behavior and correction application.
5. Add focused pipeline, archive, correction, and UI tests.
6. Run the historical backfill, verify counts and unit IDs, then publish.

Before historical mutation, retain a backup or immutable migration manifest of
the affected artifacts. Local browser DOM verification should confirm ruby
rendering, subdued skipped styling, cancellation, correction export, and the
post-application restored state.
