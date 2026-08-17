# Scope-Triage and Alphabetic-Scope Removal Plan

Status: implemented and migrated on the dev track on 2026-08-17.

The rollout preserved active batch/document identity, removed retired policy
and artifact references from active metadata, and regenerated active Bulk
Review packs without machine-derived scope defaults. The migration is
idempotent and independently detects interrupted or legacy review-pack output,
not only stale batch metadata. Batches prepared under the shortened path create
no classifier jobs and enter Bulk Review at implicit `Keep`.

## Decision

Remove LLM-based scope triage and the separate alphabetic/foreign-expression
scope classifier from the active pipeline. New units enter reading processing
with an implicit `Keep` disposition. Human reviewers remain able to select
`Skip` or terminal `Exclude` in Bulk Review.

This decision is based on operation of the dev workflow:

- machine Skip/Exclude judgments have not been accurate enough to trust;
- true Skip/Exclude cases are uncommon;
- the low incidence does not justify more prompt tuning or a stronger model;
- correcting false positives costs human time and creates exceptional state;
- alphabetic material already needs ordinary reading generation and review;
- the classifiers add stages, prompts, ledgers, retries, migrations, and UI
  behavior without removing a meaningful amount of downstream work.

The `Skip` and `Exclude` concepts are not being removed. Only machine scope
classification and provisional machine dispositions are being removed.

## Target Pipeline

The preparation path should become:

1. prepare stable source units;
2. generate deterministic/Sudachi/hybrid yomi for every unit;
3. apply deterministic safety and corpus-evidence rules;
4. ask the reading LLM only about unresolved reading targets;
5. present every unit in Bulk Review with implicit `Keep`;
6. accept human `Skip` or `Exclude` choices as explicit review decisions;
7. continue through Escalated Repair and finalization as today.

Alphabetic strings, foreign names, abbreviations, units, and mixed-script
tokens follow the same reading path as other text. Existing deterministic
reading rules may remain where they improve readings, but they must not decide
corpus scope. An uncertain alphabetic reading becomes an ordinary unresolved
reading target rather than a reason to skip its unit.

The active stage sequence should therefore omit:

- `alphabetic_analyzed`
- `alphabetic_reported`
- `alphabetic_llm_judged`
- `alphabetic_promotion_candidates`
- `scope_triage_queued`
- `scope_triage_llm_completed`

`prepared` should advance directly into the existing yomi-generation path.

## Removal Inventory

Implementation should remove active dependencies in these groups.

### Pipeline and configuration

- alphabetic and scope-triage stage constants, handlers, artifact detection,
  rerun declarations, status reporting, and CLI stage names;
- `alphabetic_entity_judge` and `scope_triage` LLM policy tasks;
- their model profiles, execution-mode settings, background/batch jobs,
  retry handling, usage summaries, and cost reporting;
- refill-worker assumptions that a new batch must traverse these stages;
- active prompt and task configuration for both classifiers.

### Alphabetic scope subsystem

- batch entity inventories and unresolved-entity reports used only for scope;
- whitelist, blacklist, obscure-entity judgment, promotion-candidate, and
  cross-batch scope-cache logic;
- projection of entity judgments back to units;
- human-unskip feedback into alphabetic scope decisions;
- obsolete alphabetic review/import code and tests.

Alphabetic tokenization or deterministic reading utilities may remain only if
the normal reading pipeline still uses them. They should be moved under yomi
reading terminology rather than retaining a scope-classification subsystem.

### Scope data and review behavior

- production of `scope_triage_input.jsonl`, `scope_triage_results.jsonl`, and
  `units.scope_triaged.jsonl` for new batches;
- machine-derived `scope_default`, `provisional_skip`, skip reasons, and
  machine-selected `Exclude` defaults;
- UI styling and explanations that distinguish provisional alphabetic or
  scope-triage skips from ordinary review decisions;
- tests whose only purpose is preserving machine scope classifications.

The Bulk Review `Skip` and `Exclude` controls, finalized tombstones, hybrid
yomi retained for human-confirmed skips, and Corpus Map correction behavior
remain supported.

## Migration Policy

Migration must distinguish machine state from human decisions.

### Finalized and human-reviewed data

- Do not rewrite finalized documents merely because an old machine classifier
  contributed to their history.
- Preserve human-confirmed `Skip` and `Exclude` decisions.
- Preserve submission provenance and old stage artifacts as historical data;
  the new runtime simply stops reading them.
- Correct questionable historical outcomes through the existing Corpus Map
  correction workflow rather than a broad automatic resurrection.

### Active batches

- Batches before yomi generation should be returned to `prepared` and advanced
  through the shortened stage sequence.
- Active, not-yet-reviewed batches whose generated review material contains
  machine-derived Skip/Exclude defaults should be regenerated from their
  stable source units with implicit `Keep`.
- Batches with submitted human review must not discard that submission. Apply
  the human disposition, then continue under the new pipeline.
- Running or persisted classifier jobs should no longer block progress. Record
  them in a migration report and stop referencing them; remote cancellation is
  optional because correctness does not depend on it.
- Stable document IDs, unit IDs, `track_doc_seq`, source text, and exact-surface
  invariants must survive regeneration.

Add a one-time migration command or explicit pipeline migration function. It
should be idempotent, produce a machine-readable report, and support dry-run
inspection before changing active batch state.

## Implementation Order

1. Add tests for the target stage sequence and implicit-`Keep` behavior.
2. Add the idempotent active-batch migration and inspect its dry-run output.
3. Change new-batch and refill progression to bypass all six obsolete stages.
4. Make yomi generation consume stable prepared units directly.
5. Remove machine disposition projection while preserving explicit human
   `Skip`/`Exclude` application and finalization semantics.
6. Migrate active dev batches and verify queue/document identity.
7. Remove alphabetic-scope and scope-triage LLM tasks, prompts, configs,
   ledgers, stage handlers, artifact plumbing, and dedicated retry code.
8. Remove obsolete UI explanations and tests.
9. Update the main pipeline documentation and README to describe only the
   shortened workflow; Git history remains the record of the retired design.
10. Run one fresh dev refill batch end to end before enabling the timer again.

Code deletion should follow compatibility migration, not precede it. This
keeps old artifacts readable long enough to migrate active batches without
carrying permanent legacy branches in the steady-state implementation.

## Verification

The removal is complete when all of the following hold:

- a fresh batch creates no alphabetic-scope or scope-triage artifacts;
- no classifier LLM requests are submitted;
- every source unit reaches yomi generation regardless of disposition history;
- alphabetic and mixed-script examples receive normal reading candidates;
- Bulk Review initially shows `Keep` for every new unit;
- human Skip/Exclude submissions still apply, persist, and finalize correctly;
- interrupted refill and review-sync runs resume without references to removed
  stages or jobs;
- active migrated batches preserve document/unit identity and exact source
  text;
- finalized historical batches remain readable in Corpus Map;
- pipeline, review-sync, refill, publication, and focused browser-DOM tests
  pass.

## Rollback

Keep the migration report and pre-migration batch metadata until at least one
fresh dev batch has finalized. Rollback means restoring the prior code from Git
and restoring only active-batch metadata from that snapshot. It must not alter
human submissions or finalized corpus data created after migration.
