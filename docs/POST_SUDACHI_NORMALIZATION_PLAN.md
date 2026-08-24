# Post-Sudachi Normalization Plan

## Goal

Introduce one explicit normalization layer immediately after Sudachi adaptation
and before decoder comparison or hybrid selection. The layer converts known,
systematically undesirable Sudachi token streams into the corpus's preferred
mechanical representation.

The intended flow is:

```text
source text
  -> raw Sudachi response
  -> source-surface restoration
  -> post-Sudachi normalization
  -> decoder/hybrid analysis
  -> post-hybrid reading repairs
  -> LLM and human review
  -> finalization
```

Raw Sudachi output remains immutable evidence. The normalized stream becomes
the Sudachi baseline consumed by every later stage.

## Responsibilities

### Sudachi adapter

The adapter is responsible only for faithfully representing the external tool
response against the original source:

- preserve the raw Sudachi fields for diagnostics;
- restore token surfaces from source offsets rather than normalized forms;
- collapse variation selectors and meaningful zero-surface morphemes onto their
  source-bearing token;
- reject a response that cannot be mapped monotonically to the source; and
- guarantee that concatenated adapted surfaces reproduce the source exactly.

These operations repair the transport boundary. They are not corpus policy.

### Post-Sudachi normalizer

The new layer applies deterministic corpus policy to the adapted token stream.
A rule belongs here only when it is source-local, does not require decoder or
LLM evidence, and has one preferred result under current corpus conventions.

Initial rule families should cover:

- **Hard source boundaries:** split a Sudachi token at every source whitespace
  boundary and retain each whitespace run as a readingless token.
- **Structural separators:** split internal middle dots and supported
  parentheses when corpus ruby must not cross those characters. Preserve
  established punctuation-bearing spellings that policy explicitly exempts.
- **Semantic parentheticals:** canonicalize forms such as `（株）`, `（有）`,
  `（社）`, `（財）`, `（涙）`, and `（笑）` into readingless brackets plus the
  intended inner reading.
- **Mixed numeric boundaries:** separate Arabic-number portions from lexical
  portions where the numeric layer owns the number, such as `2級`, `小5`,
  `中3`, and `BGM8`. The numeric portion remains readingless unless a separate
  numeric-compound rule explicitly owns the whole surface.
- **Symbolic kaomoji:** map a Sudachi token classified as a symbolic kaomoji to
  the canonical `カオモジ` reading, while retaining `（笑）` as its dedicated
  semantic-parenthetical case.
- **Deterministic script defaults:** replace misleading unit readings of
  standalone uppercase Latin letters with Japanese letter names.
- **Selected lexical boundaries:** apply narrowly reviewed canonical boundary
  rules such as `皆/ミナ 様/サマ -> 皆様/ミナサマ` when the corpus convention is
  intentionally different from Sudachi morphology.

The normalizer must be pure and idempotent. Every output token records the raw
input token indexes from which it was derived, and every applied rule records a
stable rule ID, version, before/after token sequences, and source span.

### Later stages

The following do not belong in the post-Sudachi layer:

- context-dependent readings such as `方/カタ` versus `方/ホウ`;
- decoder-supported corrections such as grouping malformed `戦/セン 争/争`
  into `戦争/センソウ`;
- LLM repair or web research;
- review-only candidate ordering;
- furigana placement; and
- learned or editorial reading preferences whose validity depends on context.

The existing post-hybrid repair layer remains available for reading defaults
and corrections that should see the hybrid result. Rules should move earlier
only after their behavior is proven independent of decoder evidence.

## Data Model

Mechanical analysis should distinguish the two representations explicitly:

```json
{
  "analysis": {
    "mechanical": {
      "yomi": {
        "sudachi_raw": {"tokens": []},
        "sudachi_normalized": {
          "normalizer_version": 1,
          "tokens": [],
          "applications": []
        },
        "hybrid": {"tokens": []}
      }
    }
  }
}
```

`sudachi_raw` is diagnostic evidence and must never be rewritten by policy
rules. `sudachi_normalized` is the only Sudachi representation consumed by the
hybrid strategy, safety projection, LLM target construction, and review-pack
generation. Canonical final tokens remain a separate result.

For each representation, concatenating token surfaces must reproduce the
source text exactly. Normalization may change boundaries and readings, but not
source characters or their order.

## Rule Design

Rules should be typed transformations over token arrays rather than regular
expressions over rendered `surface/reading` strings. Each rule must declare:

- a stable ID and schema version;
- its exact structural predicate;
- whether it splits, merges, or changes a reading;
- source and normalized examples;
- explicit exclusions;
- provenance for newly created readings; and
- regression tests for application, non-application, and idempotence.

General-purpose structural functions should remain code. Small curated lexical
boundary overrides may use a versioned data file. The rendered-string repair
table should not become a second structural normalization system.

Normalization is not safety evidence by itself. A normalized token still
requires the ordinary deterministic, decoder, LLM, or human evidence before it
is accepted.

## Migration

### Phase 1: inventory and characterization

1. Enumerate transformations currently spread across the Sudachi adapter,
   hybrid strategy, numeric normalizer, parenthetical repair code, and
   finalization backstops.
2. Classify each transformation as transport repair, post-Sudachi corpus policy,
   evidence-based hybrid logic, or final canonicalization.
3. Build a fixture set from observed cases, including whitespace-spanning
   names, middle dots, parentheticals, mixed alphanumerics, kaomoji, variation
   selectors, and `皆様`.

### Phase 2: implementation without behavior change

1. Add a typed normalized-Sudachi result and a pure normalization entry point.
2. Move existing source-local transformations into that entry point while
   preserving their current order and output.
3. Store raw and normalized streams separately in new mechanical artifacts.
4. Make hybrid strategies consume only the normalized stream.
5. Keep temporary compatibility wrappers at old call sites, with assertions
   that old and new outputs match.

### Phase 3: comparison and artifact migration

1. Shadow-run old and new paths over finalized historical batches and queued
   documents.
2. Report changes by rule, surface, reading, token count, LLM-target count, and
   final canonical difference.
3. Block rollout on source-surface mismatches, unexplained reading changes, or
   increased unresolved spans.
4. Regenerate non-authoritative intermediate artifacts from the earliest
   affected deterministic stage.
5. Migrate authoritative finalized data only for intentional canonical changes,
   using versioned reports, checksums, backups, and an idempotent dry run.

### Phase 4: cleanup

1. Remove duplicate structural branches from hybrid rendering and finalization
   after one stable dev cycle.
2. Retain finalization checks as invariant validation, not as silent repair.
3. Move qualifying structural entries out of `post_hybrid_repairs.tsv`; leave
   contextual reading preferences there.
4. Document the normalized-Sudachi schema as part of the stable artifact
   contract.

## Tests and Observability

Required automated coverage includes:

- exact source reconstruction before and after normalization;
- idempotence of the complete normalizer and every individual rule;
- stable provenance when one token splits or several tokens merge;
- no ruby-bearing token crossing whitespace or configured separators;
- deterministic handling of unknown component readings;
- preservation of exempt punctuation-bearing proper names;
- parity between old and new output during the compatibility phase; and
- artifact round trips through hybrid generation, LLM target construction,
  review generation, finalization, and decoder-corpus export.

Batch diagnostics should count applications and list previously unseen input
shapes. A sudden change in application frequency should be visible to refill
and review-sync logs, but normalization should not require human intervention
when all invariants pass.

## Completion Criteria

The migration is complete when:

- raw and normalized Sudachi streams are separately inspectable;
- all downstream mechanical consumers use the normalized stream;
- structural Sudachi workarounds have one implementation location;
- source reconstruction and idempotence hold across historical fixtures;
- shadow comparison has no unexplained canonical differences;
- existing authoritative data has been migrated or explicitly declared
  unaffected; and
- duplicate compatibility paths have been removed.
