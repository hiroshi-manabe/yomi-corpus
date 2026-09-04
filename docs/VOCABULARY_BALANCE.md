# Vocabulary Balance

## Initial inventory

Build the vocabulary baseline from every ZIP archive in BCCWJ 1.1
`Disk2/TSV_SUW_NT`. Aggregate by UniDic lemma and retain token frequency,
document frequency, reading, POS, written-form, and register distributions.
Common-noun frequency and document frequency are counted separately so
homographic uses under another POS do not qualify a candidate.

Compare the BCCWJ SUW inventory with every document currently included in the
dev track, including both finalized documents and documents already assigned to
in-flight batches. Tokenize current text with the full Sudachi dictionary in
split mode A, the closest available match to BCCWJ short units. A BCCWJ lemma is
considered represented when its lemma or any observed BCCWJ written form
matches an exact current Sudachi surface or dictionary form.

Run:

```bash
python scripts/build_bccwj_vocabulary_gap.py
```

Generated analysis files under `data/analysis/vocabulary_balance/` are ignored
artifacts:

- `bccwj_lemma_vocabulary.tsv.gz`: complete BCCWJ lemma inventory;
- `current_corpus_vocabulary.tsv.gz`: current exact surface/dictionary-form
  inventory;
- `missing_kanji_common_nouns.tsv`: kanji-bearing common nouns meeting the
  configured BCCWJ frequency threshold but absent from the current inventory;
- `manifest.json`: source identity, parameters, counts, and comparison method.

The default candidate threshold is deliberately permissive: at least five
BCCWJ common-noun occurrences and zero current matches. It creates an analysis
pool, not an approved reordering list.

## LLM gate and reordering

The next stage should classify only a narrowed subset of the missing list. Keep
that decision cheap by applying deterministic frequency and document-frequency
cutoffs first, using a small categorical output, and caching decisions by
lemma, written forms, prompt revision, and model. No LLM classification is part
of the initial inventory command.

Only approved lemmas should feed processing-order optimization. Reordering must
operate on the unfrozen, unreserved suffix and should use the retained written
forms as search keys. Coverage selection and installation remain separate,
reviewable operations.

## Document-selection planning layer

Vocabulary balancing is a planning concern, not part of canonical corpus or
pipeline semantics. The canonical processing-order authority continues to
store only the accepted mapping:

```text
processing slot -> source record ID
```

A separate, replaceable planner proposes source records for future mutable
slots. It may record why each proposal was made, but review, finalization,
export, and model-building code must not branch on that reason. Once a proposal
is accepted and installed, downstream processing treats it as an ordinary
processing-order assignment.

The initial policy may reserve the first 50,000 source documents for sequential
sampling and select vocabulary-focused documents only from the remaining source
pool. Processing can then alternate bounded phases, for example:

1. process 2,000 documents from the reserved sequential sequence;
2. process 2,000 vocabulary-coverage documents selected from outside the
   reserved range;
3. resume the sequential sequence for another 2,000 documents; and
4. repeat, while periodically evaluating vocabulary gain and distributional
   bias.

The exact phase size, ratio, and reserved range are policy parameters rather
than invariants. They can be changed without migrating reviewed corpus data.
The planner should maintain its own state for:

- the next unused sequential source record;
- campaign eligibility and approved target vocabulary;
- source records already proposed, assigned, or processed;
- phase definitions and deterministic selection parameters; and
- per-proposal coverage scores and selection rationale.

Campaign selection must use stable source identities, exclude every source
record already consumed by either policy, and never advance the sequential
cursor. Conversely, sequential selection must skip a reserved source record if
it was already consumed through another explicitly approved policy. Planning
must stop at existing processing-order reservations and may modify only the
unfrozen suffix.

## Sparse coverage index

Build a reusable sparse index before implementing a particular 2,000-document
selection algorithm. The index records which approved target lemmas occur in
each eligible source document and how many times they occur. Keep the actual
count even if a later scoring policy caps one document's contribution to one
example per lemma.

Use an ignored SQLite database rather than canonical JSON artifacts. A minimal
schema is:

```text
documents(source_line_no, source_record_id, text_length, quality_signals)
targets(target_id, lemma, approved_forms)
hits(source_line_no, target_id, occurrence_count)
metadata(key, value)
```

Enforce uniqueness on stable source identity and on target identity. Index
`hits(target_id, source_line_no)` for finding documents that cover a target and
`hits(source_line_no, target_id)` for scoring all targets in one document.
Selection plans should refer to stable source identities; source line numbers
are efficient locators within one validated source build, not durable identity.

The initial index may cover only campaign-eligible source documents after the
reserved first 50,000 records. Build it in two passes:

1. scan source text with fast literal matching over every approved written
   form; and
2. tokenize only matched documents with Sudachi A when exact short-unit
   validation is needed to reject substring collisions.

Store enough metadata to reject stale reuse: source path and fingerprint,
source sequence epoch, target-list hash, matching-policy version, Sudachi
dictionary and version, split mode, eligibility boundary, and creation time.
Changing any semantic input requires rebuilding the index or producing a new
version. Interrupted builds must not be mistaken for complete indexes.

This database is a reproducible planning cache. It must not become an input to
review, finalization, corpus export, or decoder training, and it may be deleted
and rebuilt without changing canonical state. Different scoring experiments
should reuse the same validated index and write separate plan artifacts rather
than rescanning the multi-million-document source.

Installation should be a separate command with validation and an explicit
plan artifact. At minimum, validate source-identity uniqueness, absence of
overlap with frozen or reserved assignments, deterministic replay of the plan,
and preservation of the canonical slot sequence. This keeps experimental
selection logic subordinate to the small, stable processing-order interface.
