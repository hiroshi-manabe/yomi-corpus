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

Installation should be a separate command with validation and an explicit
plan artifact. At minimum, validate source-identity uniqueness, absence of
overlap with frozen or reserved assignments, deterministic replay of the plan,
and preservation of the canonical slot sequence. This keeps experimental
selection logic subordinate to the small, stable processing-order interface.
