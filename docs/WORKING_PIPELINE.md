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

- sentence-like units for classical/non-target Japanese and yomi correctness
- batch-level alphabetic entities for the minor-alphabetic problem

For sentence-like units, the main questions are:

1. Is this classical or non-target Japanese material?
   Examples:
   - old kana
   - old orthography
   - classical Japanese
   - kanbun

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
      "classical_japanese": {
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
      "classical_japanese": null,
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

### 5.1 Classical/non-target Japanese judgment

- raw signals that may later help predict whether the unit looks like
  classical Japanese, kanbun, or other non-target material
- no mechanical `value` or `certain` decision yet

Those signals will likely include:

- Sudachi behavior
  The current mechanical baseline should use Sudachi B-mode rather than the
  default C-mode, so compounds are first split into middle units before hybrid
  refinement.
- N-gram decoder behavior
- script and orthography heuristics

The exact rules are intentionally deferred until reviewed data exists.

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

- do not assign `certain=true` mechanically for classical/non-target judgment
- do not assign `certain=true` mechanically for yomi safety
- collect raw features now and define certainty rules only after reviewed data
  accumulates

So the current effective branch is:

- classical/non-target judgment
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

For now, the sentence-level judgments should be handled with separate prompts:

1. classical/non-target Japanese or not
2. current yomi correct or not

This is intentionally simple, even if it may not be cost-optimal.

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

Likely current split:

- `classical_japanese_judge`: separate prompt
- `yomi_check`: separate prompt
- `alphabetic_entity_judge`: separate prompt, and also a different unit type
  because it operates on batch-level entity types rather than sentence units
- `yomi_repair`: separate prompt because repair should not be mixed into
  ordinary judgment prompts

## 9.1 Inputs to the LLM

For now, the LLM should receive sentence-level tasks without waiting for a
mechanical certainty decision.

For each relevant unit, it should judge:

- `classical_japanese`
- `yomi_is_correct`

At this stage, the LLM is still doing classification, not necessarily repair.

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
2. LLM binary judgment: correct or not
3. if not confidently correct, LLM repair
4. human review

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

## 10.2 Classical/non-target rules

If a unit is judged to be classical/non-target material, ask an LLM for one
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
- `./next`
- `./next dev`
- `./status`
- `./status dev`

The implicit no-argument track should be `working`.

### 10.6.3 Current one-step progression

`./next` should run one legal automatic step and then stop.

Example behavior:

- if a batch is only prepared, `./next` should build the alphabetic artifacts
- the next `./next` should build the unresolved alphabetic report
- the next `./next` should build the mechanical yomi JSONL
- after that, `./next` should stop with a clear blocking reason until a later
  automated stage is implemented

The intended UX is:

- run one command
- let it do one clear thing
- inspect `./status` when needed

### 10.6.4 Explicit wait states

OpenAI Batch and human review should be treated as first-class wait states.

Examples:

- `waiting_for_openai_batch`
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

1. classical/non-target Japanese
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

- use `gpt-5.4` as the default rescue model
- allow expensive tooling such as web search and stronger reasoning if needed
- generate a new best-effort yomi

Only if that still fails after human review should the pipeline consider a
`gpt-5.4-pro` escalation. That should be treated as a last resort for a very
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

1. When should classical/non-target Japanese feature extraction become a real
   mechanical classifier?
   The current idea is to rely partly on Sudachi and N-gram analysis quality,
   but no reviewed-data-backed scoring rule exists yet.

2. When should sentence-level `certain` gating be turned on for classical and
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
   - classical/non-target Japanese
   - unresolved alphabetic entity types
   - yomi errors
6. only then define the first reviewed-data-backed certainty rules

The project is not blocked on perfect theory. It is mainly blocked on getting a
small real-data loop running and looking at concrete examples.
