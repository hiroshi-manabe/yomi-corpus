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

## Campaign planning and preview

The first campaign uses the complete generated
`missing_kanji_common_nouns.tsv` inventory as its input. It does not depend on
the earlier manually filtered Core-derived headword lists. Campaign targets
must be kanji-bearing forms of at least two Unicode characters. One-character
forms remain eligible for ordinary sequential processing, but they do not enter
the campaign target index or contribute to campaign selection scores. They are
too ambiguous and too easily reward very short noisy documents. Selection is
best effort: the planner may leave unavailable or low-value targets uncovered.

Run the planning phases independently:

```bash
./plan-vocabulary-campaign index
./plan-vocabulary-campaign plan
./plan-vocabulary-campaign preview
```

Install the optional `planning` dependency for the accelerated matcher used by
the multi-million-document index scan. The command retains a pure-Python
fallback for small fixtures and constrained environments.

`preview` replaces the earlier single-strategy sample with a read-only
selection experiment over the proposed 2,000-document candidate set. It
compares the current score, a novelty-first score, a strict gain-per-character
score, and a balanced score at 50,000, 100,000, and 250,000 reviewed-character
budgets. The UI previews the 100,000-character selections and reports distinct
target coverage, repeated coverage, density, and duplicate share. The
`experiment` command is an explicit alias for this phase.

`all` runs the same three phases in order. The default plan proposes processing
slots 2,001 through 4,000, aims for three documents per target, and writes an
ignored SQLite index and JSON plan under
`data/analysis/vocabulary_balance/`. None of these commands reserves a batch,
advances the processing-order cursor, or modifies the order binary.

Before installation, publish the deterministic metric experiment in the review
site. The review UI labels this artifact as uninstalled and read-only and
exposes no editing, task, submission, or browser-persistence action. Publishing
merely copies `active_campaign_preview.json`; accepting and installing a plan
remains a separate future operation.

## Historical-register classification

Vocabulary-focused selection can overrepresent archival material. Detect this
with an advisory sentence classifier rather than a destructive source filter.
The classifier reports independent evidence for historical kana orthography,
old character forms, and kanbun-style syntax. Every result retains its matched
text, offset, evidence type, and score so sampling decisions can be audited.
An isolated name-prone old form such as `澤` is weak evidence and must not by
itself classify an otherwise modern sentence.

Aggregate sentence results at document level only after classification. Report
both the share of flagged sentences and the share of visible characters they
contain. The reusable classifier's conservative drop recommendation requires
at least two flagged sentences and requires both shares to cross its configured
thresholds. Campaign experiments use a separate recorded gate: at least three
flagged sentences, 10% of sentences, and 15% of visible characters. Thus a
short historical quotation in modern prose remains eligible, while a document
with a material historical-review burden is omitted from that campaign
proposal. These recommendations do not change processing order or canonical
source data.

Apply a separate annotation-density gate to source prose containing many
author-supplied parenthetical readings. A candidate is omitted when one
sentence contains four or more `表記（かな）` glosses. Counts reset at sentence
boundaries, so ordinary name readings in separate sentences do not accumulate.
This is a review-cost and corpus-style signal, not evidence of historical
orthography; report it separately from the historical-register categories.

## Campaign-specific quality gates

Optimizing rare-target coverage per reviewed character creates selection
pressure toward pathological documents. Indexes, catalogues, concatenated
headlines, scraped product listings, and machine-corrupted text can contain many
rare forms with little ordinary prose. These records may have survived the
general source cleaner and may be uncommon under sequential sampling, but they
become prominent when ranked by character efficiency. Treat quality filtering
as a constraint applied before campaign scoring, not as another reward term.

The campaign selector may intentionally apply a stricter whitespace rule than
the upstream cleaner. The corrected source epoch used the cleaner rule
`Japanese-inner-spaces / characters > 0.01`. Two undesirable preview documents
fell just below that boundary at 0.963% and 0.841%, while their total half-width
space shares were 2.58% and 1.33%. As an initial campaign-only safeguard,
evaluate documents of at least 1,000 characters with total half-width-space
density of at least 1% as exclusion candidates. This apparent duplication is a
deliberate consequence of different responsibilities: the upstream threshold
defines a stable general corpus, while the campaign gate protects an optimizer
that amplifies borderline records. Promote a proven rule upstream only through
a future explicit source-epoch migration.

Also reject a campaign candidate when any resulting review unit exceeds 800
characters. Use review-unit length rather than total document length: a long
document made of ordinary sentences remains manageable, whereas a 2,029-
character concatenated legal-title unit does not. In the current 51-document
character-efficiency preview, this limit rejects only that outlier; the next
largest units contain 668 and 581 characters.

High densities of book-title brackets, bullets, and exclamation marks are useful
diagnostic evidence for an index page, but no individual punctuation count is a
general hard rejection rule. For example, the observed index page contained 104
`『...』` pairs, 148 bullet marks, and 149 exclamation marks in 5,502 characters,
while legitimate bibliographic prose can also contain many title brackets.
Retain these measurements in evaluation output and prefer a small validated
combination over accumulating unrelated ad hoc filters.

## Minimal semantic quality check

After deterministic campaign gates, use one narrow Sol classification to catch
semantic substitution text and similarly unusable scraped text that surface
rules miss. Use `gpt-5.6-sol` with no reasoning, low verbosity, no tools, and the
following prompt:

```text
Is the following text incoherent word salad rather than meaningful Japanese?
Answer only "y" or "n".

{text}
```

For documents longer than 500 characters, `{text}` is the first three
punctuation-delimited sentences. For documents of at most 500 characters, use
the complete document; otherwise a plausible opening can hide corruption later
in a short record. Parse only a trimmed, case-insensitive exact `y` or `n`.
Anything else is malformed and follows the ordinary retry policy. Do not use a
substring test, because explanatory output can contain both letters.

The initial Sol experiment made 67 standard API calls. Five known substitution-
salad documents returned `y` in all 15 repeated trials, 15 varied coherent
controls returned `n`, and the remaining 37 preview documents produced 36 `n`
responses and one useful `y` for duplicated e-commerce/path garbage. Every
response had the requested one-character form. A 149-character corrupt record
whose first three sentences appeared superficially coherent initially returned
`n`; using its complete text returned `y` in three of three trials. Across the
57 unique preview documents, requests averaged approximately 195 input tokens
and five billed output tokens. At the recorded Sol standard rates, 2,000 checks
cost approximately USD 2.25, or roughly USD 2.50 with 10% replacement work;
batch execution approximately halves that estimate. These observations justify
a campaign experiment, not a claim of production-level classifier accuracy.

Do not impose a minimum document length merely to prevent budget-tail filling.
The 149-character corrupt record covered only the one-character target `屯` and
was selected when 158 characters remained in a 100,000-character budget. The
two-character target minimum removes this particular incentive without losing
useful short prose. More generally, do not require exact budget exhaustion, and
continue to preview a random sample before installing a campaign.

Run the classifier against the current read-only campaign preview with:

```bash
./classify-historical-register
```

The ignored report under `data/analysis/vocabulary_balance/` is an evaluation
artifact. Review false positives and false negatives before adding this signal
to campaign selection. Future campaign scoring should charge selected text by
character count, not document count, because reviewer effort follows text
volume much more closely than the number of source records.
