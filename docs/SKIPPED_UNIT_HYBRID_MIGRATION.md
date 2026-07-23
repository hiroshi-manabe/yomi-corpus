# Skipped Unit Hybrid-Yomi Migration

## Decision

Skip controls corpus inclusion and review effort. It must not prevent cheap
mechanical analysis.

Every unit, including a provisional or confirmed skip, receives the normal
Sudachi and hybrid-decoder analysis. The resulting canonical yomi tokens,
candidate metadata, model identity, and skip provenance remain attached to the
unit through review and finalized archive generation.

Paid reading LLM calls, ordinary Bulk Review editing, Escalated Repair, final
corpus export, and decoder training remain suppressed while the unit is
skipped.

## Forward Pipeline Invariants

1. Scope triage annotates a unit with a provisional skip decision but does not
   remove it from the input to mechanical/hybrid yomi generation.
2. Yomi generation emits one analyzed row for every scope-triaged input row.
3. Queue builders omit skipped rows only when selecting paid LLM or ordinary
   human-review work.
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
`units.scope_triaged.jsonl` but did not reach `units.yomi.aligned_hybrid.jsonl`.
The current inventory contains 132 such rows.

Backfill procedure:

1. Enumerate historical skipped rows by stable `unit_id` from each batch's
   scope-triaged artifact.
2. Run the current mechanical/Sudachi/hybrid path for those rows without paid
   LLM calls.
3. Preserve the original scope-triage decision and source batch metadata.
4. Add or update skipped archive records without modifying finalized
   non-skipped rows.
5. Write a migration manifest containing source counts, generated counts,
   model provenance, failures, and per-unit dispositions.
6. Make reruns idempotent: a matching generated record is unchanged, while a
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
