# Yomi Corpus Review UI Workflow Notes

Human-facing review terminology should avoid exposing internal pipeline-stage
names:

- `Bulk Review` is the reviewer-facing name for internal
  `yomi_final_review`. It is the high-throughput review pass, not necessarily
  the final human action in the whole workflow.
- `Escalated Repair` is the reviewer-facing name for internal
  `yomi_strong_repair_review`. It handles spans that need a stronger model or
  manual boundary/reading correction.

Technical file names, pack IDs, and stage IDs may keep the internal names, but
the browser UI should prefer the reviewer-facing names above.

## 1. Use a movement model, not a static list model

Documents should move through workflow states.

A document can go directly from Bulk Review to resolved:

```text
Bulk Review Queue
    |
    v
Resolved
```

Or, if stronger correction is needed, it can move through Escalated Repair:

```text
Bulk Review Queue
    |
    v
Escalated Repair Queue
    |
    v
Resolved
```

The top-level workflow states should probably be:

```text
Pending Bulk Review
Pending Escalated Repair
Resolved
```

Internally, the pipeline should keep a more precise canonical document state.
This state is per document, not per browser task and not per generated review
pack:

| State | Reviewer bucket | Meaning |
| --- | --- | --- |
| `final_pending` | Bulk Review | Bulk Review is available and no accepted submission has been applied yet. |
| `final_in_review` | Bulk Review | Some Bulk Review units for this document have been applied, but the document is not complete. |
| `final_reviewed` | Bulk Review, submitted/processing overlay | Bulk Review has been applied; backend processing has not yet decided whether the document is complete or needs Escalated Repair. |
| `strong_pending` | Bulk Review submitted overlay, or Escalated Repair when proposals exist | The document needs Escalated Repair, but it is actionable only after repair proposals/results exist. |
| `strong_in_review` | Escalated Repair | Some Escalated Repair items have been reviewed, but the document is not complete. |
| `strong_reviewed` | Escalated Repair submitted/processing overlay | Escalated Repair review has been applied; backend finalization has not yet marked the document `complete` or `skipped`. |
| `complete` | Resolved | The document is accepted for corpus output. |
| `skipped` | Resolved | The document is intentionally excluded from corpus output. |

The reviewer-facing buckets are a projection of this state:

- Bulk Review contains actionable `final_pending` and `final_in_review`
  documents.
- Bulk Review may show `final_reviewed` or non-actionable `strong_pending` as a
  submitted/processing overlay only; those documents should not be selectable
  until backend processing moves them.
- Escalated Repair contains actionable `strong_pending` and `strong_in_review`
  documents only when at least one repair proposal/result is present.
- Resolved contains `complete`, `skipped`, and any state that the backend has
  explicitly finalized as no longer actionable.

This projection matters for rolling refill. Refill should count only
actionable Bulk Review documents, not submitted/processing documents and not
documents merely present in old review packs.

"Bulk Review completed, no Escalated Repair needed" and "Escalated Repair
completed" should both be merged into **Resolved**, because operationally they
both mean:

```text
This document has no remaining work in this pack.
```

However, the path can still be kept as metadata:

```text
1 - Resolved via Bulk Review
4 - Resolved after Escalated Repair
```

## 2. Active queues should contain only actionable documents

If a document has finished Bulk Review, it should leave the Bulk Review Queue.

Example initial state:

```text
Bulk Review Queue:
1 2 3 4 5 6 7 8 9 10

Escalated Repair Queue:
empty

Resolved:
empty
```

After reviewing documents 1-5, with 4 and 5 needing Escalated Repair:

```text
Bulk Review Queue:
6 7 8 9 10

Escalated Repair Queue:
4 5

Resolved:
1 2 3
```

This keeps the meaning of "queue" clean:

```text
A queue shows things that can currently be worked on there.
```

## 3. Resolved should be unified and sorted by document number

Resolved should not be grouped primarily by route. It should probably be shown
in original document order:

```text
Resolved:
1 2 3
```

or:

```text
1 - Bulk Review only
2 - Bulk Review only
3 - Bulk Review only
```

The reason is that Resolved is mainly for reassurance and traceability:

```text
These documents are done; they did not disappear.
```

Sorting by document number lets the user understand pack progress naturally.

## 4. Add a Pack Map as a visual overview

Because the movement model becomes hard to read as vertical lists, a **Pack
Map** is useful.

Example:

```text
Pack Map

[1 OK] [2 OK] [3 OK] [4 ER] [5 ER] [6 BR] [7 BR] [8 BR] [9 BR] [10 BR]
```

Where:

```text
OK = resolved
ER = Escalated Repair pending
BR = Bulk Review pending
```

The Pack Map answers:

```text
Where is each document in the whole pack?
```

It should show all documents, regardless of current state.

## 5. Keep queues as the primary action surface

The Pack Map should mostly be for overview and light navigation. The actual
work selection should happen in the queues.

Recommended role split:

```text
Pack Map
= status overview / document navigation

Queues
= select work range / start task
```

Clicking a tile in the Pack Map may open a read-only document preview. It
should not replace the queue controls as the main way to start work.

## 6. Avoid rectangular selection in 2D grids

A 2D tile layout is good for seeing state, but bad for selecting ordered
ranges.

For example, "6 through 10" is a linear range. Once tiles wrap into rows,
rectangular selection can accidentally select strange ranges.

Avoid this:

```text
drag rectangle across tiles
```

Prefer this:

```text
From [6] to [10]
[Start Bulk Review]
```

## 7. Range selection can be combo-box based

Combo boxes are not a compromise here. They may actually be the cleanest
solution.

Example:

```text
Bulk Review Queue
[6] [7] [8] [9] [10]

From [6] to [10]
Selected: 5 documents
[Start Bulk Review]
```

The dropdowns should list only documents currently available in that queue.

For Escalated Repair:

```text
Escalated Repair Queue
[4] [5]

From [4] to [5]
Selected: 2 documents
[Start Escalated Repair]
```

Quick actions such as `[Take next 5]`, `[Select all]`, and `[Clear]` are useful
for normal operation.

## 8. Queue tiles can also be 2D, but only visually

It is fine for queues to show document tiles in a wrapped 2D layout:

```text
Bulk Review Queue

[11] [12] [13] [14] [15] [16] [17] [18]
[19] [20] [21] [22] [23] [24]
```

But the wrapping should be purely visual. Semantically, the queue is still
linear and ordered by document number.

So:

```text
Tiles = preview of available documents
Combo boxes = actual range selection
Button = start task
```

## 9. Local task states

The browser has local task states in addition to pipeline document states:

- active task: currently open in the browser
- deferred task: saved locally and resumable later
- submitted task: JSON was copied and the user reports that a GitHub Issue was
  created

Because GitHub Pages cannot reliably create Issues with a large body directly,
the UI should copy JSON and open a pre-titled GitHub Issue page. When the page
regains focus, a modal asks whether the Issue was created. If the user confirms
submission, the task moves to submitted local tasks.

Local tasks are resumable work, not history. A task is valid only while each
target document remains in that task's stage. If a Bulk Review task's document
leaves Bulk Review, or an Escalated Repair task's document leaves Escalated
Repair, that document should be removed from the local task. If no target
documents remain, the task should be deleted. This applies equally to deferred
and submitted local tasks.

Submitted local tasks are a temporary local overlay: the user has probably
submitted the work, but the pipeline has not yet imported and applied the
Issue. While the target document is still in the same stage, it should be
greyed out in the Pack Map and disabled in queues. It should be editable again
only through an explicit reopen action. Once the server-side state moves the
document to another stage or resolved state, the local task is no longer
resumeable and should disappear.

The server-side importer remains authoritative. Once the Issue is imported and
applied, the generated review pack should move those documents into the next
pipeline state or resolved state.

## Overall proposed UI structure

A possible structure:

```text
Unified Yomi Review

Pack Map
[1 OK] [2 OK] [3 OK] [4 ER] [5 ER] [6 BR] [7 BR] [8 BR] [9 BR] [10 BR]

Work Queues

Bulk Review
Available: 5 documents
[6] [7] [8] [9] [10]
From [6] to [10]
[Start Bulk Review]

Escalated Repair
Available: 2 documents
[4] [5]
From [4] to [5]
[Start Escalated Repair]

Deferred local tasks
[Resume task 1]

Submitted local tasks
[Reopen submitted task 2, if its documents are still in that task's stage]

Resolved
3 documents
[1] [2] [3]
```

## Core design principle

```text
Pack Map shows the whole pack. Queues show what can be worked on now.
Resolved shows what is already done. Local submitted tasks are not done until
the importer applies them, but they are valid only while their documents remain
in the submitted task's stage. Range selection stays linear, even if the display
is tile-based.
```

## Long-Term Workspace Direction

The current UI is pack-oriented because that is the easiest way to stabilize
schemas. The long-term UI should be document-oriented:

- backend batches are processing chunks, not reviewer-facing boundaries
- the workspace can mix documents prepared by different backend batches
- a background process can prepare later documents while the reviewer works on
  the current slice
- the map can eventually cover thousands of documents, with processed,
  submitted, active, and unprocessed documents shown together
- static index data should stay small; full review payloads should be loaded
  only when needed
- local task state is only an overlay and expires when the document leaves that
  task's stage

When documents can come from multiple backend batches, document numbers shown
to reviewers must be stable across refill. The UI should display
`track_doc_seq`, a per-track monotonic number assigned when a source document
first enters the track ledger. Batch-local `doc_seq` is still useful for
artifact debugging, but it should not be the primary reviewer-facing number.
Issue payloads should include both `track_doc_seq` and the authoritative
`doc_id`.

For rolling refill, the durable per-track ledger should become the canonical
document pool. Backend batches remain processing chunks, but queue membership is
derived from document rows in the ledger/state layer:

- `unprocessed`: source document has not entered any prepared batch
- `prepared`: deterministic/LLM preprocessing has started or completed, but no
  Bulk Review item is actionable yet
- `bulk-ready`: the document is in actionable Bulk Review
- `bulk-submitted`: Bulk Review was submitted and backend processing is
  deciding whether the document is complete or needs Escalated Repair
- `escalated-ready`: the document is in actionable Escalated Repair
- `escalated-submitted`: Escalated Repair was submitted and backend processing
  is applying/validating it
- `resolved`: the document is `complete` or `skipped`

These ledger-level labels are derived/status summaries, not a second source of
truth. The concrete `final_*`, `strong_*`, `complete`, and `skipped` document
states remain the authoritative stored values. The refill selector should use
the derived labels only for counting and choosing the next documents to
prepare.

Resolved documents should not be immutable forever. If a reviewer later finds a
problem, the UI should create a correction task rather than editing history in
place. The importer then applies the correction and the pipeline can harvest
new rewrite defaults or ruby dictionary entries from the accepted correction.

## Outstanding Manual Corrections

Some defects cannot be represented safely by the normal candidate controls or
repaired reliably by Escalated Repair, especially incorrect segmentation. Both
Bulk Review and Escalated Repair should therefore let the reviewer mark a unit
as `manual_correction_required`.

This is a durable unit-level state, not a disposition like `Skip` and not a
stage-local browser hint:

- the Bulk Review and Escalated Repair interfaces expose the same red flag
  action
- setting the flag does not disable reading edits, skipping, or escalation
- a flag set in Bulk Review remains set when the document enters Escalated
  Repair and when it later enters the resolved archive
- normal repair proposals and stage transitions must not clear it implicitly
- a reviewer may explicitly remove the flag in either review stage
- records from multiple stages for the same unit are merged rather than
  creating duplicate outstanding correction tasks
- each record retains its source stage, optional target span and short reason,
  submission identifier, and timestamp for auditability

The control should resemble the existing compact `Skip` control without being
confusable with it. Use a red outlined flag icon when unset and a filled red
flag when set, with the accessible label and tooltip `Requires later manual
correction`. A reason field may appear only after the flag is enabled. Unlike
`Skip`, the flag is additive: the sentence remains in the ordinary review flow.

Corpus Map shows a red badge containing the number of distinct unresolved
flagged units in each document. Its entry button also shows the aggregate
unresolved count so outstanding work remains visible before Corpus Map is
opened. These badges are separate from the existing blue badge, which counts
applied correction submissions: red means work remains; blue records
correction history. Opening a flagged document directly from Corpus Map scrolls
to its first flagged unit. Opening it from search instead preserves the more
specific behavior of scrolling to the first text match.

Saving or submitting a browser draft does not resolve a flag. The server clears
the corresponding unit flag only after a correction has been validated and
applied to finalized data. Application failures leave the flag in place and
surface the failure reason. When all flagged units in a document have been
resolved, its red Corpus Map badge disappears automatically.

A flagged unit may already be correct by the time it is revisited, for example
because regeneration removed the defect that originally caused the flag. In
that case Corpus Map permits a no-edit submission after an explicit warning.
The payload contains an acknowledgement patch for each still-flagged unit, with
identical original and proposed yomi, rather than an empty patch list. The
server validates the current text and yomi before recording the acknowledgement
and clearing the flag. A no-edit submission never acknowledges unflagged units.

## Resolved Document Corrections

Resolved-document edits should be treated as correction requests, not as local
mutations of finished data. The browser may show a resolved document as ruby
text, but that display represents server-applied state. If a reviewer chooses
to edit it, the UI should switch to a raw yomi editor such as:

```text
今日/キョウ は/ハ いい/イイ 天気/テンキ です/デス 。/。
```

The ruby preview should not change immediately after the reviewer edits this
raw yomi string. It should change only after the correction payload has been
submitted, imported, validated, and applied by the server-side pipeline. This
keeps the Pack Map and resolved previews honest: they show what the repository
currently accepts, not a local draft that might fail validation or never be
submitted.

The resolved correction path should validate in two places:

- the browser should perform fast validation before allowing submission, mainly
  to catch obvious formatting mistakes and prevent frustrating issue payloads
- the importer/server pipeline remains authoritative and must repeat the same
  validation before applying any correction

Browser-side resolved correction editing is save-based rather than live-submit:

- opening a unit editor creates a temporary draft only for that row
- `Save` validates the rendered-yomi text and stores it as a pending correction
- `Save` also normalizes hiragana in the reading side to katakana before
  validation and export
- `Cancel` closes the editor; if the open draft differs from the last saved
  pending correction, the browser asks before discarding it
- the Issue JSON buttons stay disabled while any editor is open, so only saved
  pending corrections can be submitted
- saving text equal to the original finalized yomi removes that row from the
  pending correction set
- saved pending corrections persist in browser storage per track and document;
  reopening the document or reloading the page restores them
- opening an Issue does not immediately erase the draft; after returning, the
  reviewer can mark it submitted locally, and Corpus Map distinguishes local
  drafts from submitted corrections waiting for server import
- once a refreshed archive contains the exact proposed yomi, the corresponding
  local record is removed and the server-applied correction badge becomes the
  authoritative history

Initial validation rules should be conservative:

- each token is represented as `surface/reading`
- concatenating all surfaces must reproduce the original text, modulo the
  project's explicit whitespace and bracket-escape conventions
- no correction may introduce, delete, or reorder original source characters
- readings for kanji or Latin-containing surfaces must be kana-only, except for
  explicitly allowed empty readings
- numeric-only surfaces follow the project numeric policy
- punctuation and kana-only surfaces may keep identity readings or empty
  readings according to the existing yomi representation rules

This path is intentionally less convenient than Bulk Review or Escalated Repair.
It is an exceptional post-resolution correction mechanism, so auditability and
server-authoritative state are more important than immediate rich editing.

Finalized correction Issues use `submission_type: finalized_correction_patch`
and `review_stage: finalized_correction`. They are not tied to a review pack:
the server applies them by `track_name`, `batch_name`, and `unit_id` against the
current finalized JSONL. A correction is accepted only if the referenced unit
still has the same source text and the proposed rendered-yomi string preserves
the unit text under the same validation rules as the browser. Re-running sync is
idempotent: a stored correction whose proposed yomi is already present is treated
as accepted, not as a mismatch.

`review-sync` imports open finalized-correction Issues, stores the payloads under
the finalized-correction submission store, applies accepted patches to
`units.yomi.final.jsonl`, republishes the review archive when anything changes,
and closes only Issues whose imported correction submissions were accepted
without skipped patches.

Applied finalized corrections are exported into archive shards with two
document-level measures: `finalized_correction_count` counts distinct correction
submissions, while `finalized_correction_sentence_count` counts distinct units
that have ever been corrected. Thus, two submissions that both edit the same
sentence are displayed as `2 corrections · 1 sentence changed`. Unit-level
`finalized_correction_count` continues to count the distinct correction
submissions that affected that unit. The Corpus Map tile badge shows correction
submissions; list metadata, tooltips, and previews also show the affected-sentence
count.

Corpus Map search uses a separately published, text-only index. Each record
contains only the track document sequence, document ID, archive shard path, and
the document's concatenated raw sentence text. The browser does not fetch this
index while merely browsing Corpus Map; it loads it after the reviewer enters a
search query. Initial search is a debounced client-side substring scan with at
most 100 rendered results. Opening a result then fetches only its full archive
shard and opens the normal read-only document preview. This is intentionally
simple for thousands of documents while preserving a boundary where the index
can later be partitioned or replaced without changing the review UI.
