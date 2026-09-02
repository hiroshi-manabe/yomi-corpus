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
review workflow efficiently. Size them from observed ordinary dev documents,
not from an arbitrary recovery-only batch size. Across the first 1,770 dev
documents, the baseline is approximately:

- 905 source characters and 24 review units per document on average;
- 605 source characters and 17 review units at the median;
- 1,120 source characters and 32 review units at the 75th percentile;
- 1,981 source characters and 53 review units at the 90th percentile.

The initial deterministic packer therefore targets 900 restored characters per
virtual document. It closes a document before adding the next unit when the
current document has at least 600 characters and the addition would cross 900.
It also closes at 32 units, whichever happens first. A single source unit is
never split merely to satisfy these targets; a unit that is itself longer than
the target occupies one virtual document. The packer must report the resulting
character and unit distributions so a campaign can detect an unexpectedly
different review workload.

Process recovery units in deterministic source order before packing. Given the
same diff and campaign configuration, document boundaries and identities must
be byte-for-byte reproducible. A virtual document may contain units from
several originals because it has no semantic or export meaning.

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
source fingerprint. Stable source identity, not an old filtered-file line
number, is the join key during migration.

Before switching refill to the regenerated source:

1. stop refill and require no active processing-order reservation;
2. map old and new records by stable source identity;
3. retain assignments only for stable identities accepted by the cutover;
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

### Corrected-policy cutover decision (2026-09-02)

The historical production filter invocation was recovered and verified by
reproducing the first 100,000 retained stable IDs exactly:

```bash
python3 scripts/filter_docs_by_score.py \
  --sigma-threshold 0.0 \
  --min-distinct-hits 2 \
  --max-latin-ratio 0.01 \
  --max-jp-inner-space-ratio 0.01 \
  --combine-mode any
```

Apply this same policy to the corrected cleaner output. Do not preserve an old
record merely because it was historically selected. Comparing the old first
1,670 records with the corrected first 1,681 records produced:

- 1,659 shared stable identities, in the same relative order;
- 11 old identities no longer selected; and
- 22 corrected-source identities newly selected.

The corrected first 1,681 records are the canonical prefix. Reuse completed
review data for the 1,659 shared identities, archive the 11 removed identities
with their review and source provenance, and process the 22 incoming identities
normally. The 11 archived records must not remain in canonical exports or be
kept through cleaner fallbacks. This is an explicit one-time prefix migration;
after it, the ordinary frozen-prefix invariant applies again at 1,681.

The prior membership-preserving refresh artifact is useful as migration
evidence but is not the cutover source. It must not be configured as the active
dataset.

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

## Operator Procedure

For the `home_tag_v1` campaign, use versioned side outputs. Do not overwrite the
filtered corpus configured in `config/datasets/ja_cc_level2.toml` until recovery
review and the future-order migration have both been validated.

1. Commit the narrowed cleaner and its regression tests in
   `llm-jp-corpus-v4`.
2. Derive the stable `meta.docId` values for finalized dev documents by joining
   the dev document ledger to the old filtered source by `source_line_no`.
3. Re-clean those UUIDs from the immutable merged source with the cleaner's
   `--doc-id-file` mode. This is sufficient for the processed-prefix recovery
   diff and stops after all requested UUIDs have been found.
4. Build the report-first campaign with
   `scripts/build_cleaner_recovery_campaign.py`. Resolve every row in
   `conflicts.jsonl` before application; do not make the generator guess.
5. Inspect `campaign.json`, especially the recovery-document size distribution.
   Materialize the campaign only after the counts and samples are accepted.
6. Run `./prepare-recovery data/recovery/home_tag_v1`. This creates an explicit
   noncanonical batch without changing the dev track's current batch or the
   processing-order cursor.
7. Advance it explicitly with `./next --batch dev_recovery_home_tag_v1 --auto`
   or let the non-current-batch sweep process it. Recovery finalization writes
   `recovery_application_ledger.jsonl`; it does not archive the virtual
   documents, export them, or harvest them into global learned lexicons.
8. Validate the scatter-back without writes, then apply the same validated
   plan:

   ```bash
   python scripts/apply_recovery_campaign.py data/recovery/home_tag_v1 \
     --batch dev_recovery_home_tag_v1
   python scripts/apply_recovery_campaign.py data/recovery/home_tag_v1 \
     --batch dev_recovery_home_tag_v1 --apply
   ```

   The command resolves anchors against immutable original `units.jsonl`,
   verifies finalized token surfaces, safely splits legacy units only at
   canonical token boundaries, rewrites destination outcome files atomically,
   and records destination revision hashes in the application ledger. A
   repeated application must report `already_applied`.
9. Ask the operator to run the reproducible full build from the upstream
   repository. The build is intentionally long-running and is not embedded in
   review synchronization:

   ```bash
   cd /panfs/panmt22/users/hmanabe/llm-jp-corpus-v4
   python3 scripts/regenerate_ja_cc_level2.py \
     --build-id home-tag-v1-corrected-20260902 \
     --workers 24
   ```

   The command writes only to
   `data/builds/home-tag-v1-corrected-20260902/`. Its `manifest.json` records
   the exact command, effective cleaner and filter parameters, repository
   revision and dirty state, runtime, file fingerprints, per-stage logs, and
   terminal status. Never reuse a build ID or promote a build whose manifest is
   not `complete`.
10. Validate the corrected first-1,681 membership and stable-identity mapping,
    then migrate the canonical prefix and unprocessed processing-order suffix
    before switching refill.

The first report generated on 2026-09-01 considered 1,670 finalized documents.
After four ambiguous anchors were resolved through validated campaign-specific
overrides, it found 343 restored units in 135 destination documents and packed
them into 16 virtual documents averaging 867 characters and 21 units, with no
unresolved conflicts. These figures are campaign diagnostics, not hard-coded
expectations.

### Post-Recovery Source Cutover

Documents first materialized after the recovery cutoff must not continue from
the old filtered source. If that happens, retire the entire materialized suffix
rather than creating a second overlapping recovery campaign:

1. stop issue watching, review sync, refill, and decoder refresh;
2. retain closed Issues as provenance but move their imported submissions out
   of the active submission stores;
3. retire every batch and archive shard whose `track_doc_seq` is at or after the
   cutoff, and truncate the document ledger to the frozen prefix;
4. rebuild global learned readings, exact rewrites, and supplemental furigana
   exclusively from retained finalized batches;
5. regenerate the complete clean/score/filter source in versioned side paths;
6. run `manage_processing_order.py migrate-suffix` to preserve stable-identity
   ordering choices while replacing only the unprocessed suffix;
7. point the dataset configuration at the validated source and create fresh
   batches with new pack identities; and
8. republish and verify the browser DOM before restarting automation.

For the 2026-09-02 dev cutover, the old slots 1 through 1,670 are migration
inputs, not an immutable final prefix. Rebuild them as the corrected canonical
slots 1 through 1,681 using stable identity: carry review data for 1,659 shared
documents, archive 11 removed documents, and review 22 incoming documents.
Materialized old slots 1,671 through 1,780 remain retired in a timestamped local
backup. Their old pack IDs remain obsolete permanently; replacement work uses
new monotonic batch names so GitHub acknowledgments cannot be replayed against
different documents.

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
- the full-build manifest records the exact historical filter policy and ends
  in `complete` before promotion;
- the corrected prefix contains exactly 1,681 unique stable identities: 1,659
  carried forward and 22 newly reviewed, with 11 old identities archived;
- campaign retirement removes active tasks without deleting audit provenance.
