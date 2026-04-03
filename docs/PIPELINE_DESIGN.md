# Pipeline Design

## 1. Goal

Build a reproducible pipeline that turns filtered modern Japanese source text
into a reading-annotated corpus, while preserving enough metadata to:

- reject clearly non-modern material
- auto-accept only very safe cases
- auto-fix simple surface/reading boundary problems
- escalate harder cases to an LLM with the right amount of context
- send the final uncertain tail to human review
- promote reviewed data back into the core corpus used by `yomi-decoder`


## 2. Repo Boundary

This repository should own orchestration, staged artifacts, flags, review
queues, and promotion logic.

It should not reimplement the other projects.

External inputs and dependencies:

- Source corpus:
  `../llm-jp-corpus-v4/data/filtered/*_kept.jsonl.gz`
- Decoder:
  `../yomi-decoder/`
- Existing OpenAI helpers:
  `../openai/`

Recommended rule:

- `yomi-decoder` stays the place for decoding logic and decoder-model building.
- `yomi-corpus` stays the place for corpus ingestion, confidence decisions,
  repair pipelines, review, and export.


## 3. Key Design Decisions

### 3.1 Keep the canonical unit small and simple

The annotation unit should usually be a sentence or short line.

Each unit record must keep:

- `doc_id`
- `unit_id`
- `unit_seq`
- `char_start`
- `char_end`
- `text`
- `source_file`
- `source_line_no`

The source JSONL remains unchanged. Units are sequential spans over the source
document text, and the core invariant is that unit text must remain recoverable
from the original document by offsets.

### 3.2 Keep analysis inside each unit record

The current direction is to avoid separate `candidate`, `decision`, and
`review` record families.

Instead, each unit should contain nested analysis blocks such as:

- `analysis.mechanical`
- `analysis.llm`
- `analysis.human_review`

This is intentionally denormalized in favor of operational simplicity.

### 3.3 Deterministic first, LLM second

The first pass should not ask the API to "solve" the whole problem.

Instead:

1. generate deterministic sentence-level judgments and a mechanical yomi with
   Sudachi and `yomi-decoder`
2. mark tasks as `certain` only when the result is strong enough to skip the
   ordinary LLM stage
3. ask the LLM to classify only the units and tasks that are not mechanically
   certain
4. ask the LLM for yomi repair only when yomi correctness is not accepted
5. send best-effort output to human review

This keeps both cost and failure modes under control.

### 3.4 Separate the three main unit-level judgments

Each unit is judged on three mostly independent questions:

- whether it is classical or otherwise non-target Japanese material
- whether it contains minor alphabetic strings that should be skipped
- whether the current mechanical yomi is correct with high confidence

All three follow the same branching idea:

- if the mechanical judgment is `certain`, skip the ordinary LLM stage
- otherwise send that task to the LLM

This is especially important for the minor-alphabetic long tail, where cost
control matters as much as linguistic correctness.

### 3.5 Use Python for the main pipeline, not shell wrappers

The existing wrapper at `/home/hmanabe/scripts/sudachi` currently resolves to:

- `sudachipy tokenize -r "$HOME/.config/sudachi/sudachi.json"`

That is useful as the source of truth for configuration, but the production
pipeline should call SudachiPy from Python and point it at the same config
path. That avoids shelling out per sentence and makes metadata capture easier.


## 4. Proposed Repository Layout

Large generated artifacts should be untracked.

Suggested layout:

```text
yomi-corpus/
  README.md
  docs/
    PIPELINE_DESIGN.md
    WORKING_PIPELINE.md
  config/
    datasets/
    pipelines/
    model_profiles/
    prompts/
    regex/
  scripts/
    import_corpus.py
    run_stage.py
    build_review_queue.py
    promote_core_corpus.py
  src/
    yomi_corpus/
      io/
      ingest/
      units/
      tokenizer/
      decoder/
      filters/
      scoring/
      repair/
      llm/
      review/
      export/
  data/
    imports/
    units/
    review_queues/
    exports/
  runs/
    20260402_example/
      manifest.json
      metrics.json
      logs/
  scratch/
```

Recommended meaning:

- `config/`: tracked small inputs and prompt templates
- `src/`: reusable code
- `scripts/`: thin CLI entry points
- `data/`: materialized stage outputs, with analysis stored inside unit records
- `runs/`: per-run manifests, metrics, logs, temporary API payloads
- `scratch/`: ad hoc inspection files


## 5. Stage Model

Each stage should write a new artifact set instead of mutating prior outputs.

Suggested stages:

### S00 Import

Input:

- `*_kept.jsonl.gz` from `llm-jp-corpus-v4`

Output:

- imported document JSONL with stable `doc_id`

Responsibilities:

- assign stable IDs
- preserve original JSON record
- normalize source metadata names

### S10 Unit Extraction

Output:

- sentence or line units with stable offsets

Responsibilities:

- split documents into units
- keep unit-to-document mapping
- preserve sequential order inside each document

### S20 Mechanical Analysis

Output:

- unit records enriched with mechanical analysis

Responsibilities:

- judge classical or non-target Japanese
- judge minor alphabetic sequence presence
- generate mechanical yomi
- attach a `certain` flag for each task

Example signals:

- old kana or historical orthography
- heavy classical auxiliary patterns
- kanbun markers or citation style
- abnormal script mixture
- very high rare-character ratio
- Sudachi analysis quality
- `yomi-decoder` agreement or failure signals

### S30 Sentence-Level LLM Classification

Output:

- unit records enriched with LLM judgments for tasks that were not mechanically
  certain

Responsibilities:

- judge classical/non-target Japanese where needed
- judge minor alphabetic-sequence presence where needed
- judge whether the current yomi is correct where needed

### S40 Yomi Repair

Output:

- corrected yomi where the prior stage did not accept the mechanical yomi

Responsibilities:

- apply deterministic repair where possible
- use an LLM repair prompt where needed
- never send knowingly bad yomi directly to the first human-review UI

Useful features:

- tokenization agreement
- reading agreement
- OOV count
- kanji token with empty reading
- punctuation-only differences
- decoder piece-crossing behavior
- suspicious token length
- regex-matchable patterns

### S50 Human Review Pass 1

Output:

- first-pass human review annotations

Responsibilities:

- show the yomi-annotated sentence
- show three checkboxes:
  - classical/non-target Japanese
  - minor alphabetic sequence present
  - yomi fully correct
- allow the first two boxes to be prefilled
- keep the yomi-correct box initially unchecked

Important UI rule:

- do not show the raw sentence separately in this UI; the yomi-annotated
  sentence already contains the original text

### S60 Rule Harvesting

Output:

- candidate reusable rules derived from reviewed cases

Responsibilities:

- propose classical/non-target triggers
- propose minor-alphabetic whitelist or blacklist entries
- keep yomi repair rules separate from classification lists

This should remain conservative and is still an open design area.

### S70 Expensive Yomi Recovery

Output:

- best-effort high-cost yomi for units that still failed human pass 1 on yomi

Responsibilities:

- use a maximally capable LLM setup
- allow stronger reasoning or external search if needed
- prepare units for a second, narrower human review pass

### S80 Human Review Pass 2

Output:

- second-pass human comments on expensive-recovery outputs

Responsibilities:

- show only:
  - the yomi-annotated sentence
  - a free-text comment box
- leave the comment blank when the yomi is acceptable
- describe the remaining error in natural language when it is not

### S90 Final Editable Review

Output:

- human-edited final yomi for the hardest remaining cases

Responsibilities:

- show a fully editable text box containing the whole yomi-annotated sentence
- let the reviewer directly rewrite the full output into the correct form
- run validation and normalization after the edit
- reject or flag outputs that no longer match the required format

### S100 Export and Promotion

- corpus export
- core-corpus promotion candidates for `yomi-decoder`

Responsibilities:

- export only accepted records
- produce promotion files for decoder retraining
- keep promotion explicit and reversible


## 6. Canonical Record Types

The pipeline should use a small set of stable schemas.

### 6.1 Unit record

```json
{
  "doc_id": "ja_cc_level2:0000000123",
  "unit_id": "ja_cc_level2:0000000123:u0007",
  "unit_seq": 7,
  "char_start": 418,
  "char_end": 457,
  "text": "...",
  "source_file": "data/filtered/ja_cc_level2.surface_word_kept.jsonl.gz",
  "source_line_no": 123,
  "analysis": {
    "mechanical": {
      "classical_japanese": {
        "value": false,
        "certain": false
      },
      "minor_alphabetic_sequence": {
        "value": false,
        "certain": true
      },
      "yomi": {
        "rendered": "...",
        "certain": false
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


## 7. What Should Count as "Almost Certainly Safe"

This should be a derived rule bundle, not intuition.

A conservative first version:

- the mechanical task-specific judgment is `certain=true`
- Sudachi tokenization and decoder output do not disagree in a suspicious way
- no unknown non-kana token
- no kanji-containing token with empty reading
- no sign of old orthography or classical text
- no suspicious minor alphabetic long-tail case

Only units that pass all of those conditions should be auto-accepted.

This will sacrifice recall, which is fine early on.


## 8. Rule and Repair Strategy

The current design distinguishes between classification lists and yomi repair
rules.

For classification:

- prefer simple whitelist or blacklist entries for minor alphabetic sequences
- remain cautious about rule harvesting for classical/non-target Japanese

For yomi repair:

- regex-like deterministic transforms are still plausible
- LLM repair remains the fallback when deterministic repair is insufficient


## 9. OpenAI Layer

## 9.1 API choice

Use the Responses API as the canonical interface.

Keep two operating modes:

- synchronous mode for prompt iteration
- Batch API mode for production throughput

Both modes should use the same prompt builder and parser.

## 9.2 Model configuration

Do not hardcode one model name deep in the pipeline.

Instead, define model profiles in config, for example:

- `triage_fast`
- `repair_local`
- `repair_document`

As of 2026-04-02, the official API docs list `gpt-5.4` as the flagship model,
with `gpt-5.4-mini` and `gpt-5.4-nano` as cheaper variants. The pipeline
should still treat model choice as stage-specific configuration rather than
hardcoding one model for everything.

Recommended default split:

- `triage_fast`: `gpt-5.4-nano`
- `repair_local`: `gpt-5.4-mini`
- `repair_document`: `gpt-5.4`

## 9.3 Cost controls

For cheap triage:

- keep the static prompt prefix identical
- put variable unit text at the end
- use very short class-code outputs
- set low verbosity
- use the lowest reasoning effort that preserves accuracy
- batch production jobs

Do not force literally one output token at the transport layer if it makes
parsing fragile. A one-character class code plus newline is a good target.

## 9.4 Prompt caching

Prompt caching is only useful for exact shared prefixes and only starts once the
prompt is long enough.

Practical rule:

- keep instructions and examples first
- append unit data last
- use a stable prompt template version
- log cached-token counts in run metrics

## 9.5 Batch constraints

Batch jobs should be organized by stage and model profile.

Practical rule:

- one batch input file per model profile
- stable `custom_id`
- full manifest of prompt version, model profile, and parser version


## 10. Human Review

Human review is not a cleanup afterthought. It is one of the outputs of the
system.

Recommended policy:

- every exported record should know whether a human has looked at it
- human-reviewed records should remain distinguishable from auto-accepted ones
- corrected human decisions should be harvestable as future evaluation data
- the first review UI should show only the yomi-annotated sentence and three
  checkboxes
- the second review UI should show only the yomi-annotated sentence and a
  free-text comment box


## 11. Recommended First Iterations

### Iteration 0: skeleton and schemas

Build:

- repo layout
- schema definitions
- import and unit extraction
- run manifest format

Do not call the API yet.

### Iteration 1: deterministic baseline

Build:

- Python Sudachi adapter using the same config as the shell wrapper
- `yomi-decoder` adapter
- first confidence signals
- first conservative auto-accept rule

Measure:

- auto-accept rate
- obvious failure buckets

### Iteration 2: cheap LLM triage

Build:

- synchronous prompt-testing command
- one compact triage prompt
- batch submission path reusing the same prompt format

Measure:

- agreement with manual spot checks
- cost per 10k units
- distribution of class codes

### Iteration 3: regex repairs and context repair

Build:

- deterministic repair rules
- local-context repair prompt
- document-context repair prompt

Measure:

- repair precision
- reduction in manual-review load

### Iteration 4: human review loop and promotion

Build:

- review queue export/import
- reviewed-status tracking
- promotion pipeline into decoder training data


## 12. Immediate Next Step

The next implementation step should be small and measurable:

1. create the skeleton package and config layout
2. implement import plus unit extraction
3. implement a Python Sudachi adapter that reproduces the current shell-wrapper
   behavior
4. write one schema-checked artifact for unit records

That gives the project a stable spine before adding model calls.
