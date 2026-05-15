# Pipeline Design

## 1. Goal

Build a reproducible pipeline that turns filtered modern Japanese source text
into a reading-annotated corpus, while preserving enough metadata to:

- reject clearly non-modern material
- eventually auto-accept only very safe cases, but not before reviewed data
  exists
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

Recommended rule:

- `yomi-decoder` stays the place for decoding logic and decoder-model building.
- `yomi-corpus` stays the place for corpus ingestion, confidence decisions,
  repair pipelines, review, and export.
- OpenAI calling, prompt iteration, batch orchestration, and usage accounting
  should live in this repository's own LLM layer.


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

### 3.3 Features first, gated decisions later

The first pass should not ask the API to "solve" the whole problem.

Instead:

1. generate raw mechanical features and a mechanical yomi with Sudachi B-mode
   segmentation and
   `yomi-decoder`
2. keep non-target and sentence-level "certain" judgments disabled
   until enough reviewed data exists
3. ask the LLM to classify units that currently have no trusted mechanical
   decision
4. ask the LLM for yomi repair only when yomi correctness is not accepted
5. send best-effort output to human review

For yomi quality, reading correctness should take priority over coarse vs. fine
segmentation. If the readings are correct, extra splits introduced by the
morphological analyzer should not by themselves count as a failure.
This includes over-split katakana expressions such as `ディ/ディ プロ/プロ
マ/マ`: awkward segmentation is tolerable at this stage if the assigned
readings are correct. Cleaner token merging can be handled later if downstream
consumers need it.

Decoder-driven reading changes should be gated by support evidence, but they do
not need to be rare. If `yomi-decoder` differs from Sudachi on the same surface
span and the exact decoder entry has real N-gram support, the hybrid layer may
use the decoder reading as a tentative override. If the decoder appears to rely
only on unigram fallback, its output should not override Sudachi.

`piece_orders[0] >= 2` should be interpreted according to the decoder's current
count threshold: order-2 support means the boundary transition is attested at
least twice in the decoder corpus. A singleton 2-gram is not enough. This keeps
cases like a one-off `と-中(チュウ)` transition from becoming trusted evidence
even if the decoder still ranks that reading highly for other reasons.

For splitting one Sudachi token into multiple decoder entries, support must
cross the split boundary. Each decoder entry after the first must start with
`piece_orders[0] >= 2`; a later entry that starts at order 1 and only gains
support internally is not evidence for the boundary itself.

Stable two-kanji confidence experiments should not let decoder tokenization
become the decision surface. The hybrid rendered tokens are the units being
judged, and decoder evidence is projected onto those character spans. If the
decoder covers a hybrid token only by disagreeing subpieces, such as
`古/コ 本屋/ホンヤ` for `古本屋/フルホンヤ`, that token remains unsupported.
Likewise, a stable two-kanji token can forgive only itself; it does not make
the following boundary safe.

The stable-token inventory should come from raw SudachiDict CSV files, not from
compiled-tokenizer lookup. Component-only rows with `-1,-1` connection IDs
must count because they can expose readings used inside larger compounds. The
rule is reading uniqueness, not POS: unique proper nouns are allowed, while
surfaces with any additional raw dictionary reading are rejected.

The hybrid result may then pass through a post-hybrid repair memory. The first
version keeps this deliberately simple: every active rule is a regular
expression substitution over the rendered yomi string, with pair-bounded
literal repairs written as patterns such as
`(?<!\S)若しくは/モシクワ(?!\S)`. These repairs are best-effort systematic
fixes, not review-skip evidence. Each application should be logged with the
rule ID, matched text, replacement, count, and source so later review can audit
or revert the rule.

Numeric runs are not treated as ordinary yomi targets. Consecutive numeric
tokens should be grouped and emitted with an empty reading, for example
`2021/`, so a future number-reading module can handle them separately. This
must also be explicit in any yomi-triage prompt: `2021/`, `30/ 分/フン`, and
`1/ 回/カイ` are intentional, not malformed yomi, and should not trigger
`Review` by themselves.

N-gram support is currently an experimental confidence feature, not a committed
pipeline gate. The useful diagnostic variant is comma-span based: exclude units
with alphabetic letters; split spans only at `、`; treat empty-reading
kanji-like content as unsafe; exempt kana-only or symbol-only adjacent
boundaries; and require other adjacent entry boundaries to be supported by the
later entry starting at `piece_orders[0] >= 2`. Early measurements should be
reported both by span count and by character coverage, and kana-only spans
should not be counted as N-gram added value.

The stable two-kanji variant is also diagnostic for now. It can mark a hybrid
token as locally forgiven only when that same token is a two-kanji compound
with exactly one reading in the raw SudachiDict surface-to-reading inventory.
This is intended to handle common compounds whose absence from the N-gram path
is probably vocabulary sparsity, while avoiding safety claims based on
decoder-only over-splitting or compiled-dictionary candidate exposure.

Decoder overrides and mechanical safety are deliberately different judgments.
If a decoder reading differs from Sudachi and has repeated 2-gram support, it
can be used as a normal tentative correction signal even if many such changes
later turn out to be wrong. Review remains the default, and Sudachi/decoder
disagreement is not a safe-skip condition. Safe auto-acceptance should require
Sudachi and decoder to agree, with the whole relevant span supported from start
to end by repeated 2-gram evidence.

This keeps both cost and failure modes under control.

### 3.4 Use two judgment granularities

Sentence-level judgments should handle:

- non-target material
- whether the current mechanical yomi is correct with high confidence

The minor-alphabetic problem should instead be handled at the batch entity-type
level:

- extract alphabetic entity occurrences from all units in the batch
- aggregate them into entity types
- resolve entity types through whitelist/blacklist lookup first
- send only unresolved entity types to the LLM or human review
- project entity-type decisions back onto units afterward

This matters because the alphabetic long tail is primarily a repeated entity
problem, not a repeated sentence problem.

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

For the alphabetic subsystem specifically, distinguish:

- batch-local artifacts under `data/units/...`
- cross-batch state under `data/state/alphabetic/`


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
- batch-level alphabetic entity inventory

Responsibilities:

- judge non-target status
- generate mechanical yomi
- add a conservative yomi auto-accept flag for units that do not need review
- extract alphabetic entity occurrences and aggregate entity types
- attach a `certain` flag for sentence-level tasks

Example signals:

- old kana or historical orthography
- heavy classical auxiliary patterns
- kanbun markers or citation style
- abnormal script mixture
- very high rare-character ratio
- Sudachi analysis quality
- `yomi-decoder` agreement or failure signals

The first yomi auto-accept rule is intentionally narrow. A unit is accepted
only when it has generated yomi, has no unresolved non-numeric readings,
Sudachi and the decoder agree on the rendered output, and the decoder top
candidate has full repeated N-gram support. The stable two-kanji relaxation is
an optional auto-accept profile, not a hardcoded `dev` behavior. That relaxation
is still conservative because Sudachi/decoder agreement remains mandatory and
ambiguous raw SudachiDict readings are rejected. Grouped numeric runs such as
`2021/` are allowed because number pronunciation is outside the current yomi
task.

### S30 Sentence-Level LLM Classification

Output:

- unit records enriched with LLM judgments for tasks that were not mechanically
  certain
- entity-type judgments for unresolved alphabetic entity types

Responsibilities:

- run first-stage yomi triage on units not mechanically auto-accepted
- return exactly one yomi triage label: `OK`, `Review`, or `Skip`
- treat `Skip` as non-target material such as foreign-language text,
  classical Japanese, kanbun, or garbled text
- judge unresolved alphabetic entity types where needed

The default yomi triage output should be a single token, not JSON. Reasons and
fine-grained labels belong in debug/eval mode because ordinary production runs
should minimize expensive model output.

The production yomi triage prompt should use the clean representative boundary
prompt rather than failure-specific lexical patches. The current baseline is
`config/prompts/yomi_triage_v2.txt`, derived from the
`general_representative_v3` prompt experiments, with `gpt-5.5` and
`reasoning_effort=none`. Synthetic-only failures should not be promoted into
prompt rules. Recurring real failures can later become deterministic repair or
review rules.

The materialized triage stage writes two artifacts:

- raw LLM results, preserving raw text, parsed status, parse errors, usage, and
  metadata for audit and cost reporting
- `units.yomi.triaged.jsonl`, where every unit has
  `analysis.llm.yomi_triage.status`

Mechanically auto-accepted units receive `OK` with source `auto_accept`.
Queued units receive the parsed LLM status when parsing succeeds; parse errors
must be treated as `Review` so malformed model output cannot silently accept or
skip a unit. Later yomi repair consumes only `Review` units, while `Skip` is
excluded and `OK` is accepted subject to later audit sampling.

#### Resumable LLM execution

LLM execution should be handled by a generic resumable job layer rather than by
adding separate queued/submitted/completed pipeline stages for each LLM-using
task. The domain pipeline stage starts or resumes an LLM job; the LLM job owns
request files, result files, remote job IDs, progress, retry state, and logs.

The same job interface should support both sync and OpenAI Batch modes:

- `sync`: process rows sequentially or with small concurrency, append each
  completed result, and skip already completed item IDs when resumed
- `batch`: submit the remaining rows as one remote batch job, persist the
  remote batch ID, poll on later runs, fetch results when complete, and resubmit
  only missing or failed items if needed

Both modes should report progress as completed items over total items. Sync mode
can update progress after each response. Batch mode should poll the remote
OpenAI Batch object and use its `request_counts` (`completed`, `failed`,
`total`) as the progress source while the server-side job is running. The
output file is still available only after completion, so final result parsing
must continue to use the downloaded output file and each request's `custom_id`.

The domain stage should complete only after the job has produced a complete
result JSONL and those results have been applied to the domain artifact. For
yomi triage, that means the job can be running while the domain step is still
`yomi_triage`, and the stage advances to `yomi_triage_completed` only after
`units.yomi.triaged.jsonl` is written.

Interruptions should be normal. The operator may stop sync mode partway through;
rerunning `./next` resumes from result rows already present. For batch mode,
rerunning `./next` polls the stored remote job, reports current
`request_counts`, and applies results once the job is complete.

#### Sentence vs comma-span operating modes

The canonical corpus unit remains the sentence-like unit produced from the
source document. The pipeline may nevertheless run yomi triage and repair on a
derived work-item layer. Two modes should be supported:

- `sentence`: the current sentence-like unit is the triage and repair work item
- `comma_span`: each sentence-like unit is split at Japanese commas (`、`), and
  each span becomes a triage/repair work item

The batch manifest or yomi config should record the selected mode explicitly in
`yomi_policy.unit_mode`. Track names should provide defaults, not hardcoded
behavior. `sentence` should remain the conservative default for `working` until
span-mode export and repair are well tested. `comma_span` is a useful `dev`
default candidate because it can increase automatic `OK` coverage and reduce
later review volume, at the cost of more LLM calls and more reconstruction
logic.

Yomi-specific policy should record only yomi structural decisions:

```json
{
  "unit_mode": "sentence",
  "auto_accept_profile": "strict"
}
```

LLM configuration is cross-cutting and should be stored separately as two
task-to-setting maps:

- `llm_policy`: which model profile each task uses
- `llm_execution_policy`: whether each task runs through sync calls or OpenAI
  Batch

```json
{
  "alphabetic_entity_judge": "standard",
  "non_target_judge": "standard",
  "yomi_triage": "standard",
  "yomi_repair": "standard",
  "yomi_rescue": "strong"
}
```

```json
{
  "alphabetic_entity_judge": "batch",
  "non_target_judge": "batch",
  "yomi_triage": "batch",
  "yomi_repair": "sync",
  "yomi_rescue": "sync"
}
```

Initial allowed values:

- `unit_mode`: `sentence`, `comma_span`
- `auto_accept_profile`: `off`, `strict`, `stable_two_kanji`
- LLM profiles: `smoke`, `economy`, `standard`, `strong`
- LLM execution modes: `sync`, `batch`

Suggested yomi defaults are `working={unit_mode=sentence,
auto_accept_profile=strict}` and
`dev={unit_mode=sentence,auto_accept_profile=stable_two_kanji}` for now. Track
defaults should also choose an LLM profile per task. Operators should be able to
override these per batch, so dev can run with no auto-accept, working can later
run with stable two-kanji auto-accept, and either track can use cheaper or
stronger model profiles for specific tasks. Execution mode should be equally
configurable per task, because prompt exploration and small repair batches are
often easier in `sync`, while large classification-style tasks are better in
`batch`. The selected explicit values must be stored with the batch for
reproducibility.

These defaults should not remain hidden Python constants. They should be moved
to a small source-controlled project config, for example:

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

[tracks.working.llm_execution_policy]
alphabetic_entity_judge = "batch"
non_target_judge = "batch"
yomi_triage = "batch"
yomi_repair = "sync"
yomi_rescue = "sync"

[tracks.dev.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "stable_two_kanji"

[tracks.dev.llm_policy]
alphabetic_entity_judge = "economy"
non_target_judge = "economy"
yomi_triage = "economy"
yomi_repair = "economy"
yomi_rescue = "standard"

[tracks.dev.llm_execution_policy]
alphabetic_entity_judge = "sync"
non_target_judge = "sync"
yomi_triage = "sync"
yomi_repair = "sync"
yomi_rescue = "sync"
```

The prepare-time precedence should stay deliberately shallow:

- explicit CLI override when preparing a batch
- configured track default

After preparation, all later pipeline stages and reruns should use the stored
resolved batch policy rather than re-reading current track defaults.

There should not be a broad global override layer until repeated operational
pain justifies it.

In `comma_span` mode, span artifacts should preserve enough parent information
to reconstruct sentence-level output:

- `span_id`
- parent `unit_id`
- `span_seq`
- span text and rendered yomi
- character offsets within the parent sentence when practical
- rendered pair indexes or another stable replacement range when practical
- optional context fields such as previous span, next span, and full parent
  sentence

Triage aggregation from spans to the parent sentence is monotonic:

- if any span is `Skip`, the parent sentence is `Skip`
- else if any span is `Review`, the parent sentence is `Review`
- else the parent sentence is `OK`

`Skip` is sentence-destructive. If any span in a parent sentence is `Skip`, the
whole parent sentence is excluded from later yomi repair and final export. Other
spans in that same sentence, even if labeled `OK` or `Review`, are retained only
as audit metadata and are not repaired.

`Review` remains local in span mode. If no sibling span is `Skip`, only
`Review` spans proceed to repair. The repair prompt should target the span, but
may receive context such as neighboring spans, the full parent sentence,
previous/next sentences, or source metadata when needed. The final exported
artifact must still be sentence-level: repaired spans are merged back into the
parent sentence, `OK` spans are preserved, and span-level decisions remain
available under audit metadata.

### S40 Yomi Repair

Output:

- corrected yomi where the prior stage did not accept the mechanical yomi

Responsibilities:

- apply deterministic repair where possible
- use an LLM repair prompt only for units labeled `Review` by yomi triage
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
- show two checkboxes:
  - non-target status
  - yomi fully correct
- allow the first box to be prefilled
- keep the yomi-correct box initially unchecked

Important UI rule:

- do not show the raw sentence separately in this UI; the yomi-annotated
  sentence already contains the original text

Minor alphabetic review should live in a separate entity-level flow with example
sentences, not in this sentence-level UI.

### S60 Rule Harvesting

Output:

- candidate reusable rules derived from reviewed cases
- candidate whitelist or blacklist promotions for alphabetic entity types

Responsibilities:

- propose non-target triggers
- propose minor-alphabetic whitelist or blacklist entries
- keep yomi repair rules separate from classification lists

This should remain conservative and is still an open design area.

### S65 Promotion Candidate Review

Output:

- human-reviewed decisions on whether candidate alphabetic entity types should
  be promoted to the global whitelist or blacklist

Responsibilities:

- review only promotion candidates, not every unresolved entity type
- confirm or reject globally reused list entries
- keep this policy-level review separate from sentence-level corpus review

Temporary candidate threshold:

- treat `3` consistent observations as enough to surface a candidate for either
  whitelist or blacklist review
- keep actual promotion gated on human approval
- treat this as a provisional operational rule, not a final policy

Rationale:

- promotion decisions have high leverage because they affect future batches
- candidate review is a better use of human time than broad manual screening of
  every entity occurrence
- blacklist promotion may later need to stay more conservative than whitelist
  promotion, but the temporary threshold is currently `3` for both directions

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
      "non_target": {
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


## 7. What Should Count as "Almost Certainly Safe"

This should eventually become a derived rule bundle, not intuition.

Current implementation policy:

- auto-accept only when Sudachi and the decoder agree and the decoder candidate
  has full repeated N-gram support
- do not define a sentence-level `certain=true` rule for non-target judgment
  yet
- collect the raw mechanical features needed to learn those rules later

The point is to avoid inventing confidence rules before reviewed data exists.


## 8. Rule and Repair Strategy

The current design distinguishes between classification lists and yomi repair
rules.

For classification:

- prefer simple whitelist or blacklist entries for Latin/alphanumeric entity
  types that are either in scope for modern Japanese text or out of scope
- match those entity-list entries case-insensitively by default
- keep exact-case exceptions for short tokens and acronyms
- judge those entities primarily at the entity-type level, not the sentence
  level
- remain cautious about rule harvesting for non-target status

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

Prompt exploration should prefer synchronous Responses API calls even when the
eventual production path uses batches. The exploration loop needs immediate raw
outputs, parsed labels, usage, and failure reports so prompts can be revised
quickly. Batch mode should enter after candidate prompts are already stable
enough for larger regression checks or production throughput.

## 9.2 Model configuration

Do not hardcode model choice deep in the pipeline, but the project should still
have an explicit default policy.

Recommended default policy:

- use `gpt-5.5` as the normal model for real annotation work
- use `gpt-5.5-pro` only as a last-resort rescue model for a very small tail
- use `gpt-5.4-nano` only for plumbing checks, transport tests, and cache/token
  instrumentation
- do not assume `gpt-5.4-mini` is the normal cost-saving path unless task-level
  evals show that the quality tradeoff is actually worth it

Model selection should be expressed through named capability/cost profiles, not
scattered model strings in pipeline branches. The batch should store
`llm_policy`, and the runner should resolve each task's profile into the task
config overrides used for the actual API call.

Initial profile meanings:

- `smoke`: plumbing-only API checks, normally `gpt-5.4-nano`
- `economy`: cheaper interactive/dev pipeline checks, normally `gpt-5.4-mini`
- `standard`: production-quality judgment/repair, normally `gpt-5.5`
- `strong`: exceptional expensive rescue settings, normally `gpt-5.5-pro` or
  web-search-enabled repair/check tasks

Track defaults should choose profiles per LLM task, not one profile for the
whole batch. A dev batch may use `standard` for realistic dry runs, and a
working batch may use `smoke` only for explicit plumbing checks before real
annotation. The resolved profile should be recorded in batch artifacts together
with the actual model and reasoning effort so later cost and accuracy audits are
unambiguous.

The mapping from track/task to default LLM profile should come from the same
source-controlled defaults config as `unit_mode` and `auto_accept_profile`.
Profile definitions live in the LLM profile config, while the prepared batch
stores the resolved task-to-profile map plus each artifact's resolved model
settings.

Stage-oriented defaults:

- `alphabetic_entity_judge`: `gpt-5.5`
- `non_target_judge`: `gpt-5.5`
- `yomi_check`: `gpt-5.5`
- `yomi_repair`: `gpt-5.5`
- post-review rescue repair: `gpt-5.5` with web search allowed
- final emergency escalation: `gpt-5.5-pro` with web search, only after
  cheaper paths and human review have already failed

This keeps the main path simple and high-quality while still reserving a clear
escape hatch for the hardest cases.

For prompt exploration, `gpt-5.4-mini` is a reasonable search model because the
goal is to test prompt shape, label semantics, and sample quality quickly. Its
results should not be treated as the final production quality estimate. The
search should sweep mini reasoning effort settings and score each run by both
quality and cost. Promote only the best few prompt candidates to `gpt-5.5` for
the final production-quality comparison.

## 9.3 Cost controls

For ordinary judgment tasks:

- keep the static prompt prefix identical
- put variable item text at the end
- set low verbosity
- use the lowest reasoning effort that preserves accuracy
- batch production jobs

For production cost control, prefer:

- `gpt-5.5` plus caching and batching
- strict structured outputs
- short outputs for judgment tasks

Do not assume that moving routine corpus judgments to `mini` or `nano` is the
best optimization by default. Verify that with task-level evals first.

Also do not assume that bundling multiple judgments into one prompt is the best
optimization by default. The default policy should be one prompt per judgment
task, because that makes parsing, prompt iteration, and regression diagnosis
much cleaner.

Merged prompts should be treated as an optimization step that needs evidence.
Only merge tasks after evals show that the combined prompt preserves accuracy
and parser stability, and only when the tasks share the same unit, context
requirements, model policy, and failure surface.

## 9.4 Prompt caching

Prompt caching is only useful for exact shared prefixes and only starts once the
prompt is long enough.

Practical rule:

- keep instructions and examples first
- append unit data last
- use a stable prompt template version
- log cached-token counts in run metrics

For naturally short judgment prompts, do not pad them just to reach the cache
threshold. In many cases, a shorter and clearer prompt is the better
optimization. Caching matters most when a task already needs a long stable
shared prefix for good reasons, such as context-heavy repair or tool-using
rescue prompts.

## 9.5 Batch constraints

Batch jobs should be organized by stage and model profile.

Practical rule:

- one batch input file per model profile
- stable `custom_id`
- full manifest of prompt version, model profile, and parser version

Tool-using rescue jobs should be separated from ordinary batch jobs because they
have a different cost profile and should only operate on the small residue that
survives the normal pipeline.

## 9.6 Local pipeline orchestration

The operator should not need to remember the exact next script for each batch.

Recommended design:

- keep durable local pipeline state for each batch
- keep a current-batch pointer per track
- use `working` as the implicit default track and `dev` as an explicit second
  track
- `working` is the strict protected track and should enforce required human
  review gates; `dev` is the relaxed experimental track
- expose read-only `status` and mutating `next` commands
- expose a `prepare` command that allocates the next batch name for a track and
  extracts the requested number of documents
- let `next` perform one legal automatic step and then stop cleanly

This project has three different kinds of stages:

- fully automatic local computation
- external waits such as OpenAI Batch jobs
- human-review waits

Those should all be represented as normal pipeline states.

### 9.6.1 Per-batch and per-track state

Recommended storage:

- one state file per batch, such as `data/pipeline/batches/<batch_id>.json`
- one track pointer file per track, such as `data/pipeline/tracks/working.json`

Batch state should record at least:

- `batch_id`
- `track_name`
- `current_stage`
- `artifacts`
- `blocking_reason`
- `updated_at`

The state enum can start small and grow with the actual implementation.

Track state should record at least:

- `track_name`
- `current_batch_name`
- `updated_at`

### 9.6.2 Current command surface

Current intended operator commands:

- `./prepare 100`
- `./prepare dev 10`
- `./prepare --yomi-unit-mode comma_span --yomi-auto-accept-profile off --llm-profile yomi_triage=smoke dev 10`
- `./next`
- `./next dev`
- `./next --force-stage yomi_generated`
- `./status`
- `./status dev`

The implicit no-argument track should be `working`.

### 9.6.3 Current one-step behavior

Current recommended behavior for the main orchestration command:

- load the current batch state for the requested track
- inspect the current stage and prerequisites
- run the next automatic step if legal
- stop after that one step and report the updated state

Examples:

- if a batch is freshly prepared, `./next` should build the alphabetic
  artifacts
- the following `./next` should build the unresolved alphabetic report
- the following `./next` should build the mechanical yomi JSONL
- the following `./next` should add the yomi auto-accept artifact
- once no later automated stage is implemented, `./next` should report that
  blocking reason and stop
- `./next --force-stage <stage>` should rerun the current completed stage
- on `working`, a confirmation prompt should appear only when rerunning would
  overwrite existing artifacts


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

### 10.1 Review transport

The review UI should not assume writable hosting on the cluster.

Current preferred transport design:

- keep the review UI in this same repository
- isolate it from the Python pipeline code as a small static web app
- host the static review UI on GitHub Pages
- export immutable review-pack JSON from the cluster
- use GitHub as the return mailbox
- for now, use one Issue per review pack and one comment per submission

The cluster should later poll GitHub, extract valid submission payloads, and
reconstruct the latest merged review state.

Recommended repo layout:

- `src/yomi_corpus/`: Python pipeline code
- `scripts/`: operator scripts and local orchestration
- `web/review/`: source for the static review app
- `docs/`: built static assets served by GitHub Pages

The important point is not the exact directory names but the separation:

- Python pipeline code should stay independent of frontend tooling
- the review UI should remain versioned with this project
- GitHub Pages output should be a normal repo artifact, not a separate system

### 10.2 Review state model

Separate three things:

- immutable review pack
- device-local draft state in the browser
- append-only review submissions returned through GitHub

The browser should persist local drafts by `review_stage` and `pack_id` so a
reviewer can leave the page and return later.

### 10.3 Reviewed ranges and overrides

For promotion-candidate review, a reviewed range matters more than explicit
marks on every approved item.

Recommended behavior:

- by default, the whole pack is in scope for export
- optional `from` / `to` markers narrow the reviewed range
- items outside that range remain visible but faded
- within the reviewed range, no explicit mark means "accept the proposed
  action"
- the submission payload should therefore contain reviewed range metadata plus
  sparse item-level overrides

### 10.4 Multiple partial submissions

One pack may produce multiple submissions.

That supports:

- interrupted review
- work split by range
- accidental multi-device use

Merge rule:

- replay submissions in order
- later submissions overwrite earlier results for overlapping items
- for an overlapping reviewed range, reset that range to default proposal
  acceptance first, then apply that submission's sparse overrides

This is intentionally simple and leaves conflict responsibility to the user.


## 11. Recommended First Iterations

### Iteration 0: skeleton and schemas

Build:

- repo layout
- schema definitions
- import and unit extraction
- run manifest format

Do not call the API yet.

### Iteration 1: mechanical feature baseline

Build:

- Python Sudachi adapter using the same config as the shell wrapper
- `yomi-decoder` adapter
- first feature capture for later scoring
- no sentence-level gating rules yet

Measure:

- auto-accept rate
- obvious failure buckets

### Iteration 2: cheap LLM triage

Before production use, run a concentrated prompt-optimization phase that is
separate from corpus batch progression. Build a fixed gold eval set, test many
prompt candidates, and freeze a prompt version before large-scale processing.
This has an upfront cost, but it avoids paying corpus-scale costs for weak early
prompts and keeps batch outputs comparable across time.

Use real-time API calls for this search phase. Do not use Batch API for the
first exploratory loop, because waiting for batch completion slows prompt
iteration and makes it harder to inspect failures while the prompt is still
fluid.

Build:

- synchronous prompt-testing command
- one compact triage prompt
- batch submission path reusing the same prompt format
- gold eval data under `data/evals/yomi_triage/`
- prompt candidates under `config/prompts/`

Measure:

- accuracy against the fixed gold set
- dangerous confusion types, especially `OK` or `Review` when the expected
  label is `Skip`
- parse-error rate
- input/output token counts
- cached input token counts when reported
- estimated cost
- cost per 10k units
- distribution of class codes
- model and reasoning effort
- exact API input-token counts for cache-sensitive candidate prompts

Search strategy:

- start with `gpt-5.4-mini` and synchronous calls
- test broad prompt families before small wording edits
- include zero-shot prompts, but expect few-shot boundary examples to be needed
- compare `low` and `medium` reasoning effort first; add higher effort only if
  accuracy failures look reasoning-bound
- rank candidates by dangerous-error avoidance, parse stability, accuracy, then
  token/cost efficiency
- rerun only the strongest candidates on `gpt-5.5` before freezing production

Cache strategy:

- keep instructions and examples before variable unit text so cacheable content
  is a stable prefix
- use local `tiktoken` only as an estimate for GPT-5-family prompts
- use the Responses `input_tokens` endpoint for exact counts when tuning around
  the 1024-token cache threshold
- treat token-count endpoint cost, if any, as negligible relative to prompt eval
  and corpus annotation costs
- if examples are accuracy-useful and the prompt is already near 1k tokens,
  tune the static prefix to about 1050-1150 exact API-counted tokens rather than
  leaving it just under the cache threshold
- do not add meaningless filler only to force caching

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
