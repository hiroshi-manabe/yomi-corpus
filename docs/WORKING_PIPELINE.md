# Working Pipeline Draft

This document is a working summary of the current intended pipeline. It is more
concrete than the general design note and intentionally reflects the latest
decisions, even where details are still unsettled.


## 1. Current Direction

The project should stay simple.

At this stage:

- the original source JSONL should remain unchanged
- units should be derived from source documents and stored sequentially
- unit-local analysis should live inside each unit record rather than being
  split into separate `candidate`, `decision`, and `review` tables
- deterministic rules should run first
- the LLM should only be used for units that deterministic rules cannot mark as
  "human-check only"
- the final authority is still human review


## 2. Scope of the Main Judgments

The pipeline now has two different judgment granularities:

- sentence-like units for non-target status and yomi correctness
- batch-level alphabetic entities for the minor-alphabetic problem

For sentence-like units, the main questions are:

1. Is this non-target material?
   Examples:
   - old kana
   - old orthography
   - classical Japanese
   - kanbun
   - foreign-language text
   - garbled text

2. Is the current mechanically generated yomi correct with high confidence?

For Latin/alphanumeric material, the main question is:

- which entity types in the batch should be treated as naturally acceptable in
  modern Japanese context, and which should be treated as out of scope?

The practical goal is not perfect theoretical classification. The goal is to
spend effort where it helps and avoid the long tail that would consume a large
fraction of time for little corpus value.


## 3. Data Model

## 3.1 Source documents

The source JSONL stays as-is. No transformed "document record" needs to replace
it. It is enough to maintain stable references back to the original input.

Each batch run should still assign internal document IDs such as:

- `doc_id`
- `source_file`
- `source_line_no`

The original JSON payload is not rewritten.

## 3.1.1 Batch artifacts vs. global state

The alphabetic subsystem should keep both:

- immutable batch-local artifacts
- cross-batch global state

Batch-local artifacts include:

- alphabetic entity occurrences for one batch
- alphabetic entity types for one batch
- projected unit-level alphabetic flags for one batch

Cross-batch global state includes:

- a token decision registry
- an append-only token evidence log

The batch artifacts describe one run. The global state carries knowledge
forward to later batches.

## 3.2 Units

Units are sentence-like spans derived from each source document.

They should be sequential inside a document.

Required unit fields:

- `doc_id`
- `unit_id`
- `unit_seq`
- `char_start`
- `char_end`
- `text`

Recommended reference fields:

- `source_file`
- `source_line_no`
- `split_rule_version`

Important invariant:

- `text` must always be recoverable from the original document text by
  `char_start` and `char_end`

No explicit previous/next-unit context fields are needed for now.

If the pipeline later chooses not to use some units, that should be expressed
through flags, not by changing the unit order.

## 3.3 Unit-local analysis

Rather than separate `candidate`, `decision`, and `review` record families,
each unit should contain nested analysis blocks.

Suggested shape:

```json
{
  "unit_id": "ja_cc_level2:0000000123:u0007",
  "doc_id": "ja_cc_level2:0000000123",
  "unit_seq": 7,
  "char_start": 418,
  "char_end": 457,
  "text": "毎週水曜日はお昼のコンサート「Concerts de Midi（ミディ・コンサート）」が開催されています。",
  "analysis": {
    "mechanical": {
      "non_target": {
        "value": false,
        "certain": false,
        "signals": []
      },
      "minor_alphabetic_sequence": {
        "value": true,
        "certain": false,
        "matches": ["Concerts de Midi"]
      },
      "yomi": {
        "rendered": "...",
        "certain": false,
        "sudachi": {},
        "ngram_decoder": {}
      }
    },
    "llm": {
      "non_target": null,
      "minor_alphabetic_sequence": null,
      "yomi_is_correct": null,
      "yomi_repair": null
    },
    "human_review": {
      "pass1": null,
      "pass2": null
    }
  }
}
```

This is intentionally denormalized. Simplicity is more important than perfect
relational cleanliness at this stage.


## 4. Unit Segmentation

The source JSONL records may contain long document text. Annotation and review
should happen at smaller sentence-like units.

Initial segmentation policy:

- split mainly on `。`, `！`, and `？`
- optionally respect source line breaks if they are already meaningful
- keep exact document offsets for each unit

The unitizer does not have to be linguistically perfect yet. It only needs to
be stable, reversible, and good enough for batch processing.


## 5. Mechanical First Pass

The first pass should process a small batch of documents, for example:

- 100 documents per run initially

That size is small enough to inspect manually and large enough to expose common
failure patterns.

For each unit, the mechanical pipeline should produce:

### 5.1 Non-target judgment

- raw signals that may later help predict whether the unit is non-target
  material, such as classical Japanese, kanbun, foreign-language text, or
  garbled text
- no mechanical `value` or `certain` decision yet

Those signals will likely include:

- Sudachi behavior
  The current mechanical baseline should use Sudachi B-mode rather than the
  default C-mode, so compounds are first split into middle units before hybrid
  refinement.
- N-gram decoder behavior
- script and orthography heuristics

The exact rules are intentionally deferred until reviewed data exists.

For the current hybrid yomi policy, supported decoder-driven reading changes can
be used broadly. If the decoder differs from Sudachi on the same surface span
and the exact decoder entry has real N-gram support, the hybrid output may use
the decoder reading as a tentative override. Unigram-only fallback output should
not override Sudachi.

The decoder is expected to apply a count threshold before reporting order-2
support. In downstream yomi-corpus rules, `piece_orders[0] >= 2` therefore
means repeated support in the decoder corpus, not merely one observed
transition. A wrong candidate may still rank high, but it should not become
trusted support if its relevant boundary is backed only by a singleton 2-gram.

When the hybrid strategy splits one Sudachi token into multiple decoder entries,
each later decoder entry must have cross-boundary support: its first piece order
must be at least 2. Internal support after an order-1 boundary is not enough to
justify replacing Sudachi's whole-token segmentation.

For stable two-kanji confidence experiments, the decision unit should remain the
hybrid rendered token, normally inherited from Sudachi plus accepted hybrid
overrides. Decoder evidence should be projected onto that token's character
span. Decoder-only subpieces such as `古/コ 本屋/ホンヤ` must not make the
hybrid token `古本屋/フルホンヤ` safe. A stable two-kanji token may forgive only
its own missing support; it must not forgive the boundary into a following
non-stable token such as `入っ/ハイッ`.

Stability should be judged from the raw SudachiDict CSV surface-to-reading
inventory, not from `Dictionary.lookup()` or the decoder. Include ordinary
entries and component-only entries whose left/right connection IDs are `-1`.
The token is stable only when that raw inventory has exactly one reading for
the surface. POS should not be a gate: proper nouns such as `群馬/グンマ` can be
stable if unique, while common/proper ambiguous surfaces such as `大麻`
(`タイマ` and `オオアサ`) are not stable.

After the hybrid strategy, the pipeline may apply a small post-hybrid repair
memory to the rendered yomi string. The first implementation treats all repair
rules as regular-expression substitutions over whitespace-separated rendered
pairs, using `config/yomi/post_hybrid_repairs.tsv`. This layer is for known
systematic fixes such as `若しくは/モシクワ -> 若しくは/モシクハ` and
`身近/ミジカ -> 身近/ミヂカ`; it does not make a unit safe by itself. Each
application must be logged in `analysis.mechanical.yomi.post_hybrid_repairs`
with the rule ID, matched string, replacement, count, and source.

Numeric runs should be grouped and excluded from normal yomi reading decisions.
For example, Sudachi-style `2/ニ 0/レイ 2/ニ 1/イチ` should become `2021/`.
Number pronunciation is a separate future module, not part of the current yomi
pipeline.

The canonical `surface/reading` token format should be structurally validated
before any automatic or LLM `OK` is trusted:

- if `surface` contains kanji or Latin letters, `reading` must be non-empty and
  contain only katakana plus `ー`
- if `surface` is digits only, `reading` must be empty; `2021/` is valid, but
  `2021/2021` is invalid
- otherwise, `reading` must equal `surface` with hiragana converted to
  katakana and all non-kana characters left unchanged; for example `です/デス`
  and `。/。` are valid

This validation is separate from yomi correctness. A structurally invalid unit
may still be sent to LLM triage so the model can identify `Skip`, but an LLM
`OK` must be blocked and converted to `Review`.

Original source whitespace should not be dropped. Before Sudachi and decoder
processing, convert ASCII space `U+0020` to NBSP `U+00A0`; keep full-width
space `U+3000` as-is. The canonical rendered yomi should preserve these as
explicit whitespace tokens, for example ` / ` for NBSP and `　/　` for
full-width space. Ordinary ASCII space remains only the token separator in the
rendered yomi string.

Yomi review should prioritize reading correctness over ideal segmentation.
Katakana expressions may be over-split by the current analyzers, but this is not
a failure if the readings remain correct. A later token-merge layer can be
added if downstream consumers need cleaner segmentation.

The first yomi auto-accept pass should run after mechanical yomi generation and
write a separate `units.yomi.auto_accept.jsonl` artifact. It should mark
`analysis.mechanical.yomi.auto_accept.value=true` only when the unit has no
unresolved non-numeric readings, Sudachi and the decoder agree on the rendered
output, and either the decoder's top candidate is supported from start to end by
repeated N-gram evidence or the same support check passes after the stable
two-kanji relaxation. The selected criterion should come from the batch's
explicit `yomi_policy.auto_accept_profile`, not directly from the track name.
Tracks only provide defaults: `working` should default to `strict`, while `dev`
can default to `stable_two_kanji` so the relaxation is exercised under
realistic pipeline runs. It remains an audited criterion: accepted rows should
still record which profile was used and whether the stable two-kanji relaxation
was involved. This is a review-skip flag, not a general proof of correctness.

N-gram confidence remains a debug metric rather than a pipeline decision. The
current useful experiment is to split only on `、`, reject alphabetic-containing
units, keep empty-reading kanji-like content inside the span as unsafe, exempt
kana-only or symbol-only adjacent boundaries, and require all other adjacent
entry boundaries to have the later entry start with `piece_orders[0] >= 2`.
Coverage should be interpreted by character count and by kanji-like span
coverage, not only by raw span count.

The stable two-kanji relaxation is a second debug experiment layered on top of
that idea. It starts from the hybrid rendered output, asks whether each hybrid
token has projected repeated decoder support, and then allows a token-local
fallback only for two-kanji compounds that have exactly one reading in the raw
SudachiDict CSV inventory. This is meant to reduce false negatives caused by
sparse decoder vocabulary, not to trust decoder over-segmentation.

As a profile, this relaxation should remain available on any track. For example,
`dev` can run with `auto_accept_profile=off` or `strict` for conservative
debugging, and `working` can later run with `stable_two_kanji` once the full
production pipeline is ready. The chosen profile must be stored with the batch
so reruns remain reproducible even if track defaults change.

Use separate thresholds for reading changes and review-skipping safety. A
decoder reading that differs from Sudachi and has repeated 2-gram support can be
treated as an ordinary tentative override signal; a high error rate is
acceptable because review is still the default. A safe auto-accept signal should
be much more conservative: Sudachi and decoder should agree, and the relevant
span should be supported from start to end by the stricter repeated-2-gram
boundary rule.

### 5.2 Batch-level alphabetic entity extraction

The alphabetic problem should not be treated as a pure sentence-level
classification task.

Instead, for each batch:

- extract all alphabetic entity occurrences mechanically from all units
- aggregate them into entity types
- apply current whitelist/blacklist lookup
- send only unresolved entity types to the LLM once, then cache the judgment
  globally
- project cached `out_of_scope` judgments back to units as provisional skip
- let final human review correct provisional skip with the same `Skip` checkbox

The cache keeps source metadata for audit/debug, but behavior should only need
the effective status: `in_scope`, `out_of_scope`, or `unknown`.

This is not limited to English. The problem includes other Latin-script foreign
material such as French.

Examples of entity types that may be skipped:

- `concerts de midi`
- `run boys`

Examples of entity types that may be retained:

- `iphone`
- `android`

### 5.3 Mechanical yomi

The mechanical pass should produce:

- one best yomi candidate for the full unit
- raw agreement and confidence signals only; no sentence-level `certain` flag
  yet

This should be based on Sudachi plus `yomi-decoder` plus mechanical agreement
signals that can later be calibrated against reviewed data.

Segmentation policy for yomi evaluation:

- if the readings are correct, overly fine segmentation should not by itself be
  treated as an error
- examples like `やってしまった` becoming `やっ/ヤッ て/テ しまっ/シマッ た/タ`
  are acceptable as a morphological-analysis choice
- evaluation should focus on reading correctness first, and segmentation only
  secondarily when it causes a real reading or usability problem


## 6. Interpretation of "Certain"

For sentence-level tasks, "certain" is a future concept, not an active one yet.

Current policy:

- do not assign `certain=true` mechanically for non-target judgment
- do not assign `certain=true` mechanically for yomi safety
- collect raw features now and define certainty rules only after reviewed data
  accumulates

So the current effective branch is:

- non-target judgment
- yomi correctness judgment

For both of those, go to the LLM unless a future reviewed-data-backed rule says
otherwise.

For alphabetic material, the equivalent branching point is the entity type:

- if an entity type is already covered by whitelist/blacklist rules, do not ask
  the LLM
- otherwise send that entity type, not the whole sentence, to the LLM
- treat the LLM answer as a cached provisional decision, not as final human
  corpus review


## 7. Minor Alphabetic Sequences

This is a cost-control policy as much as a linguistic policy.

The working assumption is:

- a small percentage of difficult foreign alphabetic strings could consume a
  disproportionate amount of time
- therefore the system should prefer to skip low-value long-tail cases rather
  than aggressively annotate everything

## 7.1 Batch-level token inventory

The batch should produce two alphabetic artifacts:

- entity occurrences
- entity types

The entity-type table is the main decision surface for this problem.

Sentence-level flags should be derived afterward from entity-type decisions.

## 7.2 Whitelist

The project should keep a whitelist of Latin/alphanumeric entity types that are
accepted as rooted in modern Japanese usage.

Examples:

- `iPhone`
- `Android`

Initial idea:

- start with no whitelist
- keep units that were accepted and successfully yomi-annotated
- extract useful alphabetic strings from those accepted units
- add good recurring items to the whitelist

Then the projection rule can become:

- if all alphabetic entity types in a unit are in the whitelist, mark that unit
  safe on the alphabetic dimension

## 7.3 Blacklist

A blacklist-oriented approach may be simpler than generating regex rules for
out-of-scope Latin/alphanumeric entities.

Current preference:

- start with word-level whitelist and blacklist entries
- use word-boundary-aware matching for Latin/alphanumeric material
- match case-insensitively by default
- handle short tokens and acronyms more cautiously with exact-case exceptions
- treat a single alphabetic letter as a deterministic exception that does not
  enter LLM judgment; examples include the `T` in `Tシャツ`, but this is a
  general low-value exception rather than a lexical whitelist entry
- avoid regex unless there is a clear payoff

This remains a working decision, not a final one.

## 7.4 LLM and human judgment unit

The preferred unit for LLM and human judgment is now the alphabetic entity, not
the whole sentence.

Recommended flow:

- extract alphabetic entities mechanically
- remove already known whitelist/blacklist entries
- ask the LLM to classify unresolved entity types, with short example snippets
  from the batch
- append the LLM results to the cross-batch judgment cache
- do not ask the LLM again for the same effective entity key unless a cache
  entry is explicitly superseded
- mark units containing any cached `out_of_scope` entity as provisional skip

Sentence context is still useful, but mainly as supporting evidence for the
entity-level decision.

The LLM stage should answer whether the entity is naturally usable in modern
Japanese context or is obscure/foreign/noisy enough to skip. This answer may
pre-check `Skip` for affected units, but it is not final deletion.

At final review time, provisional alphabetic skip units should be greyed out
with the same `Skip` checkbox already checked. If the human leaves the checkbox
checked, no entity-level status changes. If the human unchecks it, the
triggering `out_of_scope` entities become effective `in_scope` entries. If a
human checks `Skip` for a normal unit, that also does not change entity status,
because the reason may be unrelated to alphabetic material.

## 7.5 Rule harvesting

If the LLM or a human identifies an entity type as out of scope, the system may
later harvest a reusable blacklist-like entry from that decision.

Current preference:

- do not do this too early
- start with explicit token entries
- only introduce broader matching patterns if maintenance remains manageable


## 8. Classical Japanese and Kanbun

This area is less settled.

Current working idea:

- rely first on how well Sudachi and the N-gram system can analyze the unit
- combine that with orthographic and script-level heuristics
- store those signals as features for later learning
- use LLM judgment for now instead of trying to force an early mechanical
  classifier

Potential signals:

- old kana
- old orthography
- unusual auxiliary patterns
- script mixtures that rarely occur in modern prose
- systematic analysis failures from Sudachi or the decoder

The exact decision boundary is still unclear and should be refined by looking at
real examples and failure cases.


## 9. LLM Stage

The current direction is to split LLM work into two separate questions:

1. scope triage: should this raw text stay in the modern Japanese reading
   corpus?
2. reading generation: for each unresolved kanji/Latin target, what reading
   does the LLM assign in context?

The LLM should no longer be asked to decide yomi correctness directly as
`OK/Review/Skip` in the main path. That label set remains useful as historical
eval context, but the production direction is more diagnostic: compare an
independent LLM reading against the mechanical Sudachi/hybrid reading, then use
agreement and disagreement as review-routing signals.

### 9.0 Scope Triage

Scope triage is a compact binary task over raw text. The model returns exactly
one token:

- `Keep`: process the unit normally
- `Skip`: exclude the unit as non-target material

`Skip` covers foreign prose, old Japanese prose, kanbun, Chinese, garbled text,
spam, and similar non-target material. The prompt should avoid project-internal
terms such as "kobun/kanbun stage" except as examples; the operational concept
is simply target vs. non-target.

`Skip` also covers privacy or reputational-risk material that identifies a
private person together with sensitive negative information. Examples include
arrest, criminal suspicion, accusations, scandals, disciplinary action,
illness, or similar private/reputational details. This is a conservative
labor-saving rule: the corpus has enough ordinary modern Japanese text, so when
scope triage is unsure about this risk, it should choose `Skip`.

`Skip` should be decided by the dominant language and style of the unit, not by
isolated orthographic markers. A modern Japanese sentence remains target text
when old kana, old kanji, kanbun, Chinese, or foreign text appears only inside
short titles, names, bibliographic strings, or incidental quoted fragments. For
example, a modern bibliographic explanation that mentions titles such as
`東京掃苔録`, `東都掃苔記`, or `日本醫事新報` should not become `Skip` solely
because those titles contain old characters.

Longer quotations and citations use a stricter labor-saving exclusion rule. If
the unit contains even one full sentence of old kana, kanbun, Chinese,
foreign-language text, or other non-target running text, label the whole unit
`Skip`. Modern Japanese is abundant enough that losing the surrounding modern
frame is acceptable, and this avoids a later review pass over whether text is
target or non-target. The exceptions are compact embedded material such as
proverbs, fixed expressions, short titles, proper names, journal/book names, and
bibliographic labels; those do not make the unit `Skip` by themselves.

This task should usually use the `economy` profile for both dev and working
until evals show a clear reason to spend more. Its output is only a scope gate;
it is not expected to notice yomi errors.

### 9.0.1 LLM Reading Generation

LLM reading generation is the main yomi-quality signal being developed now.
The default input is plain source text with exactly one target marked by
`**...**`. The model returns JSON with exactly one key: the marked target
surface, and the value is the target's reading in hiragana.

Example:

```text
お**話**しします。 -> {"話":"はな"}
```

The marked target should normally be a kanji run or Latin-containing target
inside a Sudachi/hybrid token, not an arbitrary whole sentence. The prompt must
tell the model to use unmarked text only as context and to exclude following
kana/okurigana from the target reading.

The first implementation should stay deliberately simple:

- start from the stored Sudachi/hybrid token sequence
- render a plain marked-source context for the prompt
- optionally store a no-space furigana context as debug metadata
- split each annotated token into target chunks by furigana alignment
- query only chunks that need an independent reading signal
- keep the original full token yomi as the canonical artifact

Deterministic skip hooks can reduce query volume:

- skip stable two-kanji compounds whose raw SudachiDict surface has exactly one
  reading and whose current reading matches it
- later, skip targets whose N-gram evidence is overwhelmingly dominated by one
  reading, for example at least 99.5% of observed support

Those skips are query suppression, not final acceptance by themselves. Their
source and reason should be logged so later audits can separate mechanical
confidence from LLM agreement.

LLM reading results should be applied as comparison metadata:

- `match`: LLM reading equals the current mechanical reading
- `mismatch`: LLM reading differs from the current mechanical reading
- `missing_or_parse_error`: the response could not be trusted
- `skipped`: deterministic rule suppressed the query

Agreement is a candidate signal for bulk review or future auto-acceptance, but
it should remain distinguishable from N-gram safety, stable two-kanji safety,
and human approval. Disagreement should route the target or unit to focused
review or later repair. Ambiguous cases such as `辛い` should eventually be
handled by explicit ambiguity policy rather than hidden inside an `OK` label.

This per-target design is intentionally less ambitious than asking the LLM to
repair whole sentences. It avoids relying on the model's token-boundary
judgment, which has been weak in earlier experiments. Larger-span candidate
matching, such as asking for the reading of `給料日直後` and choosing among
Sudachi candidates, can be added later if simple per-token/per-chunk queries
miss too many segmentation errors.

The prompt is short enough that prompt-cache tuning is not a priority for this
task. Accuracy, parse stability, and clean comparison metadata matter more.

### 9.0.2 Per-Target Safety Evidence

The current whole-unit auto-accept experiments ask whether an entire sentence
or comma-span is safe, for example because Sudachi and the decoder agree and
the span has repeated N-gram support. A more useful future direction is
per-target safety: every yomi-bearing target gets its own evidence record, and
only targets without enough evidence are sent to the LLM or highlighted for
human review.

Candidate per-target signals:

- `safe_by_stable_dictionary`: the target is a stable dictionary item such as a
  two-kanji compound with exactly one trusted raw SudachiDict reading, and the
  mechanical reading matches it.
- `safe_by_corpus_frequency`: a trusted training/evidence corpus shows the
  same `(surface, reading)` pair overwhelmingly dominates that surface, for
  example at least 99.5% with a minimum count threshold.
- `safe_by_ngram`: the target's local reading is supported by repeated N-gram
  evidence, not just by a one-off transition.
- `safe_by_llm_agreement`: an independent LLM reading query returns the same
  reading as the mechanical reading.
- `unresolved`: no safety signal applies, the LLM disagrees, or the LLM result
  is missing/malformed.

The unit-level status should be derived from target-level evidence:

- if scope triage says `Skip`, the whole unit remains non-target material
- if every yomi-bearing target has a safety signal, the unit can enter bulk
  review or a later auto-accept experiment
- if any target is unresolved, the unit remains reviewable and the unresolved
  targets should be highlighted

In the human UI, this should support two review modes without changing the
underlying data. Reviewers can skim unhighlighted units quickly, while units
with highlighted unresolved targets get focused attention. The highlight should
encode evidence strength, not just a binary bad/good label. For example,
LLM-only agreement may be visually weaker than stable dictionary plus corpus
frequency support.

Safety signals are not final truth. They are risk labels. The output must keep
the source of each signal, counts, thresholds, and corpus/model versions so
future audits can estimate false-accept rates separately for dictionary
evidence, corpus-frequency evidence, N-gram evidence, LLM agreement, and human
approval.

Corpus-frequency evidence is explicitly probabilistic. For example, a training
corpus may show `大麻/タイマ` as overwhelmingly dominant, while a rare place name
reading such as `おおあさ` still exists. That does not invalidate the signal; it
means `safe_by_corpus_frequency` should be interpreted as "low-risk enough to
de-emphasize for bulk review," not "this surface has no other valid reading."
Rare proper-noun readings are an accepted residual risk unless later audits show
they are frequent enough to need exceptions or weaker highlighting.

Concrete per-target safety records should live under the unit's yomi analysis,
for example `analysis.safety.yomi.targets[]` or an equivalent versioned path.
The exact path can change during implementation, but the record shape should be
stable enough for review UI and audit tools:

```json
{
  "target_id": "ja_cc_level2:0000000020:u0014:r0009c01",
  "surface": "学校",
  "token_surface": "学校",
  "target_start": 18,
  "target_end": 20,
  "token_index": 8,
  "chunk_index": 0,
  "mechanical_reading": "ガッコウ",
  "mechanical_reading_hiragana": "がっこう",
  "is_safe": true,
  "review_status": "safe",
  "highlight_level": "none",
  "accepted_signal_names": ["safe_by_corpus_frequency"],
  "status_reason": "accepted_by_corpus_frequency",
  "signals": [
    {
      "name": "safe_by_corpus_frequency",
      "value": true,
      "count": 337,
      "surface_total_count": 337,
      "share": 1.0,
      "threshold": 0.995,
      "min_count": 5,
      "evidence_artifact": "..."
    }
  ]
}
```

A failing signal should also be recorded, because negative evidence is useful
for debugging and review:

```json
{
  "surface": "方",
  "mechanical_reading": "カタ",
  "is_safe": false,
  "review_status": "unresolved",
  "highlight_level": "strong",
  "accepted_signal_names": [],
  "status_reason": "no_accepted_safety_signal",
  "signals": [
    {
      "name": "safe_by_corpus_frequency",
      "value": false,
      "count": 435,
      "surface_total_count": 729,
      "share": 0.5967,
      "threshold": 0.995,
      "min_count": 5,
      "evidence_artifact": "..."
    }
  ]
}
```

The `方` example is a negative control: it correctly fails the corpus-frequency
rule because its readings are split across `カタ`, `ホウ`, and `ガタ`.

Recommended fields:

- target identity and alignment: `target_id`, `surface`, `token_surface`,
  `target_start`, `target_end`, `token_index`, `chunk_index`
- current reading: `mechanical_reading`, `mechanical_reading_hiragana`
- summary flags: `is_safe`, `review_status`, `highlight_level`,
  `accepted_signal_names`, `status_reason`
- evidence: `signals[]`, where each signal stores its own source-specific
  counts, thresholds, artifact versions, model/profile, or N-gram details

`review_status` should be a small controlled vocabulary, initially:

- `safe`: at least one accepted safety signal applies
- `unresolved`: no accepted safety signal applies
- `skipped`: the containing unit is non-target material

`highlight_level` is UI guidance, not truth:

- `none`: hide/de-emphasize in bulk review
- `weak`: low-risk but worth faint display, for example LLM-only agreement if
  desired
- `strong`: unresolved or conflicting evidence; reviewer should focus here

`status_reason` is a short derived explanation, not a replacement for
`signals[]`. Examples: `accepted_by_corpus_frequency`,
`accepted_by_llm_agreement`, `no_accepted_safety_signal`, `llm_disagreement`,
or `llm_parse_error`.

The unit-level yomi safety summary should be derived mechanically from these
records, for example `all_targets_safe`, `unresolved_target_count`,
`safe_signal_counts`, and `safety_policy_version`. Do not overwrite or collapse
source-specific evidence into a single boolean; later audits need to know which
signal made each target look safe.

Implementation status:

1. Corpus-frequency evidence generation and loading is implemented.
   - Add a script such as `scripts/build_yomi_surface_reading_stats.py`.
   - Input is a configured source corpus path, initially
     `/panfs/panmt22/users/hmanabe/yomi-decoder/data/raw/core_SUW_yomi_final.txt`.
   - Output a stats artifact plus a manifest with source path or ID, size,
     checksum when feasible, mtime, normalization settings, filters, script
     version, and generation time.
   - Add small committed fixture corpora and loader/generator tests.
2. `src/yomi_corpus/yomi/safety.py` now implements pre-LLM deterministic
   per-target safety.
   - Reuse the same target extraction logic as `llm_readings.py` so safety
     records and LLM queue items share stable target IDs.
   - Build deterministic per-target records with stable dictionary and
     corpus-frequency signals first.
   - Standalone lower-case `w`/`ｗ` runs, for example `ｗ` or `ww`, are treated
     as internet laughter markers. They are marked safe with `No ruby` as the
     preferred candidate and skipped by the LLM reading queue.
   - Keep that rule narrow: uppercase `W`, embedded alphabetic strings, and
     lexicalized cases such as `W主演`, `W杯`, `Wii`, `Web`, or `WiFi` must stay
     normal yomi/alphabetic targets.
   - The yomi-reading queue stage writes `units.yomi.safety_pre_llm.jsonl` and
     `yomi_safety_pre_llm_summary.json`, then queues only targets not already
     marked safe.
   - N-gram safety is still pending until the mapping from decoder entries to
     targets is clean enough to audit.
3. Apply LLM reading results back into safety.
   - The LLM target's `current_reading` is the current hybrid rendered reading
     when the rendered token stream aligns one-to-one with Sudachi tokens; raw
     Sudachi readings are only a fallback.
   - On exact LLM/hybrid agreement, add `safe_by_llm_match` and update
     `accepted_signal_names`, `is_safe`, `review_status`, and `highlight_level`.
   - On valid LLM/hybrid disagreement, keep the target unresolved but default
     final review to the LLM candidate. The reviewer can still cycle back to
     the current hybrid candidate explicitly.
   - If a unit was already whole-unit auto-accepted, project that decision into
     each target as `safe_by_unit_auto_accept` so target-level review does not
     show false unresolved highlights.
   - On yomi-reading format/key parse errors after parser salvage, retry with
     the same prompt and task config up to 3 total attempts. Retry results
     override earlier attempts for the same item ID.
   - On mismatch, missing result, or parse error after retry, keep the target
     unresolved and set `status_reason` to the relevant failure.
4. Materialize explicit final artifacts.
   - `units.yomi.safety_pre_llm.jsonl`: implemented deterministic target safety
     before LLM.
   - `yomi_reading_input.jsonl`: implemented unresolved targets sent to LLM.
   - `yomi_reading_retry2_input.jsonl` and `yomi_reading_retry3_input.jsonl`:
     implemented parse-error targets resent with the same prompt.
   - `units.yomi.llm_readings.jsonl`: currently implemented post-LLM artifact
     containing both LLM reading judgments and merged target safety records.
   - `units.yomi.safety.jsonl`: optional future alias/final target safety
     artifact if the review/export stages need a narrower file.
   - Summaries should count total targets, safe-by-signal counts, queued LLM
     targets, LLM agreement, LLM disagreement, parse errors, and unresolved
     targets.
5. Integrate review/debug output.
   - Add an export or UI input that highlights targets by `highlight_level`.
   - Keep old whole-unit `auto_accept` as legacy/debug until the per-target
     safety path is trusted enough to replace it.

Suggested milestones:

1. Stats generator/loader only; inspect coverage on the source corpus.
2. Deterministic safety with stable dictionary plus corpus frequency.
3. LLM queue based on unresolved safety targets.
4. LLM agreement merged into final safety records.
5. Highlighted review/debug export.
6. Decide whether whole-unit auto-accept should be renamed debug-only or removed
   from the normal path.

### 9.0.3 Corpus-Frequency Evidence Interface

Corpus-frequency safety requires this project to read evidence derived from
the decoder/source training corpus. The main pipeline should consume a stable
stats artifact rather than reading decoder-internal runtime files directly.
That artifact can be exported by the decoder, or generated inside this project
from a configured source corpus. For current experiments, generating it here is
often more flexible because this repo can control normalization, filtering, and
threshold policy directly.

Minimum fields:

- `surface`
- `reading`
- `count`
- `surface_total_count`
- `share`
- `corpus_version` or `source_corpus_version`
- optional `exported_at`, `source_corpus_path_or_id`, `decoder_version`, and
  `normalization_version`

The loader should answer questions such as: "Does this surface have one
dominant trusted reading above the configured threshold, and does the current
mechanical reading match it?" It should cache the stats in memory for a
pipeline run, but should record the source artifact path, version, threshold,
and minimum count in every generated safety summary.

If this project generates the stats, the source corpus path should be a config
value and the full corpus may live outside git. The generation command should
write a manifest containing the source corpus path or ID, checksum when
feasible, normalization settings, filters, script version, output path, and
generation time.

Current milestone-1 command:

```bash
python scripts/build_yomi_surface_reading_stats.py
```

It reads `[corpus_frequency]` from `config/yomi/default.toml` and writes the
ignored generated artifacts:

- `data/generated/yomi_surface_reading_stats.tsv`
- `data/generated/yomi_surface_reading_stats.manifest.json`

Use `--no-checksum` for quick exploratory runs when a SHA-256 of the full source
corpus is unnecessary.

Initial corpus-frequency safety defaults:

- `min_count = 5`
- `min_share = 0.995`

These defaults came from inspecting exact-boundary samples with count 5. They
are intentionally "low-risk enough for de-emphasis" thresholds, not proof that
no alternate reading exists.

Important cautions:

- A high-frequency reading can encode corpus bias or systematic annotation
  errors, so it should suppress LLM calls only when thresholds are conservative
  and the evidence remains auditable.
- The threshold should include both share and count; `1/1 = 100%` is not enough
  evidence.
- Surface normalization must be explicit. If the decoder normalizes old
  characters, Latin width, kana, or symbols differently from this project, the
  evidence artifact must say so.
- Changing the source/training corpus changes safety decisions. Pipeline
  outputs must therefore record the exact evidence artifact version for
  reproducibility.
- The raw corpus may be large or have licensing/format constraints. Prefer
  committing small fixture corpora and derived fixture stats for tests, while
  keeping full corpora and large evidence artifacts in configured local paths.

### 9.1 Triage Unit Modes

The canonical output unit remains sentence-level. However, the work item sent to
LLM triage and later yomi repair can be either the sentence or a comma-delimited
span.

Supported modes:

- `sentence`: one sentence-like unit is one triage/repair work item
- `comma_span`: split each sentence-like unit at `、` and use the resulting
  spans as triage/repair work items

The selected mode should be stored in batch state or yomi config as
`yomi_policy.unit_mode`, alongside `yomi_policy.auto_accept_profile`. LLM model
choice is separate and should be stored as `llm_policy`, a task-to-profile map.
Tracks should only provide defaults; the actual per-batch values should be
explicit so reruns stay reproducible.

Example policy:

```json
{
  "unit_mode": "sentence",
  "auto_accept_profile": "strict"
}
```

Example LLM policies:

```json
{
  "alphabetic_entity_judge": "standard",
  "scope_triage": "economy",
  "yomi_reading": "standard",
  "yomi_repair": "standard",
  "yomi_rescue": "strong"
}
```

```json
{
  "alphabetic_entity_judge": "background",
  "scope_triage": "background",
  "yomi_reading": "background",
  "yomi_repair": "background",
  "yomi_rescue": "background"
}
```

Allowed values for now:

- `unit_mode`: `sentence`, `comma_span`
- `auto_accept_profile`: `off`, `strict`, `stable_two_kanji`
- LLM profiles: `smoke`, `economy`, `standard`, `strong`
- LLM execution modes: `sync`, `background`, `batch`

Suggested defaults are `working={unit_mode=sentence,
auto_accept_profile=strict}` and
`dev={unit_mode=sentence,auto_accept_profile=stable_two_kanji}`.
Operators should still be able to run dev with `off` or `strict`, and later run
working with `stable_two_kanji` once that policy is trusted. Track defaults
should also choose LLM profiles per task. Dev can use `economy` for scope gates
and plumbing-oriented tasks, but `yomi_reading` should default to `standard`
even on dev: cheaper mini-model mistakes create false engineering problems and
make prompt/pipeline evaluation noisier. Working likewise uses `economy` for
the scope gate, `standard` for ordinary reading work, and `strong` for rescue.
Track defaults should also choose execution modes per task. `background` should
be the normal default for both
`dev` and `working`, because it submits independent requests, polls until
completion by default, avoids slow sequential calls, and can be resumed by
rerunning `./next` after interruption. `sync` is
best for prompt exploration, smoke tests, tiny runs, and tasks where immediate
failure inspection matters. `batch` is best for very large low-urgency tasks
where latency is acceptable and cost/rate-limit behavior matters. In batch
mode, `./next` should submit any missing remote chunks, poll roughly once per
minute by default, show aggregate API request-count progress when available,
and resume from stored local state after interruption. `sentence` is the safer
unit-mode default while the pipeline is still stabilizing.
`comma_span` should be available, especially for dev experiments, because it
can raise the automatic `OK` rate and reduce downstream review volume. Its cost
is more API calls and extra reconstruction logic.

These defaults should be source-controlled configuration, not hidden Python
constants. A minimal shape is:

```toml
[tracks.working.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "strict"

[tracks.working.llm_policy]
alphabetic_entity_judge = "standard"
scope_triage = "economy"
yomi_reading = "standard"
yomi_repair = "standard"
yomi_rescue = "strong"

[tracks.working.llm_execution_policy]
alphabetic_entity_judge = "background"
scope_triage = "background"
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"

[tracks.dev.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "stable_two_kanji"

[tracks.dev.llm_policy]
alphabetic_entity_judge = "economy"
scope_triage = "economy"
yomi_reading = "standard"
yomi_repair = "economy"
yomi_rescue = "standard"

[tracks.dev.llm_execution_policy]
alphabetic_entity_judge = "background"
scope_triage = "background"
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"
```

The prepare-time precedence should stay simple:

- explicit `./prepare` CLI override
- configured track default

After preparation, every later stage and rerun should use the stored resolved
batch policy rather than re-reading current track defaults.

Avoid adding a wider global override layer unless this narrower config becomes
operationally painful.

In `comma_span` mode, each span work item should keep parent metadata:

- span ID
- parent unit ID
- span sequence number
- span text and rendered yomi
- offsets or rendered-pair ranges when practical
- optional previous/next span and full parent sentence context

Sentence-level aggregation from span labels is:

- `Skip` if any span is `Skip`
- `Review` if no span is `Skip` and at least one span is `Review`
- `OK` only if all spans are `OK`

`Skip` wins over everything else. If any span is `Skip`, the whole parent
sentence is excluded from the normal yomi corpus pipeline. No other span in that
sentence proceeds to repair, even if that span was labeled `Review`; all span
labels are kept only as audit metadata.

If there is no `Skip`, `Review` remains span-local in `comma_span` mode. Later
repair should target only the reviewed span, while attaching broader context
when needed. The context is not limited to the parent sentence; it can include
neighboring spans, previous/next sentence, or source metadata. The final
dataset, however, is always sentence-level, so repaired spans must be merged
back into their parent sentence before export.

The default policy should be:

- one prompt per judgment task
- one parser per judgment task
- one eval set and failure bank per judgment task

The main reason is not only implementation simplicity. It is error isolation.
If one prompt handles exactly one judgment, prompt iteration, regression
analysis, and human review alignment all become much easier.

If the cost later proves too high, the prompts can be merged or restructured,
but only after task-level evals show that the merged version preserves accuracy
and parsing stability.

Prompt optimization should happen as a concentrated pre-production phase, not
inside ordinary batch progression. The project should first build a sufficiently
large gold set for each LLM task, run many prompt candidates against that fixed
set, compare quality and token cost, and freeze the winning prompt before using
it over large corpus batches. Iterating prompts while processing the corpus
would make early batches less reliable and would make improvement cost scale
with corpus size.

Exploratory prompt optimization should use synchronous Responses API calls, not
the Batch API. The point at this stage is fast inspection and revision: run a
candidate, inspect failures and usage, edit the prompt, and rerun. Batch mode is
for production throughput and later regression-scale checks after a prompt
family is already promising.

Historical yomi-triage evals used four conceptual labels: `OK`, `Fix`,
`Ambiguous`, and `Skip`. `Fix` meant the current yomi was wrong but the correct
reading could be determined from the unit/context and repaired. `Ambiguous`
meant the unit was target Japanese, but the correct reading could not be safely
determined from the available context. Those labels remain useful for analyzing
old prompt runs and for future repair-stage evals, but they are not the current
main-path LLM interface.

Experiment note, 2026-05-18: a small 60-row comparison tested direct
`OK/Fix/Ambiguous/Skip` triage against a two-stage `OK/Review/Skip` first pass
plus a downstream `Fix/Ambiguous/OK/Skip` router. With `gpt-5.4-mini`,
end-to-end `3-way -> router` reached 47/60, while direct 4-way reached 36/60;
neither route recovered any of the 6 conceptual `Ambiguous` rows. With
`gpt-5.5`, `3-way -> router` reached 49/60 and direct 4-way reached 45/60. The
`gpt-5.5` router could identify ambiguity when run on gold Review rows
(15/19 overall), but the first 3-way pass still sent all 6 gold `Ambiguous`
rows to `OK`, so they never reached the router in the true end-to-end route.
Conclusion for now: do not depend on triage-time `Ambiguous` detection. The
main path should use binary scope triage plus independent LLM reading
generation instead of direct yomi correctness triage.

Each example should store the original sentence, the exact mechanical yomi
annotation that the model will see, the expected conceptual label, and optional
human notes that are not included in the production prompt. The eval set should
include clear mechanical errors, genuinely ambiguous cases that must remain
reviewable, acceptable variant readings that should not trigger unnecessary
repair, and non-target examples. Optimization priorities are: avoid dangerous
label errors, preserve parse stability, improve accuracy, then shorten prompt
tokens.

The prompt search should explicitly include few-shot variants. A no-example
prompt is worth testing, but the expected winner is likely to be a short prompt
with a small number of boundary examples. Useful prompt families include:

- zero-shot label definitions
- terse one-example-per-label prompts
- boundary-example prompts for `辛い`, `私/ワタクシ`, embedded old-character
  titles, and full non-target quotations
- slightly longer examples with short reasons, only if terse examples fail
- compressed or ungrammatical prompts, if they preserve label behavior

During exploration, `gpt-5.4-mini` is acceptable as the default search model.
For mini, sweep reasoning effort as an experimental parameter, not a fixed
assumption. Compare at least `low` and `medium`, and include higher settings if
the API/model supports them and early results suggest accuracy is effort-bound.
Every run should record model, reasoning effort, prompt path, input/output token
counts, cached-token counts if reported, estimated cost, parse errors, confusion
matrix, and dangerous errors. The final production prompt should still be chosen
from `gpt-5.5` behavior after the mini search narrows the candidate set.

Runtime model selection should use named LLM profiles rather than raw model
names spread across pipeline branches. The batch should store `llm_policy`, and
the runner should resolve each task's profile into the actual task config
overrides used for model, reasoning effort, and any expensive tool settings.

Initial profile meanings:

- `smoke`: transport and instrumentation checks only, usually `gpt-5.4-nano`
- `economy`: cheaper flow validation and prompt/pipeline debugging, usually
  `gpt-5.4-mini`
- `standard`: normal corpus-quality judgment/repair, usually `gpt-5.5`
- `strong`: exceptional last-resort repair/check settings, usually
  `gpt-5.5-pro` or web-search-enabled tasks

Track defaults should choose profiles per LLM task. Per-batch overrides should
allow realistic dev dry runs with `standard`, cheap plumbing checks with
`smoke`, and explicit expensive rescue runs without changing the track itself.
Artifacts should record both the named profile and the resolved model settings
for auditability.

The track/task-to-profile default should come from the same project defaults
config as the other policy defaults. Profile definitions live in the LLM profile
config, but the
prepared batch should remain the reproducibility boundary.

Prompt-cache tuning should be deliberate. OpenAI prompt caching starts at 1024
input tokens, so the reusable static prefix should eventually be tuned to be
just over that threshold before the variable `{text}` and `{rendered}` fields.
Do not guess this boundary from character count. Use local `tiktoken` counts for
quick iteration, then use the Responses `input_tokens` endpoint for exact
GPT-5-family input counts when the prompt is close to the threshold. Token-count
API calls should be treated as operationally negligible compared with real eval
runs, so exact counting is acceptable when it helps stabilize cache behavior.

The target is not to add filler. If extra examples are useful for accuracy, put
them in the static prefix and use them to cross the cache threshold. If an
equally accurate prompt is far below 1024 tokens, keep it short; otherwise, when
the prompt is already near 1k tokens, prefer moving it to a cache-friendly
static prefix around 1050-1150 exact API-counted tokens.

Likely current split:

- `scope_triage`: binary raw-text prompt that returns only `Keep` or `Skip`
- `yomi_reading`: per-target reading prompt that returns one JSON object for
  the marked kanji/Latin target
- `alphabetic_entity_judge`: separate prompt, and also a different unit type
  because it operates on batch-level entity types rather than sentence units
- `yomi_repair`: separate prompt because repair should not be mixed into
  ordinary judgment prompts

## 9.1 Inputs to the LLM

For scope triage, the LLM should receive raw unit text only. It should decide
whether the text is target material, not whether the current yomi annotation is
correct.

For LLM reading generation, the LLM should receive plain source text with
exactly one marked target. Surrounding tokens should not normally include
furigana annotations; they are context only. A no-space furigana rendering may
be stored as debug metadata for comparison, but it is not the default prompt
input for `yomi_reading`. The LLM should output the target reading, not a
sentence-level correctness label and not a full rewritten sentence.

For alphabetic material, the LLM should instead receive unresolved entity types
plus example sentences from the batch.

For yomi-facing prompts, the stored full yomi annotation remains the source of
truth, but the prompt may use derived display forms to reduce visual noise. The
preferred candidate for human/LLM-facing judgment is no-space furigana-style
text: readings are rendered inline and token spaces are removed. For example:

```text
こんな感じ（かんじ）でラミントンもいろいろなタイプが販売（はんばい）されていて、お値段（ねだん）も＄2.50～＄3くらいでお手頃（てごろ）価格（かかく）です。
```

This is only an LLM/human-facing representation. The stored artifact should
still keep the full rendered form, such as `こんな/コンナ` and `で/デ`, so that
auditing, deterministic reconstruction, and N-gram feedback remain lossless.

The no-space display is intentionally more natural than a token-spaced view. It
should reduce model complaints about segmentation artifacts such as `ラミン
トン` when segmentation repair is out of scope. For N-gram decoder training,
debugging, and alignment inspection, keep token-spaced views available:

```text
full:            送っ/オクッ て/テ
compact:         送っ/オクッ て
furigana spaced: 送（おく）っ て
furigana prompt: 送（おく）って
```

Because raw corpus text may already contain both `（...）` and `(...)`, prompt
display must reserve full-width parentheses for yomi annotations. Escape source
parentheses before adding yomi annotations:

- source `（` -> `-LRB-`
- source `）` -> `-RRB-`
- source `(` -> `-lrb-`
- source `)` -> `-rrb-`

Example:

```text
raw:             荷物を送って、（明日）届く。
furigana prompt: 荷物（にもつ）を送（おく）って、-LRB-明日-RRB-届（とど）く。
```

This is a reversible display-layer transform only. Stored source text, full
token yomi, debug output, and N-gram feedback should keep the original
parentheses unless a specific exported view says otherwise.

No-space furigana display should mark rare fused numeric yomi tokens with a
leading `|`. For example, canonical `1人/ヒトリ` displays as
`|1人（ひとり）`, while ordinary separated tokens `1/ 人/ニン` display as
`1人（にん）`. The `|` marker is not source text; it exists only to prevent the
LLM from confusing a digit that belongs to a yomi-bearing token with a normal
digit token handled by the future number-reading module.

Furigana rendering should use the Sudachi-derived annotated-form dictionary
when possible. The dictionary can map `(surface, reading)` pairs such as
`送っ/オクッ` to `送（おく）っ` and `読み仮名/ヨミガナ` to
`読（よ）み仮名（がな）`. If a pair has no unique mapping, the renderer should
fall back conservatively rather than inventing a fragile annotation.

Treat this as a display/projection layer, not as the corpus format. The
canonical accepted corpus remains a token sequence of `surface/reading` pairs.
Furigana text such as `振（ふ）り仮名（がな）` is a review/UI projection derived
from those pairs.

When a document or unit is finally accepted, any non-dictionary or inferred
furigana projection that the reviewer saw and accepted should be persisted as
validated projection metadata. Store at least the `surface`, normalized
`reading`, accepted annotated form, converter method/confidence, source
(`human_accepted_review_ui` or equivalent), dictionary version, and the
batch/unit IDs where it was accepted. Future UI rendering may prefer these
accepted projections before falling back to dictionary/scored alignment, and a
later promotion step may fold repeated accepted projections into the
annotated-form dictionary. This cache is audit/training evidence; it does not
replace the canonical `surface/reading` corpus representation.

LLM-proposed fixes may include neighboring kana or symbol tokens when the
needed correction crosses token boundaries. For example, a proposal may replace
`外出/ガイシュツ て` with `外/ソト 出/デ て`. The application layer should not
blindly string-replace the displayed prompt text. It should align the proposed
`from` span back to the original full token sequence. Apply automatically only
when the match is unique and structurally valid; otherwise keep the item for
human review.

Implementation plan for yomi display modes:

- Add shared rendering utilities for yomi display modes. Do not duplicate this
  logic inside individual prompts or pipeline stages.
- Preserve the existing full `rendered` string in all stored unit artifacts.
  Add derived text only at prompt-build time, or store it as explicit debug
  metadata if caching/debugging requires it.
- Support at least `full`, `compact`, and `furigana_no_space`; a spaced
  furigana debug mode is also useful.
- Allow yomi prompt experiments to omit the separate source `Text:` line. In
  no-space furigana mode, the displayed yomi already contains the source surface
  plus readings, so a yomi-only prompt can test whether the extra raw-text line
  is redundant or distracting.
- Use plain marked source text first for per-target LLM reading generation
  (`yomi_reading`). A mini-model comparison on a 150-item stratified sample did
  not show an accuracy drop relative to no-space furigana context, while input
  tokens fell by roughly 25%.
- Keep the `yomi_reading` prompt extremely short unless later evidence shows a
  regression. A GPT-5.5 test on the same 150-item sample with
  `目が**痛**い。->{"痛":"いた"}\n{marked_text}->` produced no parse errors and
  fewer input tokens than the earlier explanatory prompt. The main failure mode
  was not prompt format, but target extraction/expectation boundaries such as
  `日々`: the queued target must be the whole readable unit (`日々/ひび`), not
  only the first kanji (`日/ひび`).
- Use no-space furigana first for LLM proposal tasks where the model benefits
  from seeing existing yomi annotations, such as `yomi_review_resolution` and
  possibly later `yomi_check`.
- Keep full and spaced views available for full-sentence repair prompts until
  there is a deterministic expansion/alignment layer for applying model output.
- Add a task-level config switch so prompt experiments can compare full,
  compact, and furigana display without changing code.
- Update every prompt that receives a derived yomi display to state the display
  policy. For no-space furigana, the prompt should say that token spaces are
  intentionally omitted and segmentation artifacts are not the target unless
  explicitly requested.
- For local-fix proposal prompts, tell the model to copy the displayed local
  span in `fixes.from` and use the desired full local yomi annotation in
  `fixes.to`. The downstream application layer handles alignment to the full
  stored token sequence.

Prompt merging should be treated as a later optimization question, not the
starting architecture. Only tasks with the same unit, same context needs, same
model policy, and similar failure modes should even be considered as merge
candidates.

## 9.2 Yomi repair

If the LLM does not judge the current yomi to be certainly correct, that unit
should be sent to a second prompt that actually repairs the yomi.

So the yomi path becomes:

1. mechanical yomi
2. mechanical auto-accept for low-risk units
3. LLM triage for the remaining units: `OK`, `Review`, or `Skip`
4. optional review routing for `Review` units: `Fix`, `Ambiguous`, operational
   `OK` for first-stage false positives, or `Skip`
5. LLM repair path for `Fix`
6. bulk audit for `OK` items and focused review for unresolved items

The second routing step is deliberately downstream of the first triage step.
It lets the first prompt stay compact while collecting evidence about whether a
future single wider label set would be safe. In router output, operational
`OK` means "the first triage over-called Review"; in the conceptual gold data,
that item is simply `OK`.

Regex-based repair rules may still be useful here, and this is the area where
regex currently seems more justified than for whitelist/blacklist classification.


## 10. Rule Generation from LLM or Human Decisions

The project also wants a second-order learning loop: use hard decisions to
expand deterministic coverage over time.

## 10.1 Trigger point

Current idea:

- do not generate new rules immediately from ordinary sentence-level LLM
  judgments
- instead, generate candidate rules after human review has confirmed the
  judgment

That should reduce noise.

## 10.2 Non-target rules

If a unit is judged to be non-target material, ask an LLM for one
 conservative reusable trigger that:

- matches this case
- aims for broad coverage
- avoids over-triggering as much as possible

Examples discussed:

- a token such as `言ひ`

This is only a sketch, not a validated rule design.

## 10.3 Latin/alphanumeric entity entries

If a Latin/alphanumeric entity type is judged to be out of scope, add or
propose a reusable entity-level entry.

Current preference is still to keep these as simple entity-level entries rather
than general regexes.

## 10.4 Provisional alphabetic skip review

Alphabetic review should piggyback on final unit review rather than introducing
a separate routine promotion-candidate surface.

Recommended flow:

- keep cached LLM judgments for entity types
- mark units with `out_of_scope` entities as provisional skip
- display provisional skip units greyed out, with `Skip` pre-checked
- show concise reasons such as the triggering entity and cached status
- if the reviewer unchecks `Skip`, store an effective `in_scope` override for
  the triggering entities

This is meant to minimize human effort while keeping all final skip decisions
visible in the same review interface used for yomi quality.

The review unit remains the sentence/unit. For each provisional skip, show:

- entity key
- effective status
- source, such as static blacklist or LLM cache
- optional short rationale and example snippets for debugging

Human actions can stay simple:

- approve
- reject
- defer

This review should remain separate from sentence-level yomi review. Its purpose
is policy confirmation for global automation, not direct corpus annotation.

## 10.4 Yomi repair rules

For yomi correction, regex-like repair rules still seem reasonable because many
useful fixes may be boundary or formatting corrections rather than semantic
reinterpretations.

This area needs experimentation.

Future task: learn exact surface-span defaults from confirmed strong repairs.
When a strong repair changes a local span and the final human confirmation
accepts it, record the whole repaired surface span as a default for future
batches. Examples:

- `一発 -> いっぱつ`
- `池尻中学校 -> いけじり ちゅうがっこう`

These learned defaults should apply to the exact surface span, even if the
original analyzer split it differently. This is intentionally narrower than
general regex promotion: `池尻中学校` should not automatically imply a global
`中学校 -> ちゅうがっこう` rule unless that broader rule is separately approved.
The rule should be auditable and should record the source batch, repaired
rendering, rejected readings, and whether web search was used.

## 10.5 Review transport and UI state

Because the working environment is a Linux cluster accessed over SSH, the human
review UI should not assume that it can write directly back to the cluster.

Current preferred review transport:

- treat this repository's GitHub Pages review UI as the `dev` review surface
  while the workflow and review-pack schemas are still changing
- keep the static dev review UI in this repository, not a separate UI
  repository
- isolate it in its own web-facing directory so the Python pipeline and static
  frontend remain loosely coupled
- host the static review UI on GitHub Pages
- use GitHub as the return mailbox
- for now, prefer one GitHub Issue per review pack
- submit one review result per Issue comment

This is meant to work from both desktop browsers and iPad browsers without
requiring a writable backend on the cluster.

Practical layout direction:

- keep review-UI source under `web/review/`
- keep Python pipeline code under `src/` and `scripts/`
- publish built static assets under `docs/` through this repo's GitHub Pages
  configuration

This keeps hosting simple while still letting the UI evolve together with the
pack format, submission format, and review workflow.

Long-term task: after the review flow has stabilized over multiple batches,
prepare a separate static review project for the `working` track. That project
should be stable and production-oriented, while this repository's Pages site can
remain a fast-changing dev UI. A split would also keep UI publishing commits and
review traffic out of the corpus/pipeline repository. Do not split early: this
repo should continue to own review-pack generation, submission ingestion,
replay semantics, and corpus state. A split is only attractive once the boundary
is a stable versioned contract:

- review-pack JSON schema
- review-submission JSON schema
- publishing/sync mechanism from pipeline artifacts to the UI project
- GitHub Issue/comment ingestion convention back into this repository

Until then, the monorepo layout is intentionally pragmatic.

Near-term dev trial: the next dev batch should use a small batch size, around
10 documents, to test browser-selected work slices and partial returned
submissions. Treat that run as a workflow/UI implementation trial rather than a
quality benchmark. It should verify that local draft state, range/subset export,
Issue submission import, and later-wins replay work before applying the same
pattern to larger batches.

For final yomi review, use a sentence-level review pack in one continuous list,
but make the normal view look like ruby-rendered text rather than pipeline
metadata. Avoid making each document a separate page unless later batch sizes
require it. A flat list works better with the existing range-export model:
default export covers everything, `from here` and `to here` marks narrow the
exported range, and multiple returned files can be merged by stable item IDs.

Each sentence should have only two visible controls:

- `Skip`
- `...` for range marks and other low-frequency actions

Yomi targets should be edited inline. Unresolved targets are highlighted;
safe targets are visually quiet but may still be tappable if candidate readings
exist. Tapping a target cycles through known candidates and then no-ruby, for
example `きんきん -> ちかぢか -> none`. Candidate readings should come from the
recorded evidence: current mechanical/hybrid reading, LLM reading,
corpus-frequency dominant reading, and stable dictionary reading.

Changed spans should be colored differently from unresolved spans. A changed
span is a local override. A no-ruby choice means the reviewer rejects the
current reading and wants strong-model handling for that local area. Consecutive
no-ruby targets in the same sentence should be grouped automatically into one
strong-repair span. Whether to use web search should be decided by the strong
repair prompt/model from the target context, not by a human review checkbox.

Whole-sentence escalation should not be a primary control. So far, real cases
look like local boundary/reading failures that can be handled by canceling a
2-3-token area. If a future example truly needs whole-sentence repair, add it as
an advanced/fallback path rather than the default review action.

Strong-repaired spans go through a separate final confirmation UI published as
the `yomi_strong_repair_review` stage. The current first pass exposes the source
text, rejected readings, LLM proposal, and before/after yomi, with accept/reject
review decisions. Rejected repairs block finalization rather than being silently
included in the corpus. A later pass can add direct structured editing for
confirmed-but-imperfect repairs.

### 10.5.1 Review packs

The cluster should export immutable review-pack files.

Each review pack should include:

- `pack_id`
- `review_stage`
- ordered review items with stable `item_id`
- `seq` numbers for visual order
- proposed action for each item
- evidence summary and short example snippets

### 10.5.2 Local draft persistence

The browser UI should save draft state locally so that a reviewer can leave the
page and return later without losing progress.

Recommended approach:

- key local draft state by `review_stage` and `pack_id`
- store per-item overrides
- store optional range markers
- restore automatically on reload

This local draft state is device-local. It is not the authoritative shared
state.

### 10.5.3 Reviewed range semantics

For review export, the important concept is reviewed coverage, not
explicit clicks on every approved item.

Default UI behavior:

- all items are initially in the export range
- optional `from` and `to` markers can narrow that range
- if neither marker is set, export all items
- if only `from` is set, export that item and everything after it
- if only `to` is set, export everything before and including it
- if both are set, export only the inclusive interval

Visual behavior:

- items inside the current range should look normal
- items outside the current range should remain visible but faded
- `from` and `to` rows should have distinct marker styling
- overridden rows should be highlighted more strongly than simple in-range rows

### 10.5.4 Sparse overrides

Within the reviewed range, the default interpretation is:

- no explicit mark means the reviewer accepts the proposed action

So the submission should store:

- reviewed range
- sparse per-item overrides such as `reject` or `defer`

This is important because the reviewer may visually inspect many items and only
change a few of them.

### 10.5.5 Multiple submissions

One review pack may produce multiple submissions.

This supports:

- interrupted review sessions
- partial review by range
- accidental multi-device work if it ever happens

Merge rule:

- later submissions overwrite earlier submissions for overlapping items
- overlapping range handling is intentionally simple
- responsibility for accidental overwrite stays with the user

For reviewed-range semantics, a later overlapping submission should reset that
range to default acceptance first, then apply its sparse overrides.

That ensures that an omitted override in a later submission really means
"accept proposal" inside that later reviewed range.

## 10.6 Local pipeline state and orchestration

The pipeline should not depend on the operator remembering which script to run
next.

Current preferred direction:

- keep durable local state for each batch
- keep a current-batch pointer per track
- use `working` as the implicit default track and `dev` as an explicit second
  track
- `working` is the strict protected track; `dev` is the relaxed experimental
  track
- provide `./prepare`, `./next`, and `./status` commands
- let `./next` perform one implemented automatic step per call

This is meant to unify:

- ordinary local processing
- OpenAI Batch submission / polling / fetch
- human-review wait points

### 10.6.1 Per-batch and per-track state

Recommended shape:

- one local state file per batch under `data/pipeline/batches/`
- one track pointer file per track under `data/pipeline/tracks/`
- current stage
- known artifacts
- most recent blocking reason
- timestamp

The exact schema can stay minimal at first and grow with the implemented
stages.

### 10.6.2 Command surface

Current intended commands:

- `./prepare 100`
- `./prepare dev 10`
- `./prepare --yomi-unit-mode comma_span --yomi-auto-accept-profile off --llm-profile yomi_reading=smoke dev 10`
- `./next`
- `./next dev`
- `./next --force-stage yomi_generated`
- `./next dev --llm-mode sync`
- `./next dev --llm-mode background`
- `./next dev --llm-mode batch`
- `./status`
- `./status dev`
- `./status dev --stages`
- `./set-stage dev yomi_auto_accepted`
- `./set-stage working yomi_auto_accepted --yes`

The implicit no-argument track should be `working`.

`./status --stages` is a terse read-only mode. It prints only the completed
current stage and the next stage, which is useful when the full structured
status is too noisy. `./next` should not have this mode because it advances the
pipeline and would be easy to mistake for read-only inspection.

Stages that call the LLM should include `llm` in the stage name. Current
LLM-calling stages are:

- `alphabetic_llm_judged`
- `scope_triage_llm_completed`
- `yomi_reading_llm_completed`

Queue-building stages such as `scope_triage_queued` and `yomi_reading_queued`
prepare LLM inputs but do not call the API, so they should not be treated as
LLM-calling stages. `./next --llm-mode <mode>` is a per-invocation override for
the stage being run. It should be accepted only when that stage calls the LLM;
on deterministic stages it should fail explicitly rather than being silently
ignored. The saved track policy remains unchanged.

`./set-stage <track> <stage>` is an explicit pointer-only rewind tool. It should
change only the saved `current_stage` for the current batch and must not delete
or rewrite artifacts. It refuses forward moves; use `./next` to advance. On the
protected `working` track it requires interactive confirmation or `--yes`.

### 10.6.3 Current one-step progression

`./next` should run one legal automatic step and then stop.

Example behavior:

- if a batch is only prepared, `./next` should build the alphabetic artifacts
- the next `./next` should build the unresolved alphabetic report
- the next `./next` should run or resume alphabetic entity LLM judgment for
  unresolved entity types and update the global judgment cache
- the next `./next` should project cached `out_of_scope` entity status to
  provisional skip reasons before general scope/yomi processing
- the next `./next` should queue raw-text scope triage
- the next `./next` should run or resume scope triage and exclude `Skip` units
- the next `./next` should build the mechanical yomi JSONL
- the next `./next` should add the yomi auto-accept artifact
- the next `./next` should build a yomi-reading queue from unresolved targets
- the next `./next` should run or resume the configured yomi-reading LLM task
  and write comparison metadata
- after that, later repair/review stages should consume targets or units with
  LLM/mechanical disagreement, parse failure, or unresolved ambiguity; `Skip`
  units are excluded and agreement cases enter bulk audit or later
  auto-acceptance experiments
- `./next --force-stage <stage>` should rerun the current completed stage
- on `working`, confirmation should happen only when that rerun would actually
  overwrite existing artifacts

The intended UX is:

- run one command
- let it do one clear thing
- inspect `./status` when needed

### 10.6.4 Resumable LLM Jobs

LLM calls should be orchestrated through a generic resumable job layer. This is
needed because the project will use LLMs in multiple stages: alphabetic entity
judgment, scope triage, yomi reading generation, ordinary yomi repair, and
rescue repair. Each stage should not invent its own
sync/background/batch lifecycle.

The pipeline stage should own the domain transition, while the LLM job owns
operational execution:

- input rows and request JSONL
- result JSONL
- mode: `sync`, `background`, or `batch`
- task name and resolved LLM profile/model settings
- total item count, completed item count, failed item count
- remote response IDs and remote status for background mode
- remote batch IDs and remote status for batch mode
- timestamps, attempts, and error information

Sync mode behavior:

- process items sequentially at first; small concurrency can be added later
- append each completed result to the job result JSONL immediately
- on resume, skip item IDs already present in the result JSONL
- show progress from completed item count over total item count
- allow the operator to interrupt safely and continue with the same `./next`

Background mode behavior:

- submit one Responses API background request per item, preferably submitting
  all missing items before entering the polling loop
- store each `item_id -> response_id` mapping in the LLM job directory
- on resume, do not resubmit item IDs that already have either a response ID or
  a parsed result
- poll stored response IDs until completion or interruption by default, and
  append completed results immediately
- report progress from parsed completed result count over total item count
- treat incomplete background responses as an active resumable job, not as a
  separate domain pipeline stage
- do not postpone polling indefinitely; OpenAI documents background response
  storage as roughly a 10-minute polling window

Batch mode behavior:

- submit remaining items as one or more OpenAI Batch jobs
- store remote job IDs, request files, and manifest in the LLM job directory
- on resume, poll the stored remote job
- if the remote job is still running, report status and OpenAI
  `request_counts` (`completed`, `failed`, `total`) and do not advance the
  domain stage
- if complete, download and normalize output, then apply results
- if partially failed, resubmit only missing or failed item IDs when possible

The batch output file is available only after completion. Progress before that
comes from the Batch object's `request_counts`, while final result mapping must
come from the downloaded output file and each request's `custom_id`.

Batch API operational limits that should shape implementation:

- one OpenAI batch can include up to 50,000 requests
- one batch input file can be up to 200 MB
- pending batch prompt tokens count against a per-model enqueued-token limit
- batch creation is rate-limited, documented as up to 2,000 batches per hour
- a batch can expire; completed request outputs remain available, and
  unfinished requests are returned as errors

Current implementation: batch mode presents one logical resumable LLM job to the
domain pipeline, while request-count chunking may split it into multiple remote
OpenAI batch jobs.

The task-config knob for request-count chunking is
`batch_max_requests_per_batch`. The production default stays high enough
to preserve single-batch behavior for ordinary jobs, while tests can set a
small value to force multiple remote OpenAI batches.

Suggested storage:

```text
data/llm/jobs/<job_id>/
  manifest.json
  input.jsonl
  requests.jsonl
  results.jsonl
  usage_summary.json
```

Suggested manifest fields:

```json
{
  "job_id": "dev_batch_0003_yomi_reading",
  "track_name": "dev",
  "batch_name": "dev_batch_0003",
  "task_name": "yomi_reading",
  "mode": "sync",
  "status": "running",
  "total_items": 559,
  "completed_items": 184,
  "failed_items": 0,
  "profile": "economy",
  "execution_mode": "background",
  "remote_response_ids_path": "responses.jsonl",
  "remote_batch_id": "batch_...",
  "remote_status": "in_progress",
  "model": "gpt-5.4-mini"
}
```

Domain pipeline stages should not need many operational substages such as
`queued`, `submitted`, or `batch_completed`. The normal pattern should be:

- if no LLM job exists for the domain stage, create it and start execution
- if an incomplete job exists, resume or poll it
- if the job is complete, apply results and advance the domain stage

Human review remains a first-class wait state, but OpenAI sync/background/batch
execution should usually be represented as an attached resumable LLM job rather
than as many stage names.

### 10.6.5 CLI Output and Logs

`PipelineWorkspace.advance()` may continue returning a full structured summary
for tests and internal callers. The `./next` command should not print that full
JSON by default. It is too long for ordinary operation and hides the important
state.

Default `./next` output should be concise and human-readable:

```text
Track: dev
Batch: dev_batch_0003
Stage: yomi_reading
LLM job: dev_batch_0003_yomi_reading
Progress: 184/559 completed
Status: running
Next: rerun ./next dev to continue
```

On completion:

```text
Track: dev
Batch: dev_batch_0003
Stage: yomi_reading_llm_completed
Completed: 559/559
Output: data/units/dev_batch_0003/units.yomi.llm_readings.jsonl
Next: yomi_repair
```

Full structured summaries should be written to local logs, for example:

```text
data/pipeline/logs/YYYYMMDD/<timestamp>_<track>_<batch>_<stage>.json
```

`./next --json` should preserve the current machine-readable behavior by
printing the full structured summary to stdout. `./status --json` should also be
available if status becomes human-readable by default later.


## 11. Human Review: Pass 1

The first human review UI should be sentence-based. It can use the same simple
checkbox interaction for both `OK` and non-`OK` queues, but the queue context
changes how the reviewer approaches the work.

Recommended queues:

- `bulk_ok_audit`: automatic `OK` items; scan many sentences quickly and mark
  only sentences with a problem
- `focused_review`: items that still need attention after repair; inspect each
  sentence carefully

The core checkbox can simply mean "problem found" or "needs attention":

- in `bulk_ok_audit`, unchecked means accepted by default after scanning
- in `focused_review`, unchecked means accepted after focused inspection
- checked means keep unresolved or send to correction

This lets LLM `OK` reduce human workload without pretending that LLM `OK` is
infallible. The review pack should still record the queue type and the source
of each automatic `OK`, so later analysis can estimate false-OK rates by
source.

### 11.1 Long-term large-batch review queues

Long-term target: keep pipeline batches large, for example 100 documents, but
make human review operate on smaller browser-selected slices. The pipeline batch
is durable state; the UI-selected range or checkbox subset is only a work unit
for one submission.

The target queue lifecycle is:

1. Prepare one large batch.
2. Put items needing human final review into a `final_review_pending` list.
3. Let the UI show that list, let the reviewer choose a range or checked
   subset, and persist that in-progress work in browser storage.
4. The reviewer exports/submits JSON through GitHub Issues/comments.
5. A periodic importer polls Issues/comments, applies matching submissions
   idempotently, and updates local queue state.
6. Completed final-review items are greyed out or removed from the first list.
7. Items needing strong repair move into `strong_repair_pending`.
8. Strong-repair review works the same way; completed items disappear from the
   second list.
9. When both pending lists are empty, the orchestrator finalizes the batch and
   prepares the next large batch.

Operational requirements:

- submission payloads must carry stable item IDs, queue/stage IDs, pack IDs,
  and reviewed range/subset metadata
- browser draft state should be keyed by `pack_id`, `queue_id`, and selected
  item IDs or range
- completed/greyed-out state should be rendered from imported local queue
  state, not from browser-only local storage
- importer replay must be idempotent and tolerate many open Issues/comments
- overlapping submissions should continue to use the existing later-wins replay
  rule
- strong repair should be derived from final-review submissions, not from a
  separately prepared manual batch

This is not the current implementation target. It should wait until the
final-review and strong-repair schemas are stable enough that a larger queue
manager will not churn every batch.

Display:

- the current best-effort yomi-annotated sentence
- three checkboxes

The three checkboxes are:

1. non-target status
2. yomi fully correct

Important intended behavior:

- the first checkbox may already be prefilled by mechanical or LLM output
- the yomi checkbox should start unchecked
- the yomi-annotated sentence already contains the original sentence content, so
  a separate raw-text field is unnecessary in this UI
- a sentence that is already known to have incorrect yomi should not be shown as
  a knowingly bad candidate; instead the pipeline should first attempt repair

Minor alphabetic review should be visible in this sentence-level UI only as
pre-checked `Skip` suggestions with reasons. The reviewer should not have to
visit a separate entity-level UI for routine cases.


## 12. Human Review: Pass 2

Units that still fail the yomi check after the best-effort pipeline should enter
a second, more expensive path.

## 12.1 Expensive repair

For these units:

- use `gpt-5.5` as the default rescue model
- allow expensive tooling such as web search and stronger reasoning if needed
- generate a new best-effort yomi

Only if that still fails after human review should the pipeline consider a
`gpt-5.5-pro` escalation. That should be treated as a last resort for a very
small tail, not part of the normal path.

## 12.2 Final review UI

The final review UI should look primarily like ruby-annotated text, not like a
table of records. Sentence-level metadata should be hidden except for compact
controls:

- skip checkbox
- range-marker menu
- tappable ruby spans

For each highlighted yomi span, the reviewer should choose one of three states:

- accept the current segmentation and reading
- keep segmentation but edit only readings
- edit segmentation and readings

When editing segmentation, the UI should let the reviewer toggle split points
between characters. For example, `池尻中学校` can become `池尻 + 中学校` and
the reading fields should follow that split. If the reviewer has already typed
readings and then changes a split in a way that discards fields, the UI should
warn before rebuilding the fields.

Direct span fixes are applied to the rendered yomi immediately when they can be
matched uniquely in the current rendered string. Ruby-cancelled spans are not
treated as direct fixes; they are queued for strong LLM repair as local target
groups.

## 12.3 Strong repair and finalization

Strong repair should operate on local target groups produced by human ruby
cancellation, not on whole sentences by default. The model receives the source
text, current yomi, rejected span, and rejected readings, and returns replacement
surface/reading items. The application step validates that replacement surfaces
concatenate to the rejected span and that readings are valid kana.

After strong repairs are applied, the reviewed units can be finalized. Final
postprocessing should continue to validate:

- rendered-yomi format
- surface preservation
- reading normalization


## 13. Batch Execution Model

The pipeline should run in batches of documents.

Current initial preference:

- start with batches of about 100 documents
- later scale upward once the process is stable

Within a batch:

- import documents
- derive units
- run mechanical analysis for every unit
- build the batch-level alphabetic entity inventory
- run entity-level LLM judgment only for unresolved alphabetic entity types
- run sentence-level LLM classification by default for now
- run LLM yomi repair where needed
- build human review queues

### 13.1 Current post-review stages

The current yomi pipeline now continues after `final_review_prepared`.

Implemented stages:

- `final_review_applied`
- `yomi_strong_repair_queued`
- `yomi_finalized`

`final_review_applied` reads local review submission JSON files from:

```text
data/review_submissions/yomi_final/
```

The submission format is the JSON exported by the GitHub Pages review UI:

- `review_stage` must be `yomi_final_review`
- `pack_id` must match the generated review pack
- `reviewed_ranges` define which sentence items were actually reviewed
- sparse `overrides` carry `skip` and target-level reading choices
- target-level `choice_source: "none"` means the previous reading was rejected
  and should enter the strong-repair queue; consecutive no-ruby targets are
  grouped into one strong-repair span
- `skip` dominates operational output: target choices on a skipped sentence are
  preserved as audit data, but they are not applied to rendered yomi and do not
  trigger strong repair

When `./next` reaches `final_review_applied`, it first scans open GitHub Issues
for yomi final-review submissions and stores matching payloads in that local
store. The apply step then replays the local store. If GitHub import fails or
finds no matching submission, the stage falls back to the existing local-store
check and blocks as a human review gate.

For debugging or manual recovery, a single Issue can still be imported
explicitly:

```bash
python scripts/import_yomi_final_review_issue.py --issue-number 1
```

`yomi_strong_repair_queued` should focus on canceled target groups. Target-level
`choice_source == "none"` choices are collected in token order, and consecutive
canceled targets in the same sentence become one `repair_scope:
"target_group"` queue entry. These groups need focused strong repair rather
than serving as confirmed local constraints. Skipped sentences are excluded
from this queue. The strong-repair prompt should decide whether web
search is needed from the target context and rejected readings, and should
record whether search was actually used.

For a canceled ruby, the queue should preserve the rejected reading explicitly
as `rejected_readings`. This is useful for strong/web repair cases: for example,
if human review rejects `史輝/ふみてる` in the publisher-name context
`史輝出版`, the strong model should receive both the canceled span and the fact
that `ふみてる` is known wrong. The fixture
`data/evals/yomi_strong_repair/regression_v1.jsonl` records this kind of
target-level web-repair case.

The same open-Issue scan that `./next` runs can also be invoked manually:

```bash
python scripts/import_yomi_final_review_inbox.py
```

The older alphabetic Issue importer has been retired. Alphabetic entity
judgments are now treated as provisional pipeline evidence, while final
skip/keep corrections are handled in the yomi final-review UI.

Merge rule:

- replay submissions by `generated_at_epoch`, `submission_id`, and source path
- a reviewed range defaults every item in that range to accepted
- sparse overrides apply on top of that default
- later overlapping submissions overwrite earlier ones

If no matching submission exists, `final_review_applied` blocks with a human
review gate. This is intentional: even on `dev`, final yomi review should not be
silently skipped.

`yomi_strong_repair_queued` creates the local strong-repair input queue:

```text
data/units/<batch>/yomi_strong_repair_queue.jsonl
data/units/<batch>/yomi_strong_repair_queue_summary.json
```

Queue entries are generated for:

- consecutive target-level `choice_source: "none"` overrides as `repair_scope:
  "target_group"` and `repair_order: 1`

Older sentence-level escalation should be treated as legacy/fallback plumbing,
not the preferred review path. The current design expects reviewers to cancel
the local problematic targets instead.

`yomi_strong_repair_llm_completed` runs `config/llm/yomi_repair.toml` on that
queue, using the track's `yomi_repair` profile and execution mode. The default
dev and working profile is `standard` because local target repair needs the
stronger `gpt-5.5` behavior seen in examples such as `真光元`; cheaper mini
models are too noisy for this stage.

The stage writes:

```text
data/units/<batch>/yomi_strong_repair_results.jsonl
data/units/<batch>/yomi_strong_repair_usage_summary.json
data/units/<batch>/units.yomi.strong_repaired.jsonl
data/units/<batch>/yomi_strong_repair_apply_summary.json
```

Application is intentionally conservative. For now, target-group repairs are
applied only when the parsed JSON array is valid, every reading is kana-only,
and the concatenated returned surfaces exactly match the rejected span. Missing,
parse-failed, sentence-scope, or surface-mismatched rows block the stage rather
than being silently accepted.

`yomi_finalized` reads `units.yomi.strong_repaired.jsonl` when it exists. If the
strong repair queue is non-empty, finalization requires a later human
confirmation step even when every LLM repair was mechanically applied. This is
intentional: strong repair results are candidates, not final truth. If no
successful apply summary exists, or if confirmation is missing, finalization
blocks. The confirmation is stored by applying submissions for the
`yomi_strong_repair_review` pack; when all repair items are reviewed and none is
rejected, `yomi_strong_repair_apply_summary.json` is marked `confirmed`.
Otherwise finalization remains blocked. When confirmed, finalization writes:

```text
data/units/<batch>/units.yomi.final.jsonl
data/units/<batch>/yomi_finalize_summary.json
data/units/<batch>/manual_yomi_rewrites.jsonl
data/units/<batch>/supplemental_furigana.tsv
data/units/<batch>/yomi_finalization_harvest_summary.json
```

The `yomi_strong_repair_review` UI may also submit `manual_segments` for a
repair item. This is the first manual correction path for local strong repairs:
the rejected span is displayed inline, boundaries between characters can be
toggled, and the UI emits segment-level `surface/reading` pairs. During
confirmation these manual segments override the LLM repair only if their
surfaces concatenate exactly to the rejected span and all readings are valid
kana. Invalid manual segments keep confirmation incomplete rather than being
silently applied.

For each rejected strong-repair span, the review pack should include
dictionary-backed reading candidates for substrings found by prefix-style
lookup from every character position. For example, `池尻中学校` should carry
candidates for substrings such as `中学校` and `学校`, and ambiguous entries
such as `池尻` may carry multiple readings. The UI currently uses the first
candidate as the default reading when a boundary edit creates that segment, but
the full candidate list is preserved in JSON for later richer controls.

Skipped units are excluded from `units.yomi.final.jsonl`. Reviewed, non-skipped
units are retained.

At batch finalization, the pipeline also harvests two conservative reusable
artifacts:

- exact strong-repair rewrite rules, appended de-duplicated to
  `data/lexicon/manual_yomi_rewrites.jsonl`
- supplemental furigana allocations, appended de-duplicated to
  `data/lexicon/supplemental_furigana.tsv`

Manual yomi rewrites affect future tokenization/readings only by exact surface
span match at first. For example, if a reviewed repair establishes
`池尻中学校 -> 池尻/イケジリ 中学校/チュウガッコウ`, a later exact
`池尻中学校` occurrence can use that as a default. Do not generalize these
rules into regexes until there is explicit evidence.

Supplemental furigana is display-only. It records accepted `surface/reading` to
annotated-form mappings not already present as exact Sudachi-derived dictionary
entries, so future ruby rendering can load it alongside
`sudachi_20251022.tsv`. It must not change the underlying corpus format, which
remains `surface/reading`.

Simple target reading choices are applied directly to exact rendered yomi
tokens when the reviewed target covers the whole token. Harder cases, such as
no-ruby targets or future token-boundary corrections, should go through the
strong repair queue.

### 13.2 Decoder model refresh boundary

Updating the `yomi-decoder` language model should be an explicit maintenance
workflow, not a normal per-batch `./next` stage.

Reason:

- a decoder model refresh changes future batch behavior
- several finalized batches may be aggregated before rebuilding
- small `dev` batches are often experimental and should not automatically
  affect the decoder
- comparisons are easier when model refreshes are deliberate experiment
  boundaries

Boundary:

- `yomi-corpus` owns reviewed-corpus export and model-version selection
- `yomi-decoder` owns model building and decoder internals

Track policy:

- `dev` and `working` should have separate reviewed-corpus exports
- `dev` model refreshes may use dev-finalized batches, but should not
  automatically affect `working`
- `working` model refreshes should use only accepted working material, or
  explicitly promoted dev material
- a batch should pin the decoder model it used at batch creation time
- model changes should affect only later batches, never a batch already in
  progress

Recommended artifact layout:

```text
data/decoder_corpora/dev/<batch>.txt
data/decoder_corpora/working/<batch>.txt
data/decoder_models/dev/<model-id>/
data/decoder_models/working/<model-id>/
```

Implemented low-level interface:

```bash
python /path/yomi-corpus/scripts/export_decoder_corpus.py \
  --batch reviewed_batch_name

python /path/yomi-decoder/scripts/build_model.py \
  --base-corpus /path/core_SUW_yomi_final.txt \
  --extra-corpus /path/yomi-corpus/data/exports/decoder_corpus/reviewed_batch_name.txt \
  --output-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD
```

Then this project should call the decoder with the chosen model directory:

```bash
python /path/yomi-decoder/scripts/decode.py \
  --model-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD \
  ...
```

Operator-facing refresh command:

```bash
python scripts/refresh_decoder_model.py --track dev
python scripts/refresh_decoder_model.py --track working
```

That wrapper:

- find finalized batches for the selected track
- export missing decoder corpora
- build a new model under `data/decoder_models/<track>/...`
- update the track state with the latest model path

When `prepare` or `./next` starts a new batch, it copies the track's latest
decoder model path into the batch state. The batch then uses that pinned path
for all Sudachi/decoder hybrid generation.

Each batch manifest should eventually record:

- decoder executable/version
- base corpus path or version
- extra reviewed-corpus inputs
- model output directory or model ID
- build timestamp
- relevant build parameters

For now, the pipeline should only produce stable finalized artifacts. A separate
export/update command can later consume `units.yomi.final.jsonl` files when the
operator decides that enough reviewed material has accumulated.

### 13.3 Auto advancement

`./next --auto` repeatedly advances the current batch until one of these happens:

- a human gate blocks progress
- a stage is still incomplete
- confirmation is required
- the final stage is reached
- an error/blocking reason appears

Plain `./next` remains single-step and should be preferred while debugging a
specific stage.


## 14. Unclear or Open Points

The following points are not settled and should be treated as explicit open
questions:

1. When should non-target feature extraction become a real
   mechanical classifier?
   The current idea is to rely partly on Sudachi and N-gram analysis quality,
   but no reviewed-data-backed scoring rule exists yet.

2. When should sentence-level `certain` gating be turned on for non-target and
   yomi tasks?
   The branching logic is clear, but it should remain disabled until there is
   enough reviewed data.

3. How much context should be shown in entity-level alphabetic review?
   The preferred unit is now the entity type, but some cases such as `OK` or
   `Lab` may still need representative sentence examples.

4. What exact format should represent unit-local nested analysis?
   The current example is only a draft.

5. How should the mechanical yomi confidence score eventually be computed from
   Sudachi and `yomi-decoder` outputs?

6. At what point should reusable rules be harvested automatically from reviewed
   cases?
   Current preference is after human review, not before.

7. How should the second-pass expensive LLM workflow be constrained so that cost
   does not grow too quickly?


## 15. Immediate Next Steps

The basic small-batch loop exists. The next work should be done in this order:

1. regenerate the active `dev` final-review pack whenever candidate-generation
   logic changes
2. complete the current dev final review, import the GitHub-Issue submission,
   and apply it
3. finalize the batch if no strong-repair targets remain
4. implement real strong repair for canceled ruby target groups, using optional
   sentence-level web search when the reviewer requests it
5. add conservative learned default repairs from accepted human/strong-LLM fixes
6. propagate human skip/unskip decisions back into alphabetic token decisions
   once the final-review UI exposes that override cleanly
7. export finalized yomi into a supplemental decoder corpus and rebuild the
   track-local decoder model
8. run several more small dev batches before changing `working` defaults

The immediate blocker is no longer theory or initial infrastructure; it is
closing the review-to-finalization loop on concrete dev batches.
