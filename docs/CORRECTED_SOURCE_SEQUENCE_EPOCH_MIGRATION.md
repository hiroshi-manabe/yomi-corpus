# Corrected Source Sequence-Epoch Migration

## Objective

Replace the dev track's historical cleaner output with the corrected filtered
source as though that source had been used from the beginning. The resulting
canonical track has contiguous reviewer-facing document numbers, no permanent
holes, and no membership-preserving cleaner exceptions.

The expected prefix transition, subject to final verification against the
completed full-build manifest, is:

- old canonical input: 1,670 documents;
- corrected canonical prefix: 1,681 documents;
- stable source identities carried forward: 1,659;
- old source identities archived: 11; and
- corrected-source identities entering ordinary review: 22.

The 1,659 carried documents take their corrected-source positions. The 22 new
documents occupy their natural positions among them and pass through the normal
Bulk Review and Escalated Repair workflow. Refill resumes at document 1,682.

Final staging found one shared identity whose corrected text contains restored
boilerplate-like segments not reviewed by the earlier recovery campaign. Treat
that identity as a full ordinary reprocessing case rather than weakening text
validation or extending the special recovery mechanism. The operational action
counts are therefore 1,658 carried, one reprocessed, 22 incoming, and 11
archived; 23 documents enter ordinary review.

This is a one-time dev migration. It is an explicit exception to the normal
rule that `track_doc_seq` and `doc_id` never change after assignment.

## Why An Epoch Is Required

The upstream record's `meta.docId` is the stable identity used to join the old
and corrected sources. The current corpus `doc_id`, however, is generated from
the dataset name and physical source line, for example
`ja_cc_level2:0000000123`. Unit and region identities inherit that prefix.

Renumbering therefore changes more than a display number. It can make a new
document reuse text identifiers formerly present in Issues, submissions,
review packs, browser storage, and archives. Treat the cutover as a new
**source sequence epoch**:

- seal the old epoch as read-only audit material;
- assign a new epoch ID to every canonical ledger row and generated artifact;
- reject submission replay when its epoch does not match the active epoch; and
- never import an untagged legacy submission after cutover.

The ordinary UI does not need to show the epoch. It exists to prevent stale
artifacts from modifying the rewritten canonical track.

## Preconditions

Do not apply the migration until all of the following hold:

1. The upstream `home-tag-v1-corrected-20260902` build has stopped running.
2. Its `manifest.json` has `status: complete`, three successful stages, the
   expected `historical-production-v1` policy, and valid artifact checksums.
3. A fresh stable-identity comparison confirms the expected 1,659 shared, 11
   removed, and 22 incoming records. The migration must stop on any mismatch;
   these counts are validation expectations, not hard-coded selection rules.
4. Issue watching, review sync, refill, decoder refresh, and publication are
   stopped.
5. Processing order has no active reservation and no review submission is
   being imported.
6. The `home_tag_v1` recovery campaign remains fully applied: 338 inserted
   units, five skipped units, and no unresolved application conflict.
7. Materialized old-source work after document 1,670 is retired and cannot be
   replayed into the new epoch.

Tooling may be implemented and tested against copies while the upstream build
runs. Canonical files and configuration must remain unchanged until all
preconditions pass.

## Migration Plan Artifact

The first migration command is report-only. It reads the old source, corrected
source, document ledger, finalized archives, batch state, and recovery
application ledger and writes an immutable plan directory containing:

- old and corrected source fingerprints and build-manifest fingerprint;
- the old and new sequence epoch IDs;
- one row per corrected prefix position with `meta.docId`, old source line,
  old corpus `doc_id`, old `track_doc_seq`, new source line, new corpus
  `doc_id`, new `track_doc_seq`, and disposition;
- explicit `carried`, `removed`, and `incoming` identity lists;
- every canonical file expected to be rewritten or regenerated;
- every legacy artifact expected to be sealed or quarantined;
- hashes of all migration inputs; and
- expected output counts and hashes where they can be determined in advance.

The plan is deterministic. Repeated report-only runs over the same inputs must
produce the same semantic mapping. Review the complete 11-row removal list and
22-row incoming list before application.

## Canonical Rewrite

The apply command builds a complete staging tree and validates it before one
atomic installation step. It must not edit the live tree incrementally.

### Carried documents

For each of the 1,659 shared `meta.docId` values:

- copy the latest canonical reviewed state, including applied cleaner-recovery
  additions and later finalized corrections;
- assign the corrected source position as `track_doc_seq`;
- regenerate source-line-derived `doc_id`, `unit_id`, region IDs, and internal
  references with schema-aware transformations;
- retain original Issue and correction provenance as legacy-epoch references;
- verify that token surfaces reconstruct the corrected source text exactly;
  and
- stop rather than guess if anchors, source identity, or reconstructed text do
  not agree.

Do not perform an unrestricted string replacement over JSON. Every rewritten
schema needs an explicit transformer and a focused test.

### Removed documents

Move the 11 removed identities and their old canonical payloads into the sealed
legacy-epoch archive. They remain available for audit but disappear from active
ledger state, Corpus Map, search, exports, N-gram input, decoder input, and
learned-reading harvests.

### Incoming documents

Create ordinary document-ledger rows for the 22 incoming identities at their
corrected positions. Prepare them in one or more migration-specific batches
using fresh pack IDs and the normal current pipeline. They are not cleaner
recovery documents and receive no inherited readings or review decisions from
the displaced old documents that previously occupied those numbers.

The migration is not complete merely because these documents are queued. The
new epoch may become active with the 22 documents in Bulk Review, but decoder
and finalized exports must include only documents that have reached their
ordinary eligible state.

## State And Artifact Handling

The migration implementation must inventory and handle at least:

- `data/pipeline/document_ledger/dev.json`;
- per-document review state and batch state;
- canonical unit, yomi, repair, and finalized files;
- finalized correction histories and application counters;
- recovery-applied destination revisions;
- processing-order binary, manifest, and event log;
- generated archive, Corpus Map, search, and review-site artifacts;
- learned readings, exact rewrites, supplemental furigana, N-gram corpora, and
  decoder inputs; and
- browser-visible task identifiers and server acknowledgment state.

Derived artifacts should be rebuilt from rewritten canonical data rather than
patched. Historical review packs, imported submissions, and closed Issues stay
in the sealed legacy epoch and are never rewritten into apparently current
submissions.

Add `source_sequence_epoch` to canonical state and new submission payloads.
Importers must reject an explicit mismatched epoch and treat missing epoch data
as legacy-only after cutover. Clear or namespace browser task storage so stale
local tasks cannot be reopened against reused numeric document identifiers.

## Processing Order

Build a fresh processing-order store against the corrected filtered artifact:

- slots 1 through 1,681 are the corrected source's first 1,681 selectable
  records;
- ledger rows for all 1,681 slots exist, including the 22 active review rows;
- the cursor is 1,682;
- each selectable source record occurs exactly once in the complete order;
- future explicit reordering choices are carried forward by `meta.docId` when
  still applicable; and
- the source fingerprint and build-manifest fingerprint are recorded.

Do not use the current `migrate-suffix` implementation for this operation. It
is intentionally prefix-preserving and cannot represent a sequence-epoch
rewrite.

## Retiring Cleaner Recovery

The `home_tag_v1` recovery campaign has already scattered accepted restored
segments into their destination documents. The sequence migration carries
those latest destination revisions into the corrected epoch.

After cutover validation:

- remove the recovery batch and recovery documents from active publication and
  scheduler discovery;
- retain the campaign manifest, recovery units, finalization summary,
  application ledger, and Issue provenance in the sealed legacy archive;
- remove campaign-specific operational configuration and temporary source
  pointers; and
- retain generic recovery code only as dormant migration tooling, not as a
  normal stage for the corrected source.

This retires the special per-sentence repair used for text formerly dropped by
the cleaner. The corrected full source becomes authoritative instead.

## Application, Validation, And Rollback

The eventual tool should expose separate `plan`, `validate`, and `apply`
operations. `apply` requires the reviewed plan hash and must be idempotent.

Before installation, create a timestamped snapshot containing all canonical
inputs, configuration, processing-order state, and generated publication state.
Record its location and hashes in the migration manifest. Installation uses a
single migration lock and atomic directory/file replacements where possible.

Required post-apply checks include:

- active epoch and dataset configuration reference the completed corrected
  build;
- canonical numbering is exactly contiguous from 1 through 1,681;
- 1,681 ledger rows map one-to-one to corrected stable source identities;
- carried, removed, and incoming counts match the reviewed plan;
- no old-epoch submission is importable;
- every carried finalized document reconstructs corrected source text;
- removed documents are absent from all canonical exports;
- incoming documents appear only in their valid ordinary review queue;
- Corpus Map, search, archive, and review queues pass a real browser DOM check;
- rebuilt learned and decoder inputs contain no legacy-only document; and
- refill dry-run selects document 1,682 from the corrected processing order.

If validation fails before installation, discard the staging tree. If it fails
after installation, stop all workers and restore the recorded snapshot as one
unit. Do not attempt a partial reverse renumbering.

## Implementation Order

1. Add epoch fields and replay guards without changing the active epoch.
2. Implement deterministic report-only identity mapping and artifact inventory.
3. Implement schema-aware carried-document rewriting and tests on a synthetic
   miniature migration containing shared, removed, and incoming documents.
4. Implement legacy sealing, derived-artifact rebuild, processing-order rebuild,
   and rollback snapshot handling.
5. Run the complete plan and validation against copied dev state.
6. Review the 11 removals, 22 additions, and every reported conflict.
7. Stop workers, create the final snapshot, and apply the reviewed plan.
8. Publish and perform local browser DOM verification before restarting issue
   watching, review sync, and refill.
9. Review the 22 incoming documents normally and retire the active
   `home_tag_v1` recovery campaign.

## Execution Record

The migration was applied on 2026-09-02 as source sequence epoch
`home_tag_v1_corrected_20260902`. Pre-install validation confirmed 1,681 unique,
contiguous ledger documents, 1,658 carried finalized documents, and 23 ordinary
review documents across batches `dev_batch_1018` through `dev_batch_1020`.

The previous active epoch is sealed locally under
`data/migrations/source_epoch/home_tag_v1_corrected_20260902/legacy_epoch`.
The corrected source has SHA-256
`95c3b3d248f87932c700f0e56917ee5f6aacfe8f3902633a1131d8be7429bd77`,
contains 2,593,288 selectable documents, and resumes refill at slot 1,682.
All three migration batches reached Bulk Review through the ordinary mechanical
and LLM stages; one stale background response was superseded and retried without
discarding the other completed responses.
