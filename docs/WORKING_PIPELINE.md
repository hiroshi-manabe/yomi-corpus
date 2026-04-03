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

Each sentence-like unit is judged on three mostly independent questions:

1. Is this classical or non-target Japanese material?
   Examples:
   - old kana
   - old orthography
   - classical Japanese
   - kanbun

2. Does this sentence contain minor alphabetic strings that are not worth
   spending much annotation effort on?
   Examples:
   - obscure English, French, or other foreign proper names
   - foreign titles or event names that are not well-established in modern
     Japanese usage

3. Is the current mechanically generated yomi correct with high confidence?

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
        "matches": ["Concerts", "de", "Midi"]
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

- `value`: whether the unit looks like classical Japanese, kanbun, or other
  non-target material
- `certain`: whether the mechanical system is confident enough that only human
  confirmation remains

This judgment will likely depend on:

- Sudachi behavior
- N-gram decoder behavior
- script and orthography heuristics

The exact rules are not settled yet.

### 5.2 Minor alphabetic-sequence judgment

- `value`: whether the unit contains alphabetic material that should probably be
  skipped rather than fully annotated
- `certain`: whether that decision is mechanically safe enough

This is not limited to English. The problem includes other Latin-script foreign
material such as French.

Example of the kind of sentence that may be skipped:

- `Concerts de Midi`

Example of the kind of item that should probably be retained:

- `iPhone`
- `Android`

### 5.3 Mechanical yomi

The mechanical pass should produce:

- one best yomi candidate for the full unit
- a `certain` flag for whether that yomi should go directly to human checking
  without an intermediate LLM step

This should be based on Sudachi plus `yomi-decoder` plus deterministic
agreement heuristics.


## 6. Interpretation of "Certain"

For all three tasks, "certain" means:

- the pipeline believes the answer is strong enough that the next step should be
  human confirmation, not LLM arbitration

So the immediate branch is:

- if `certain=true`, skip the ordinary LLM stage for that task
- if `certain=false`, send that task to an LLM

This is the same high-level pattern for:

- classical/non-target judgment
- minor alphabetic-sequence judgment
- yomi correctness judgment


## 7. Minor Alphabetic Sequences

This is a cost-control policy as much as a linguistic policy.

The working assumption is:

- a small percentage of difficult foreign alphabetic strings could consume a
  disproportionate amount of time
- therefore the system should prefer to skip low-value long-tail cases rather
  than aggressively annotate everything

## 7.1 Whitelist

The project should keep a whitelist of alphabetic strings that are accepted as
rooted in modern Japanese usage.

Examples:

- `iPhone`
- `Android`

Initial idea:

- start with no whitelist
- keep units that were accepted and successfully yomi-annotated
- extract useful alphabetic strings from those accepted units
- add good recurring items to the whitelist

Then the mechanical rule can become:

- if all alphabetic strings in a unit are in the whitelist, skip the
  minor-alphabetic check for that unit

## 7.2 Blacklist

A blacklist-oriented approach may be simpler than generating regex rules for
minor alphabetic sequences.

Current preference:

- start with word-level whitelist and blacklist entries
- use word-boundary-aware matching for alphabetic material
- avoid regex unless there is a clear payoff

This remains a working decision, not a final one.

## 7.3 Rule harvesting

If the LLM or a human identifies a unit as containing a minor alphabetic
sequence, the system may later harvest a reusable blacklist-like rule from that
decision.

Current preference:

- do not do this too early
- start with explicit token entries
- only introduce broader matching patterns if maintenance remains manageable


## 8. Classical Japanese and Kanbun

This area is less settled.

Current working idea:

- rely first on how well Sudachi and the N-gram system can analyze the unit
- combine that with orthographic and script-level heuristics
- use LLM judgment on units that are not mechanically certain

Potential signals:

- old kana
- old orthography
- unusual auxiliary patterns
- script mixtures that rarely occur in modern prose
- systematic analysis failures from Sudachi or the decoder

The exact decision boundary is still unclear and should be refined by looking at
real examples and failure cases.


## 9. LLM Stage

For now, the three sentence-level judgments should be handled with separate
prompts:

1. classical/non-target Japanese or not
2. minor alphabetic sequence present or not
3. current yomi correct or not

This is intentionally simple, even if it may not be cost-optimal.

If the cost later proves too high, the prompts can be merged or restructured.

## 9.1 Inputs to the LLM

The LLM should only receive tasks that were not mechanically marked as certain.

For each relevant unit, it should judge:

- `classical_japanese`
- `minor_alphabetic_sequence`
- `yomi_is_correct`

At this stage, the LLM is still doing classification, not necessarily repair.

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

## 10.3 Minor alphabetic rules

If a unit is judged to contain minor alphabetic material, ask for one reusable
trigger.

Current preference is to keep these as simple token-level entries rather than
general regexes.

Example discussed:

- `concerts`

with word-boundary-aware matching.

## 10.4 Yomi repair rules

For yomi correction, regex-like repair rules still seem reasonable because many
useful fixes may be boundary or formatting corrections rather than semantic
reinterpretations.

This area needs experimentation.


## 11. Human Review: Pass 1

The first human review UI should be sentence-based.

Display:

- the current best-effort yomi-annotated sentence
- three checkboxes

The three checkboxes are:

1. classical/non-target Japanese
2. minor alphabetic sequence present
3. yomi fully correct

Important intended behavior:

- the first two checkboxes may already be prefilled by mechanical or LLM output
- the third checkbox should start unchecked
- the yomi-annotated sentence already contains the original sentence content, so
  a separate raw-text field is unnecessary in this UI
- a sentence that is already known to have incorrect yomi should not be shown as
  a knowingly bad candidate; instead the pipeline should first attempt repair


## 12. Human Review: Pass 2

Units that still fail the yomi check after the best-effort pipeline should enter
a second, more expensive path.

## 12.1 Expensive repair

For these units:

- use a maximally capable LLM setup
- allow expensive tooling such as web search and stronger reasoning if needed
- generate a new best-effort yomi

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
- run LLM classification only where certainty is absent
- run LLM yomi repair where needed
- build human review queues


## 14. Unclear or Open Points

The following points are not settled and should be treated as explicit open
questions:

1. How exactly should classical/non-target Japanese be detected mechanically?
   The current idea is to rely partly on Sudachi and N-gram analysis quality,
   but no concrete scoring rule exists yet.

2. What counts as "certain" for each of the three tasks?
   The branching logic is clear, but the thresholds are not.

3. Should the minor alphabetic mechanism use a pure whitelist/blacklist system,
   or eventually add regex-like patterns?
   Current preference is token-level lists first.

4. What exact format should represent unit-local nested analysis?
   The current example is only a draft.

5. How should the mechanical yomi confidence score be computed from Sudachi and
   `yomi-decoder` outputs?

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
   - minor alphabetic strings
   - yomi errors
6. only then define the first deterministic certainty rules

The project is not blocked on perfect theory. It is mainly blocked on getting a
small real-data loop running and looking at concrete examples.
