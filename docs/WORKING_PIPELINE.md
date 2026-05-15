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
- send only unresolved entity types to the LLM or human review

Then project the entity-type decisions back onto units.

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
- avoid regex unless there is a clear payoff

This remains a working decision, not a final one.

## 7.4 LLM and human judgment unit

The preferred unit for LLM and human judgment is now the alphabetic entity, not
the whole sentence.

Recommended flow:

- extract alphabetic entities mechanically
- remove already known whitelist/blacklist entries
- ask the LLM to classify unresolved entity types
- ask humans to review unresolved entity types, ideally with example sentences

Sentence context is still useful, but mainly as supporting evidence for the
entity-level decision.

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

For yomi, the first sentence-level LLM pass should be a compact triage task.
The model receives the original sentence and the current yomi-annotated
sentence, then returns exactly one token:

- `OK`: the current yomi annotation is correct
- `Review`: the unit is target Japanese, but should not be accepted
  automatically because the yomi has an error, malformed output, or unresolved
  local ambiguity
- `Skip`: the unit is non-target text and should not be yomi-repaired, for
  example because it is foreign-language text, classical Japanese, kanbun, or
  garbled text

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
target or non-target. The
exceptions are compact embedded material such as proverbs, fixed expressions,
short titles, proper names, journal/book names, and bibliographic labels; those
do not make the unit `Skip` by themselves.

`Review` is also the right label when the yomi is not safely acceptable because
of unresolved local ambiguity, even if the attached reading is one possible
reading. For example, an isolated sentence such as `辛いね` should remain in
the review path if the available unit does not decide between readings such as
`カライ` and `ツライ`. The triage label is operational: it answers
whether the unit can be accepted as final now, not whether the current reading
is linguistically imaginable.

By contrast, inherently unresolved but acceptable reading variation should not
be forced into `Review`. Examples such as `日本/ニッポン` or `私/ワタクシ` can be
slightly marked or less frequent, but they are not errors if context cannot
reliably force another reading. These cases should normally be `OK`; if a
variant repeatedly distracts the LLM, prefer a deterministic normalization or
post-hybrid repair rule before triage rather than teaching triage to debate
acceptable stylistic variants.

This is deliberately output-cheap. Reasons belong in debug/eval mode, not in
the default production triage prompt.

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

Example LLM policy:

```json
{
  "alphabetic_entity_judge": "standard",
  "non_target_judge": "standard",
  "yomi_triage": "standard",
  "yomi_repair": "standard",
  "yomi_rescue": "strong"
}
```

Allowed values for now:

- `unit_mode`: `sentence`, `comma_span`
- `auto_accept_profile`: `off`, `strict`, `stable_two_kanji`
- LLM profiles: `smoke`, `economy`, `standard`, `strong`

Suggested defaults are `working={unit_mode=sentence,
auto_accept_profile=strict}` and
`dev={unit_mode=sentence,auto_accept_profile=stable_two_kanji}`.
Operators should still be able to run dev with `off` or `strict`, and later run
working with `stable_two_kanji` once that policy is trusted. Track defaults
should also choose LLM profiles per task, so dev can use `economy` for flow
checks while working uses `standard` for ordinary corpus work and `strong` for
rescue. `sentence` is the safer unit-mode default while the pipeline is still
stabilizing. `comma_span` should be available, especially for dev experiments,
because it can raise the automatic `OK` rate and reduce downstream review
volume. Its cost is more API calls and extra reconstruction logic.

These defaults should be source-controlled configuration, not hidden Python
constants. A minimal shape is:

```toml
[tracks.working.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "strict"

[tracks.working.llm_policy]
alphabetic_entity_judge = "standard"
non_target_judge = "standard"
yomi_triage = "standard"
yomi_repair = "standard"
yomi_rescue = "strong"

[tracks.dev.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "stable_two_kanji"

[tracks.dev.llm_policy]
alphabetic_entity_judge = "economy"
non_target_judge = "economy"
yomi_triage = "economy"
yomi_repair = "economy"
yomi_rescue = "standard"
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

For `yomi_triage`, the initial gold set should include balanced `OK`, `Review`,
and `Skip` cases, with hard examples deliberately overrepresented. Each example
should store the original sentence, the exact mechanical yomi annotation that
the model will see, the expected label, and optional human notes that are not
included in the production prompt. The eval set should include both clear
mechanical errors and ambiguity cases that must remain reviewable, while also
including acceptable variant readings that should not trigger unnecessary
repair. Optimization priorities are: avoid dangerous label errors, preserve
parse stability, improve accuracy, then shorten prompt tokens.

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

- `non_target_judge`: separate prompt when a standalone non-target classifier
  is needed
- `yomi_triage`: first yomi LLM pass; returns only `OK`, `Review`, or `Skip`
- `alphabetic_entity_judge`: separate prompt, and also a different unit type
  because it operates on batch-level entity types rather than sentence units
- `yomi_repair`: separate prompt because repair should not be mixed into
  ordinary judgment prompts

## 9.1 Inputs to the LLM

For yomi triage, the LLM should receive only units not accepted by mechanical
auto-acceptance.

For each relevant unit, it should jointly judge:

- whether the unit is target Japanese
- whether the current yomi annotation is correct

At this stage, the LLM is still doing classification, not repair.

For alphabetic material, the LLM should instead receive unresolved entity types
plus example sentences from the batch.

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
4. LLM repair/review path only for `Review`
5. human review

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

## 10.4 Promotion candidate review

Whitelist and blacklist promotion should not happen automatically from one LLM
answer.

Recommended flow:

- accumulate evidence for each entity type across batches
- let deterministic rules or the LLM generate promotion candidates
- use a temporary threshold of `3` consistent observations to surface either a
  whitelist or blacklist candidate
- show only those promotion candidates to a human
- promote to global whitelist or blacklist only after human approval

This is meant to minimize human effort while still keeping globally reused list
entries trustworthy.

This `3`-observation rule is only a temporary operating rule. It can be made
stricter later if the evidence quality turns out to be noisier than expected.

The review unit here is the entity type, not the sentence.

For each promotion candidate, show:

- entity key
- proposed direction: whitelist or blacklist
- evidence summary such as observation counts and recent judgments
- a few short example snippets
- optional short rationale

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

## 10.5 Review transport and UI state

Because the working environment is a Linux cluster accessed over SSH, the human
review UI should not assume that it can write directly back to the cluster.

Current preferred review transport:

- keep the static review UI in this repository, not a separate UI repository
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

For promotion-candidate review, the important concept is reviewed coverage, not
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
- `./prepare --yomi-unit-mode comma_span --yomi-auto-accept-profile off --llm-profile yomi_triage=smoke dev 10`
- `./next`
- `./next dev`
- `./next --force-stage yomi_generated`
- `./status`
- `./status dev`

The implicit no-argument track should be `working`.

### 10.6.3 Current one-step progression

`./next` should run one legal automatic step and then stop.

Example behavior:

- if a batch is only prepared, `./next` should build the alphabetic artifacts
- the next `./next` should build the unresolved alphabetic report
- the next `./next` should build the mechanical yomi JSONL
- the next `./next` should add the yomi auto-accept artifact
- the next `./next` should build `yomi_triage_input.jsonl` from units not
  mechanically auto-accepted
- the next `./next` should run the configured yomi LLM triage task and write
  both raw LLM results and `units.yomi.triaged.jsonl`
- after that, later repair/review stages should consume only units labeled
  `Review`; units labeled `Skip` are excluded and units labeled `OK` are
  accepted subject to later audit sampling
- `./next --force-stage <stage>` should rerun the current completed stage
- on `working`, confirmation should happen only when that rerun would actually
  overwrite existing artifacts

The intended UX is:

- run one command
- let it do one clear thing
- inspect `./status` when needed

### 10.6.4 Explicit wait states

OpenAI Batch and human review should be treated as first-class wait states.

Examples:

- `waiting_for_openai_batch`
- `waiting_for_yomi_triage_results` when a batch-mode yomi triage job has been
  submitted but not fetched
- `waiting_for_promotion_candidate_review`
- `waiting_for_sentence_review_pass1`

If the blocking condition has not been satisfied yet, `advance` should report
that and stop cleanly instead of failing or guessing.


## 11. Human Review: Pass 1

The first human review UI should be sentence-based.

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

Minor alphabetic review should not be mixed into this sentence-level UI. It
should live in an entity-level review flow with example sentences.


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

## 12.2 Second review UI

The second review UI should show:

- the yomi-annotated sentence
- a free-text comment box

Reviewer behavior:

- leave the comment blank if the yomi now looks correct
- if it is still wrong, describe the mistake in natural language

## 12.3 Final correction loop

Then feed that human free-text feedback to a regular LLM and let it revise the
output again.

This is not actually the final step.

## 12.4 Final editable review pass

After the LLM revises the yomi based on the human comment, the result should go
back to a human reviewer one more time.

The final review UI should show:

- a fully editable text box containing the entire yomi-annotated sentence

Reviewer behavior:

- directly edit the full yomi-annotated sentence into the correct final form

After the human edit, the pipeline should run postprocessing such as:

- format validation
- normalization
- checks that the edited output still matches the required annotation format

This is the actual final correction step for the hard yomi cases.


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

The most useful next implementation steps appear to be:

1. finalize the minimal unit schema
2. implement document-to-unit segmentation with stable offsets
3. implement a nested unit-local analysis structure
4. run Sudachi and `yomi-decoder` on a small batch of real data
5. inspect real failure examples for:
   - non-target status
   - unresolved alphabetic entity types
   - yomi errors
6. only then define the first reviewed-data-backed certainty rules

The project is not blocked on perfect theory. It is mainly blocked on getting a
small real-data loop running and looking at concrete examples.
