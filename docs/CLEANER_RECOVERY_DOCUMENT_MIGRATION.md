# Cleaner-Regeneration Recovery Document Migration

## Problem

The upstream `llm-jp-corpus-v4` cleaner currently treats unrestricted
substrings such as `ホーム` and `タグ` as boilerplate. The direct deletion unit
is a sentence-like segment, but segment isolation and the minimum cleaned-text
length can turn that deletion into removal of a whole source document.

Fixing the cleaner and regenerating its output can therefore add legitimate
segments to documents that have already completed human review. Those segments
must not be inserted silently: they have never passed yomi generation, Bulk
Review, Escalated Repair, or human disposition review. Regeneration also changes
the source fingerprint used by the processing-order store.

## Decision

Process restored segments through temporary **recovery documents**. A recovery
document is a bounded review container, not a canonical corpus document. Its
units follow the ordinary mechanical analysis, reading LLM, Bulk Review, and
Escalated Repair paths. After review, accepted units are scattered back to
their original documents at validated insertion anchors.

Recovery documents may be deleted after the campaign. Their application ledger
and the resulting original-document revisions remain durable.

## Scope

The first campaign is intended for text restored by correcting known cleaner
false positives, especially unrestricted `ホーム` and `タグ` boilerplate
matches. It is not a general textual merge facility.

Classify regenerated records before creating recovery work:

- unchanged processed documents require no action;
- additions to processed documents become recovery units;
- changed or deleted previously finalized text is a conflict and requires a
  separate migration decision;
- newly surviving source documents become ordinary future documents;
- changes to unprocessed documents are handled when migrating the future
  processing-order suffix.

## Regeneration And Diff

Retain immutable snapshots of the old merged source, old cleaned source, and
old filtered source. Regenerate from the same merged records after correcting
the cleaner. Match records by a stable source identifier preserved in metadata,
not by regenerated JSONL line number.

Diff old and new cleaned text as ordered sentence-like segments. A candidate
restoration must be representable as additions between unchanged anchors. For
each addition, store:

- stable source-record ID and original `doc_id`;
- track and `track_doc_seq` when the document is already in the ledger;
- finalized document revision on which the recovery unit is based;
- restored source text and its hash;
- hashes and text excerpts for the preceding and following unchanged anchors;
- old and new segment ordinals for diagnostics;
- cleaner version and recovery campaign ID.

Do not guess when alignment is ambiguous. Duplicate anchors, concurrent source
corrections, replacements, and deletions enter a conflict report instead of a
review pack.

## Recovery Identity

Use a separate namespace such as:

```text
recovery:home_tag_v1:<source-record-id>:<restored-text-hash>
```

The identity must be deterministic. Generating the same campaign twice must
produce the same recovery unit IDs. A durable ledger records at least:

- `campaign_id`;
- `recovery_unit_id`;
- source and destination document identity;
- insertion-anchor identity;
- preparation batch and review artifact IDs;
- Bulk Review and Escalated Repair states;
- Issue/comment provenance;
- application state and destination revision;
- conflict or failure details.

The ledger, rather than queue artifacts, is the authority for whether a unit is
pending, under review, applied, skipped, excluded, conflicted, or archived.

## Virtual Document Construction

Bundle recovery units into bounded virtual documents to reuse the existing
review workflow efficiently. A practical initial bound is 10-30 restored units
or the existing review-pack size limit, whichever is smaller. A virtual
document may contain units from several originals because it has no semantic or
export meaning.

Each row must still display enough original context to review the restored
segment. Keep the previous and following source segments as read-only context
metadata. Context may be sent to an LLM, but it must not become part of the
recovery unit's canonical token stream.

Never create N-gram transitions across adjacent recovery units or across a
virtual-document boundary. The virtual order exists only for review throughput.

## Pipeline Behavior

Recovery units use the ordinary stages wherever possible:

1. run mechanical tokenization, hybrid reading generation, and deterministic
   validation on the restored text;
2. run the normal reading LLM where required, with original neighboring context;
3. present the unit in Bulk Review with ordinary keep, skip, exclude, flag, and
   direct-edit behavior;
4. derive Escalated Repair work from rejected local spans exactly as for normal
   documents;
5. finalize the recovery unit only after all required review is complete;
6. apply the finalized disposition to its original document.

Skip and exclude remain meaningful recovery outcomes. A skipped or excluded
restored segment is recorded in the recovery ledger but is not inserted into
the canonical finalized document.

Recovery containers must be visibly labeled in Issue titles and diagnostics,
but the reviewer-facing editing controls should remain the existing Bulk Review
and Escalated Repair controls.

## Application To Original Documents

Application is atomic per destination document. Before inserting anything:

- confirm that the destination revision matches the recovery unit's base
  revision, or rebase against an unambiguous newer revision;
- confirm exact source preservation for every recovered token stream;
- confirm that both insertion anchors still resolve uniquely and in order;
- confirm that the recovery unit has not already been applied;
- order multiple insertions according to regenerated source order.

Apply all valid units for one destination document in one new correction
revision. Record the previous and new document hashes, inserted unit IDs,
source campaign, and Issue provenance. Regenerate ruby, search, archive-detail,
decoder-corpus, and other derived artifacts from the revised canonical
document.

An application failure must not close the Issue or mark the unit resolved. It
enters an explicit conflict/application-failed state for manual recovery.

## Export And Learning Rules

Recovery documents are never exported as corpus documents. They must not:

- receive ordinary `track_doc_seq` values;
- appear as independent entries in Corpus Map;
- contribute virtual adjacency to decoder or N-gram training;
- be mistaken for newly selected source documents;
- advance the ordinary processing-order cursor.

Only the revised original document contributes to exports and later model
maintenance. Harvested readings and segmentation defaults may be learned after
successful insertion, with provenance pointing to the recovery campaign and
destination revision.

## Processing-Order Migration

Regenerating the filtered source invalidates the current processing-order
source fingerprint. Preserve the frozen prefix as historical provenance; do not
reinterpret old source line numbers against regenerated JSONL.

Before switching refill to the regenerated source:

1. stop refill and require no active processing-order reservation;
2. map old and new records by stable source identity;
3. retain all existing ledger assignments and frozen `track_doc_seq` values;
4. rebuild only the unprocessed permutation suffix from regenerated selectable
   records;
5. carry forward explicit future reordering choices where their source records
   still exist;
6. append newly surviving documents exactly once;
7. validate uniqueness, completeness, frozen-prefix provenance, and the new
   source fingerprint before restarting refill.

Recovery review and future-order migration are related by the same regeneration
but remain separate operations. Recovery patches processed documents; the new
permutation controls documents not yet admitted to the track.

## Lifecycle

A campaign can be retired when every generated recovery unit is applied,
skipped, excluded, or explicitly archived as a conflict. Then:

- remove recovery documents from active review publication;
- retain the compact campaign manifest and application ledger;
- retain Issue provenance and destination correction revisions;
- delete reproducible intermediate packs if storage is unnecessary;
- remove campaign-specific UI labels after confirming no active tasks remain.

The generic recovery document format may remain available for another bounded
cleaner migration, but no permanent parallel review workflow is required.

## Rollout

1. Narrow the upstream boilerplate rules and add cleaner regression tests.
2. Regenerate a small source sample and validate segment-level old/new diffs.
3. Build a report-only recovery generator and quantify restored units,
   destination documents, conflicts, and wholly restored documents.
4. Implement deterministic recovery identities and the durable campaign ledger.
5. Materialize one small recovery pack and run it through Bulk Review and
   Escalated Repair without applying results.
6. Implement atomic destination insertion and derived-artifact regeneration.
7. Apply a small dev campaign and verify Corpus Map, search, exports, and model
   inputs through a real browser DOM check.
8. Run the full dev recovery campaign.
9. Rebuild the unprocessed processing-order suffix against the regenerated
   source and resume refill.
10. Archive the campaign after all terminal states are reconciled.

## Required Validation

- recovery generation is deterministic across repeated runs;
- unchanged source segments never enter recovery review;
- context text cannot leak into canonical recovered token streams;
- duplicate submission or repeated sync cannot insert a unit twice;
- multiple additions to one document preserve regenerated source order;
- concurrent finalized correction produces a conflict or a validated rebase;
- recovery containers never appear in canonical corpus exports;
- revised original documents pass exact surface reconstruction;
- virtual adjacency never enters N-gram or decoder training;
- a regenerated source cannot be used with the old line-number fingerprint;
- campaign retirement removes active tasks without deleting audit provenance.
