# Bulk Review Row-Level Direct-Edit Migration

Status: proposed; implementation has not started.

## Motivation

Bulk Review currently gives a reviewer two practical outcomes for a kept row:

- accept the current rendered yomi; or
- cancel one or more readings and send the resulting local target groups to
  Escalated Repair.

Some problems are already recognizable during Bulk Review as unsuitable for
Escalated Repair. Examples include token-boundary failures, several interacting
local errors, or a case where the reviewer can write the correct canonical yomi
more quickly than describing uncertainty to another model. The current
workaround is to carry a red manual-correction flag through finalization and
edit the finalized document later in Corpus Map. That preserves correctness but
adds an avoidable review cycle and makes the flag serve as deferred work rather
than an exceptional fallback.

Add a third row-level resolution route to Bulk Review: edit the canonical yomi
for that row directly and treat the row as resolved by the Bulk Review
submission.

## Decision

Routing is per review row, where a row corresponds to one stable pipeline unit,
normally a sentence. A single document may contain all three routes:

1. `accept`: retain the current canonical yomi;
2. `escalate`: send only the rejected local target groups to Escalated Repair;
3. `direct_edit`: replace the row's canonical yomi with human-edited structured
   tokens and resolve that row immediately.

This is not a fourth document queue and not a new top-level Issue category.
Direct edits originate in Bulk Review and travel in the same `[Bulk Review]`
Issue as ordinary acceptance and escalation decisions.

Human `Skip` and terminal `Exclude` remain disposition decisions. For a kept
row, the resolution route is `accept`, `escalate`, or `direct_edit`. A skipped
or excluded row does not additionally need a yomi resolution route.

## Terminology and Data Boundary

The UI may use a concise label such as `Edit yomi directly`. Internally, this
does not edit immutable raw source text. It edits the canonical compact yomi
token sequence:

```json
[["今日", "キョウ"], ["は", "ハ"], ["晴れ", "ハレ"], ["。", "。"]]
```

The concatenated token surfaces must exactly reproduce the original unit text,
including whitespace and punctuation. Sentence-boundary and raw-text editing
remain separate future work.

## Review UI

### Entry point

Each Bulk Review row should offer a direct-edit action alongside the existing
ruby interaction and row controls. It should be visually secondary because
ordinary acceptance is expected to dominate, but it must not require carrying
the row through Escalated Repair or Corpus Map.

Opening direct edit should reuse the established finalized-correction editor
where practical:

- initialize it from the row's current canonical yomi tokens;
- use a wrapping textarea rather than horizontal scrolling;
- normalize hiragana readings to katakana on save;
- run the same client-side validation used by finalized correction;
- show the resulting ruby preview after saving;
- allow the saved edit to be reopened and changed before submission;
- warn before discarding unsaved edits; and
- persist drafts in browser storage with the containing Bulk Review task.

The row should visibly distinguish a saved direct edit from both an untouched
accepted row and an escalated row. Saving a direct edit clears escalation
targets for that row because the structured replacement supersedes them.
Conversely, changing the row back to Escalated Repair must discard the saved
direct replacement only after confirmation.

### Flags

The red manual-correction flag remains independent while editing. A successfully
saved direct edit normally resolves the reason for the flag, so the UI should
offer to clear it and should default to clearing it for that row. A reviewer may
retain the flag deliberately if another post-finalization problem remains.

### Mixed documents

The task screen must allow, for example:

- row 1: accepted;
- row 2: direct edit saved;
- row 3: Escalated Repair requested; and
- all remaining rows: accepted.

Submission remains document-oriented, but routing and correction data are
stored per unit. The document must not be described as wholly escalated merely
because one row needs Escalated Repair.

## Submission Schema

Introduce an explicit row-resolution representation in the next Bulk Review
submission schema. One possible shape is:

```json
{
  "item_id": "ja_cc_level2:0000000861:u0004",
  "resolution": "direct_edit",
  "base_unit_revision": "...",
  "canonical_yomi_tokens": [
    ["今日", "キョウ"],
    ["は", "ハ"],
    ["晴れ", "ハレ"],
    ["。", "。"]
  ],
  "manual_correction_required": false
}
```

Requirements:

- use `item_id`/`unit_id` as identity, never a row position;
- include a base revision or equivalent digest so stale browser edits cannot
  overwrite newer server state silently;
- serialize only rows whose route or content differs from the implicit
  `accept` default;
- keep current local target-group overrides for `escalate` rows;
- do not duplicate direct-edit rows into Escalated Repair payloads;
- retain reviewer, Issue, submission, pack, batch, document, and stable
  `track_doc_seq` provenance; and
- keep the top-level Issue category as Bulk Review.

The exact field names may change during implementation, but the three routes
must be explicit after migration. Inferring `direct_edit` from incidental token
differences would make replay and auditing fragile.

## Server-Side Application

Bulk Review import should apply one submission in this order:

1. resolve every referenced unit against the authoritative pack and document;
2. reject stale base revisions and unknown units;
3. validate all direct-edit token sequences without mutating canonical data;
4. validate ordinary dispositions and escalation target groups;
5. atomically apply the validated direct edits and review decisions;
6. build Escalated Repair input only from rows routed to `escalate`;
7. derive the document's next state; and
8. record submission and Issue provenance before closing the Issue.

Validation must be shared with finalized correction wherever the invariants are
the same. At minimum:

- token surfaces are non-empty;
- concatenated surfaces exactly equal source text;
- readings satisfy the current script and exceptional-symbol policy;
- source whitespace, variation selectors, and punctuation are preserved;
- canonical serialization is deterministic; and
- a direct edit cannot modify another unit or document.

All direct edits in one imported submission should validate before any are
committed. On failure, preserve the Issue and mark application failure on the
affected document instead of partially advancing it.

## Document State and Finalization

No new durable document state is required. After Bulk Review application:

- directly edited rows count as resolved;
- accepted rows count as resolved;
- skipped or excluded rows follow their existing terminal semantics;
- only `escalate` rows create strong-repair work;
- if at least one row is escalated, the document enters the existing
  Escalated Repair path; and
- if no row is escalated, the document can follow the existing direct
  finalization path.

A document containing direct edits and escalated rows is finalized only after
the escalated rows are confirmed. The direct edits must remain present in the
canonical intermediate data and in the final output; later repair application
must not rebuild the document from a pre-edit artifact.

## Harvesting and Provenance

Direct edits are human-confirmed corpus evidence. After the document reaches
normal finalization, they should participate in the same harvesting mechanisms
as other accepted human corrections:

- exact surface/reading candidates;
- accepted segmentation or rewrite rules where eligible;
- supplemental furigana placement data where needed; and
- decoder corpus/model refresh input.

Harvest from the final canonical unit, not directly from an unfinalized browser
payload. Store enough provenance to distinguish a Bulk Review direct edit from
an accepted Escalated Repair and a later finalized correction.

## Compatibility and Migration

### Existing submissions and finalized data

- Existing Bulk Review submissions retain their current meaning.
- A legacy row with no rejected targets is interpreted as `accept`.
- A legacy row with rejected target groups is interpreted as `escalate`.
- Existing finalized corrections remain post-finalization corrections and are
  not relabeled as direct edits.
- Existing finalized documents are not regenerated for this migration.

### Active packs and browser drafts

- Add schema-version-aware replay before changing emitted submissions.
- Existing active packs may be augmented at publication time if they already
  contain canonical editable tokens; otherwise regenerate only unsubmitted
  packs from stable unit artifacts.
- Never discard an already submitted Bulk Review task.
- Migrate browser drafts by assigning the legacy-derived route above. Preserve
  saved ruby choices, skips, exclusions, flags, task identity, and submission
  overlays.
- Do not automatically convert existing red flags into direct edits. They
  continue through the current workflow unless the reviewer explicitly edits
  the row.

The migration should be backward-readable for historical Issue JSON, but new
steady-state code should emit only the explicit route schema. Compatibility
branches should be localized in submission normalization/replay rather than
spread through rendering and application code.

## Implementation Order

1. Add pure shared validation for canonical unit token edits and tests proving
   parity with finalized correction.
2. Extend Bulk Review draft state with explicit per-row resolution routes and
   direct-edit payloads.
3. Add the row-level editor and browser persistence without yet emitting the
   new submission schema.
4. Add schema normalization and replay for legacy and new submissions.
5. Emit explicit route data in Bulk Review Issues and add importer validation.
6. Apply direct edits atomically before generating Escalated Repair input.
7. Update state derivation so mixed documents escalate only the relevant rows.
8. Ensure strong-repair application overlays its changes on the already edited
   canonical intermediate data.
9. Add harvesting provenance for finalized Bulk Review direct edits.
10. Migrate active browser/pack state, publish dev, and exercise one document
    containing accepted, direct-edited, and escalated rows.
11. Remove obsolete flag-only workarounds only after the direct route has been
    stable in operation; retain flags as an exceptional manual-correction tool.

## Verification

The migration is complete when:

- one Bulk Review document can contain all three row routes;
- a direct edit survives submission, server application, optional Escalated
  Repair of another row, finalization, archive publication, and decoder export;
- direct-edit rows never appear in the Escalated Repair queue;
- invalid or stale edits leave the Issue open and the document recoverable;
- surface concatenation remains byte-for-byte equal to source text;
- refresh/reopen preserves local and server-processing indicators;
- old submissions replay unchanged;
- accepted edits are harvested once with correct provenance; and
- focused Python, browser-state, Issue-import, pipeline, and real-browser DOM
  tests pass.

## Non-Goals

This migration does not add:

- raw source-text editing;
- sentence-boundary editing;
- cross-document bulk replacement;
- a fourth human review queue;
- a new Issue category; or
- automatic conversion of every manual-correction flag into an edit.

Those can be evaluated independently after row-level direct editing has proved
stable.
