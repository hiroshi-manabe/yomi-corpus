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

Canonical `surface/reading` tokens should also satisfy a structural validity
rule before any `OK` decision is trusted:

- if `surface` contains kanji or Latin letters, `reading` must be non-empty and
  contain only katakana plus the long-vowel mark `ー`
- if `surface` is digits only, `reading` must be empty, so `2021/` is valid and
  `2021/2021` is invalid
- otherwise, `reading` must equal the result of converting hiragana in
  `surface` to katakana while leaving non-kana characters unchanged, so
  `です/デス` and `。/。` are valid

This is a format guardrail, not a semantic yomi correctness rule. A unit with a
structurally invalid token can still be sent to the LLM for `Skip` detection,
but an LLM `OK` must be forced back to `Review`.

Original source whitespace should be preserved in the canonical yomi token
stream. Before Sudachi and decoder processing, convert source ASCII space
`U+0020` to NBSP `U+00A0`; keep full-width space `U+3000` unchanged. Whitespace
tokens are then rendered explicitly as `NBSP/NBSP` or `　/　` rather than being
dropped. ASCII space remains reserved as the canonical token separator.

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

Current LLM yomi direction:

- use a binary raw-text scope triage (`Keep`/`Skip`) only to exclude non-target
  material
- do not ask the first LLM pass to certify yomi correctness as
  `OK/Review/Skip`
- ask the LLM for independent readings of marked kanji/Latin targets in
  furigana-style context
- compare those readings with the stored Sudachi/hybrid readings
- route LLM/mechanical agreement to bulk audit or future auto-acceptance
  experiments, and route disagreement or parse failure to focused review

This makes the LLM a second reading source rather than a black-box correctness
classifier. It also preserves diagnostic metadata: N-gram support, stable
two-kanji safety, LLM agreement, and human approval remain separate signals.
The older `OK/Review/Skip` and `OK/Fix/Ambiguous/Skip` triage experiments are
kept as prompt-evaluation history, not as the main implementation target.

### 3.4 Use two judgment granularities

Sentence-level judgments should handle:

- non-target material
- whether the current mechanical yomi is correct with high confidence

The minor-alphabetic problem should instead be handled at the batch entity-type
level:

- extract alphabetic entity occurrences from all units in the batch
- aggregate them into entity types
- resolve entity types through whitelist/blacklist lookup first
- send only unresolved entity types to the LLM once, then cache the judgment
  globally
- use the effective entity status immediately for provisional unit-level skip
- let final human review override provisional skip with the same `Skip`
  checkbox used for ordinary skip decisions
- keep the judgment source for audit/debug, but make behavior depend only on
  effective `in_scope`, `out_of_scope`, or `unknown`

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

### S30 LLM Scope Gate and Reading Signals

Output:

- unit records enriched with binary scope judgments for target/non-target text
- per-target LLM reading results for yomi targets that were not suppressed by
  deterministic confidence rules

Responsibilities:

- run scope triage on raw text before mechanical yomi generation
- return exactly one scope label: `Keep` or `Skip`
- treat `Skip` as non-target material such as foreign-language text, old
  Japanese prose, kanbun, Chinese, spam, or garbled text
- treat `Skip` as a conservative privacy/reputational-risk gate when a unit
  identifies a private person together with sensitive negative information such
  as arrest, criminal suspicion, accusations, scandal, disciplinary action, or
  illness
- build a yomi-reading queue from unresolved kanji/Latin targets in the
  Sudachi/hybrid token stream
- ask the LLM for the reading of exactly one marked target per request
- compare the LLM reading with the stored mechanical reading

The default scope-triage output should be a single token, not JSON. Reasons
belong in debug/eval mode because ordinary production runs should minimize
expensive model output. Scope triage should normally use the `economy` profile
unless evals show that a stronger model materially reduces dangerous `Keep` or
`Skip` errors.

The default LLM reading output should be a one-key JSON object such as
`{"話":"はな"}`. The current default request context is plain source text with
one target marked by `**...**`, using the ultra-short pattern prompt
`目が**痛**い。->{"痛":"いた"}\n{marked_text}->`. The model should return only
the marked target's reading and must not rewrite the whole sentence.

The materialized LLM stage writes separate artifacts:

- raw LLM results, preserving raw text, parsed status, parse errors, usage, and
  metadata for audit and cost reporting
- scope-gated unit artifacts, where every unit has a parsed `Keep`/`Skip`
  result or a safe fallback
- yomi-reading queue/result/apply artifacts, where each requested target has an
  LLM reading, comparison status, and usage metadata

Scope parse errors must be treated conservatively so malformed model output
cannot silently skip or accept a unit. Yomi-reading parse errors must not become
agreement. Because yomi-reading requests ask for a tiny one-key JSON object,
format/key parse errors should first be retried once with a stricter prompt:
`Return exactly one JSON object with key "{surface}"; no explanation.` Retry
results override first-pass results for the same item ID. Any remaining parse
error after retry routes the target or unit to focused review.

Scope triage is intentionally ordered before yomi generation. It only needs raw
unit text, and early `Skip` decisions avoid spending Sudachi, decoder, safety,
and yomi-reading LLM work on non-target units. Later yomi stages consume the
scope-triaged artifact and ignore `Skip` rows.

In this pipeline, agreement is operational rather than absolute. LLM/mechanical
agreement does not mean "guaranteed correct forever." It means the target or
unit may be low-risk enough for high-throughput audit or future auto-acceptance
experiments. The source of every low-risk signal must remain visible, for
example N-gram support, stable two-kanji support, LLM reading agreement, or
human approval, so later audits can measure false-accept rates separately by
source.

#### Per-target safety evidence

Whole-unit auto-accept is useful as a conservative experiment, but the more
scalable design is per-target evidence. Each yomi-bearing target in the
Sudachi/hybrid token stream should get a safety record. The LLM is queried only
for targets that deterministic evidence cannot make low-risk. Human review then
sees unresolved targets highlighted inside the unit instead of receiving only a
coarse sentence-level `OK` or `Review`.

Candidate safety signals:

- stable dictionary evidence: the target has one trusted dictionary reading,
  such as a stable two-kanji raw SudachiDict item, and the current reading
  matches it
- corpus-frequency evidence: a trusted decoder/training-corpus stats artifact
  shows that one reading dominates the surface, for example >=99.5% with a
  minimum count threshold
- repeated N-gram evidence: the local reading is supported by repeated N-gram
  support, not a one-off transition
- LLM agreement: an independent per-target reading query returns the same
  reading as the mechanical reading
- unit auto-accept evidence: a legacy whole-unit auto-accept decision can mark
  every target in that unit low-risk, but it should remain a distinct signal so
  later audits can separate it from per-target evidence
- unresolved evidence: no signal applies, the LLM disagrees, or the LLM output
  is missing/malformed

The unit-level status is derived from target-level evidence. If scope triage
returns `Skip`, the entire unit is excluded. Otherwise, a unit with no
unresolved targets can enter bulk review or later auto-accept experiments. A
unit with unresolved targets stays reviewable, and the UI should highlight the
specific targets. Highlight intensity can reflect evidence strength: a
dictionary-plus-corpus target can be visually quiet, while LLM-only agreement
or unresolved targets can be more visible.

These safety records are risk labels, not truth labels. Store the full evidence
source and parameters: signal name, counts, threshold, N-gram order/count,
dictionary source, LLM model/profile, and artifact versions. This lets later
audits measure which evidence source caused false accepts and retune thresholds
without losing provenance.

Corpus-frequency evidence is probabilistic and corpus-dependent. A surface such
as `大麻` may be overwhelmingly observed as `タイマ` in the evidence corpus, while
a rare place-name reading such as `おおあさ` remains possible. That is an
accepted residual risk for bulk review/de-emphasis: the signal says "low-risk
under this corpus and policy," not "lexically impossible to read otherwise." If
audits show repeated misses of rare proper-noun readings, add targeted
exceptions or lower the confidence/highlight level for those surfaces.

Concrete per-target safety records should be versioned data, not just UI state.
A passing corpus-frequency record:

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

A failing corpus-frequency record should still keep the measured signal:

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

The `方` example is a useful negative control: the schema can record the corpus
counts, but policy must not mark it safe because the surface has genuinely split
readings and the dominant share is far below 99.5%.

Minimum fields:

- alignment: `target_id`, `surface`, `token_surface`, `target_start`,
  `target_end`, `token_index`, `chunk_index`
- mechanical reading: `mechanical_reading`,
  `mechanical_reading_hiragana`
- summary: `is_safe`, `review_status`, `highlight_level`,
  `accepted_signal_names`, `status_reason`
- evidence list: `signals[]`, with per-signal counts, thresholds, artifact
  versions, N-gram details, dictionary source, or LLM model/profile

Initial controlled values:

- `review_status`: `safe`, `unresolved`, `skipped`
- `highlight_level`: `none`, `weak`, `strong`

Unit-level fields such as `all_targets_safe`, `unresolved_target_count`,
`safe_signal_counts`, and `safety_policy_version` should be derived from the
target records. Do not collapse evidence into one irreversible boolean.
`status_reason` should stay a short derived explanation, for example
`accepted_by_corpus_frequency`, `accepted_by_llm_agreement`,
`no_accepted_safety_signal`, `llm_disagreement`, or `llm_parse_error`.

Implementation sequence:

1. Generate/load corpus-frequency stats from a configured source corpus path,
   initially `/panfs/panmt22/users/hmanabe/yomi-decoder/data/raw/core_SUW_yomi_final.txt`.
   The generator writes both stats and a manifest; tests use small committed
   fixtures.
2. Add a yomi safety module that builds per-target records using the same
   target IDs and alignment logic as LLM reading queue items.
3. Add deterministic signals first: stable dictionary and corpus frequency.
   Add N-gram target safety after the decoder-entry-to-target mapping is
   auditable.
4. Change yomi-reading queue construction to queue only unresolved, non-skipped
   targets from the pre-LLM safety artifact. The comparison baseline is the
   current hybrid rendered reading when the rendered token stream aligns with
   Sudachi tokens; raw Sudachi readings are only a fallback. This avoids false
   LLM mismatches such as Sudachi `方/ほう` versus hybrid `方/かた`.
5. Merge LLM results back into final safety records. LLM/hybrid agreement adds
   `safe_by_llm_match`; disagreement or malformed output leaves the target
   unresolved. For an unresolved disagreement with a valid LLM reading, final
   review should default to the LLM candidate while visibly keeping the target
   reviewable. Legacy whole-unit auto-accept decisions should be projected into
   target records as `safe_by_unit_auto_accept`, not left only as a unit-level
   flag.
6. Produce explicit artifacts and summaries:
   - `units.yomi.safety_pre_llm.jsonl`
   - `yomi_reading_input.jsonl`
   - `units.yomi.llm_readings.jsonl`, containing the merged post-LLM target
     safety records until a separate final safety artifact is needed
   - counts by safety signal, queued LLM targets, LLM agreement/disagreement,
     parse errors, and unresolved targets
7. Add review/debug export that highlights targets by `highlight_level`.
8. Keep the old whole-unit auto-accept path as legacy/debug until per-target
   safety has enough validation to replace it.

#### Corpus-frequency evidence interface

Corpus-frequency evidence should be consumed through a stable stats artifact,
not by reading decoder-internal runtime files directly during the main
pipeline. That artifact may be generated by the decoder, or generated inside
this project from a configured source/training corpus. For current
experimentation, generating the stats here is often preferable because the
normalization, filtering, and threshold policy can be aligned with this
pipeline and changed without requiring a decoder-side export.

A suitable stats artifact could be TSV or JSONL with one row per
`(surface, reading)` pair:

```text
surface  reading  count  surface_total_count  share  source_corpus_version
```

Optional fields such as `exported_at`, `source_corpus_path_or_id`,
`decoder_version`, and `normalization_version` are useful for reproducibility.
This project should load that artifact through a small evidence loader, cache
it for a pipeline run, and answer questions like: "Does this surface have a
dominant trusted reading above the configured threshold, and does the current
reading match it?"

If this project generates the stats, keep the source corpus path configurable
and allow the full corpus to live outside git. The source-to-stats command
should be deterministic and should write both the stats artifact and a manifest
recording the input corpus path or ID, corpus checksum when feasible,
normalization settings, filters, script version, and generation time.

Do not treat high share alone as sufficient. The rule should require both a
share threshold and a minimum count, because `1/1 = 100%` is weak evidence. The
normalization policy must also be explicit: old/new forms, Latin width, kana,
symbols, and `々` handling need to match or be recorded.

The initial default is `min_count = 5` and `min_share = 0.995`. Count-5 boundary
samples looked acceptable for this signal's intended role: de-emphasizing
low-risk targets while preserving auditability and bulk review visibility.

Changing the source/training corpus changes safety decisions. Every pipeline
output using corpus-frequency evidence must record the evidence artifact path
or ID, `source_corpus_version`, threshold, and minimum count. Full corpora and
large evidence files may live outside git if they are large; tests should use
small committed fixture corpora and fixture stats that exercise the same
generator and loader.

#### Resumable LLM execution

LLM execution should be handled by a generic resumable job layer rather than by
adding separate queued/submitted/completed pipeline stages for each LLM-using
task. The domain pipeline stage starts or resumes an LLM job; the LLM job owns
request files, result files, remote job IDs, progress, retry state, and logs.

The same job interface should support sync, OpenAI Responses background mode,
and OpenAI Batch mode:

- `sync`: process rows sequentially or with small concurrency, append each
  completed result, and skip already completed item IDs when resumed
- `background`: submit one Responses API background request per item, persist
  each response ID, poll response objects until completion or interruption by
  default, append completed results as they become available, and avoid
  resubmitting item IDs that already have a response ID or parsed result
- `batch`: submit the remaining rows as one or more remote batch jobs, persist
  remote batch IDs, poll at a slow interval until completion or interruption,
  fetch result files when complete, and resume from stored chunk state on later
  runs

All modes should report progress as completed items over total items. Sync mode
can update progress after each response. Background mode should poll stored
Responses API response IDs; completed responses can be parsed and appended
immediately. Batch mode should poll each remote OpenAI Batch object and use its
`request_counts` (`completed`, `failed`, `total`) as the progress source while
the server-side job is running. Batch output files are still available only
after completion, so final result parsing must continue to use downloaded output
files and each request's `custom_id`.

The domain stage should complete only after the job has produced a complete
result JSONL and those results have been applied to the domain artifact. For
scope triage or yomi reading generation, that means the job can be running
while the domain step is still active, and the stage advances only after the
scope artifact or yomi-reading comparison artifact is written.

Interruptions should be normal. The operator may stop sync mode partway through;
rerunning `./next` resumes from result rows already present. For background
mode, `./next` submits any missing Responses background requests, polls stored
response IDs roughly once per minute by default, and applies results once all
responses have completed. For batch mode, `./next` submits any missing remote
chunks, polls roughly once per minute by default, reports aggregate
`request_counts` when available, and applies results once all chunks have
completed and been fetched. If either remote mode is interrupted, rerunning
`./next` continues from stored local job state.

OpenAI API constraints that affect this design:

- Responses background mode stores response data only for a limited polling
  window, documented as roughly 10 minutes. It is useful for medium interactive
  runs such as about 150 requests, but local polling should not be postponed
  indefinitely.
- A single Batch API input file may contain up to 50,000 requests and may be up
  to 200 MB.
- Batch API also has a per-model enqueued prompt-token limit. Pending batch
  input tokens count against that queue limit until the batch completes.
- Batch creation is rate-limited, documented as up to 2,000 batches per hour.
- Batch jobs can expire if they do not complete within the completion window;
  completed requests remain available and unfinished requests appear as errors.

Current implementation: batch mode keeps one logical pipeline LLM job, but may
split requests into multiple remote OpenAI batch jobs. The task-config knob is
`batch_max_requests_per_batch`; the default preserves single-batch behavior up
to OpenAI's documented request limit, while tests and smoke runs can set a small
value to exercise multi-batch behavior. A future size-based guard such as
`max_input_file_mb` may still be useful before very large production runs.

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
- `llm_execution_policy`: whether each task runs through sync calls, Responses
  background mode, or OpenAI Batch

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

Initial allowed values:

- `unit_mode`: `sentence`, `comma_span`
- `auto_accept_profile`: `off`, `strict`, `stable_two_kanji`
- LLM profiles: `smoke`, `economy`, `standard`, `strong`
- LLM execution modes: `sync`, `background`, `batch`

Suggested yomi defaults are `working={unit_mode=sentence,
auto_accept_profile=strict}` and
`dev={unit_mode=sentence,auto_accept_profile=stable_two_kanji}` for now. Track
defaults should also choose an LLM profile per task. Operators should be able to
override these per batch, so dev can run with no auto-accept, working can later
run with stable two-kanji auto-accept, and either track can use cheaper or
stronger model profiles for specific tasks. `yomi_reading` should default to
`standard` even on dev, because mini-model reading errors create noisy false
problems and can distort prompt/pipeline design. Execution mode should be
equally configurable per task. `background` should be the normal default for both
`dev` and `working`, because it avoids slow sequential calls while still
allowing `./next` polling/resume. Prompt exploration, smoke tests, and tiny
repair batches are often easier in `sync`; very large low-urgency tasks with
acceptable latency are better in `batch`. The selected explicit values must be
stored with the batch for reproducibility.

These defaults should not remain hidden Python constants. They should be moved
to a small source-controlled project config, for example:

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
yomi_repair = "standard"
yomi_rescue = "standard"

[tracks.dev.llm_execution_policy]
alphabetic_entity_judge = "background"
scope_triage = "background"
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"
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

LLM-facing yomi text may be more natural than the stored rendered annotation.
The stored `surface/reading` token sequence remains the source of truth for
storage, auditing, deterministic replacement, and N-gram feedback, but prompts
and human review UIs may use derived display forms.

The preferred display candidate is no-space furigana-style text. It should
render token readings as inline parenthesized kana and then concatenate tokens
without token spaces, for example:

```text
荷物（にもつ）を送（おく）って結果（けっか）を待（ま）ちます。
```

This is the most familiar Japanese reading-annotation form and should be easier
for both humans and LLMs to judge than whitespace-separated token pairs. It also
hides irrelevant segmentation artifacts such as katakana splits (`ラミン トン`)
when segmentation repair is out of scope for the current task.

Raw corpus text often contains both full-width and half-width parentheses, so
LLM-facing no-space furigana must reserve full-width `（...）` for yomi
annotations only. Before rendering yomi for an LLM prompt, escape original
source parentheses as a reversible display-layer transform:

- source `（` -> `-LRB-`
- source `）` -> `-RRB-`
- source `(` -> `-lrb-`
- source `)` -> `-rrb-`

For example, raw `荷物を送って、（明日）届く。` may be displayed as
`荷物（にもつ）を送（おく）って、-LRB-明日-RRB-届（とど）く。`.
The stored source text and canonical token sequence remain unescaped. Escapes
are applied only to human/LLM display text and should be restored or validated
before any model proposal is applied.

No-space display should also preserve the distinction between ordinary numeric
tokens and rare fused numeric yomi tokens. If a digit-containing surface is one
canonical yomi-bearing token, prefix the displayed token with `|`. For example,
canonical `1人/ヒトリ` displays as `|1人（ひとり）`, while ordinary separated
tokens `1/ 人/ニン` display as `1人（にん）`. The marker is not source text; it
only tells the LLM that the digit participates in the annotated token.

The furigana renderer should be dictionary-backed when possible. The Sudachi
derived CSV can map `(surface, reading)` to annotated surfaces such as
`読（よ）み仮名（がな）` or `送（おく）っ`; this avoids heuristic reconstruction
for many inflected forms. If no unique dictionary-backed form exists, the
renderer can fall back to a simple token-level representation or keep the
stored `surface/reading` pair for that token.

This projection is deliberately separate from the accepted corpus format. The
canonical data remains `surface/reading` tokens; furigana text is a derived UI,
LLM, or export view. After a document/unit is accepted, persist any
non-dictionary or inferred furigana projection that the reviewer effectively
accepted. The metadata should include the surface, normalized reading, accepted
annotated form, converter method/confidence, source such as
`human_accepted_review_ui`, dictionary version, and batch/unit IDs. Future UI
rendering can prefer this accepted projection cache before falling back to the
base dictionary/scored converter, and repeated accepted projections can later be
promoted into the annotated-form dictionary. The cache is evidence and display
support, not a replacement for canonical `surface/reading`.

Whitespace-separated views still have a role:

- full token view: `送っ/オクッ て/テ`
- compact token view: `送っ/オクッ て`
- furigana spaced debug view: `送（おく）っ て`
- furigana no-space prompt view: `送（おく）って`

Use plain marked source text for per-target LLM reading generation by default:
only the requested target is marked, and the rest of the sentence is unannotated
context. A 150-item mini-model comparison showed similar match counts to
no-space furigana context with materially fewer input tokens, so the simpler
view is the default until larger experiments say otherwise.

Use no-space furigana for LLM triage/proposal and human bulk review experiments
where the existing yomi annotation itself is what the model or reviewer must
judge. Use spaced token views for debugging, alignment inspection,
deterministic repair rules, and N-gram decoder training data, where token
boundaries are useful.

Because some corrections cross token boundaries, the repair/proposal prompt
should allow local spans that include neighboring kana or symbols. For example,
`外出/ガイシュツ て` may need to become `外/ソト 出/デ て`. Proposed `from` spans
must be aligned back to the full stored annotation before application. Display
strings, including no-space furigana, are views and should not be blindly
string-replaced as canonical data. Automatic application should require one
unique valid span in the stored token sequence; ambiguous or non-matching
proposals stay in human review.

Implementation plan:

- Implement yomi display renderers as shared prompt-rendering filters, not as
  task-specific prompt text. Supported display modes should include at least
  `full`, `compact`, and `furigana_no_space`; a spaced furigana debug mode is
  also useful.
- Keep full rendered yomi in all unit artifacts. Derived display text is
  computed at prompt-build time or stored as explicit debug metadata only.
- Enable plain marked source text first for `yomi_reading`.
- Enable no-space furigana first for LLM judgment/proposal tasks that inspect
  existing yomi annotations: yomi triage, review-resolution/local-fix proposal,
  and possibly yomi check.
- Keep full rendered yomi available for prompts or tools that need exact token
  boundaries until span alignment and expansion are implemented for furigana
  display.
- Add a task-level configuration flag so experiments can compare full and
  compact/furigana display.
- For per-target reading generation (`yomi_reading`), prefer an ultra-short
  pattern prompt unless later tests show a regression:
  `目が**痛**い。->{"痛":"いた"}\n{marked_text}->`. A GPT-5.5 test on 150
  current-format items produced no parse errors and materially lower input
  token use than the older explanatory prompt. Keep target extraction strict:
  iteration-mark words such as `日々` must be queued and evaluated as the whole
  readable unit, not as `日` alone.
- When a prompt receives a derived display, it must state the display policy.
  For compact display, bare kana, symbols, and numbers are intentional
  abbreviations. For furigana display, absence of token spaces is intentional
  and should not be treated as evidence that segmentation was fixed.
- For proposal tasks, parse the model's local `from`/`to` spans as proposals.
  Align `from` against the full stored token sequence. Apply only unique valid
  spans; otherwise escalate to human review.

Before repair, the pipeline may run a lightweight router over units that first
triage labeled `Review`. This router separates operational causes:

- `Fix`: concrete yomi repair is possible from the available unit/context
- `Ambiguous`: target Japanese, but no safe correction can be chosen from the
  available context
- `OK`: first-stage triage was a false positive; the unit can move to bulk
  audit
- `Skip`: first-stage triage missed non-target text

This router is an operational step, not the conceptual gold taxonomy. Gold data
should use `OK`, `Fix`, `Ambiguous`, and `Skip`; first-stage `Review` is the
collapsed production label for both `Fix` and `Ambiguous`, plus possible
first-stage false positives.

Experiment note, 2026-05-18: direct 4-way triage was tested against a
two-stage `OK/Review/Skip -> Fix/Ambiguous/OK/Skip` route on the small yomi
triage eval set. With `gpt-5.4-mini`, the end-to-end two-stage route scored
47/60 and direct 4-way scored 36/60. With `gpt-5.5`, the two-stage route scored
49/60 and direct 4-way scored 45/60. The `gpt-5.5` router could emit
`Ambiguous` when run only on gold Review rows, but the first 3-way prompt sent
all conceptual `Ambiguous` rows to `OK`, so end-to-end recall for ambiguity was
still zero. Production should therefore keep the first triage label set at
`OK/Review/Skip`; conceptual `Ambiguous` remains useful for gold data and
later repair/proposal-stage evaluation, not as a first-triage requirement.

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

Minor alphabetic review should not be a separate routine human surface. Entity
judgments are cached and projected to provisional unit skip; the final
sentence/unit review UI is where a human restores wrongly skipped units.

### S60 Rule Harvesting

Output:

- candidate reusable rules derived from reviewed cases
- candidate human overrides for alphabetic entity types

Responsibilities:

- propose non-target triggers
- propose minor-alphabetic entity status updates when human review contradicts
  provisional skip
- keep yomi repair rules separate from classification lists

This should remain conservative and is still an open design area.

### S62 Alphabetic Entity Judgment

Output:

- cached LLM judgments for unresolved alphabetic entity types
- unit-level provisional skip reasons derived from effective entity status

Responsibilities:

- read the unresolved entity-type report produced after alphabetic extraction
- ask the LLM once per unresolved entity type, using a few short batch examples
  as context
- judge whether the entity is naturally usable in modern Japanese context or is
  obscure/foreign/noisy enough to skip
- store the answer in the cross-batch alphabetic judgment cache
- do not ask again for the same effective entity key unless the cache is
  explicitly invalidated or superseded
- mark a unit as provisional skip when any effective entity status is
  `out_of_scope`
- preserve raw model output and usage metadata for later audit

The LLM answer is provisional policy, not a final corpus decision. It may cause
a unit to start with `Skip` checked, but that unit remains visible in final
review. If the human unchecks `Skip` for a provisional alphabetic skip, the
triggering `out_of_scope` entities become effective `in_scope` entries from then
on. If the human leaves the unit skipped, or manually skips a unit that was not
provisionally skipped, entity status does not change.

The cache should preserve source metadata such as `static_whitelist`,
`static_blacklist`, `llm`, or `human_unskip`, but ordinary behavior should not
depend on the source. The effective status is just `in_scope`, `out_of_scope`,
or `unknown`.

### S65 Provisional Skip Review

Output:

- final review records containing the displayed `Skip` checkbox state
- optional human overrides for alphabetic entities when provisional skip is
  restored

Responsibilities:

- show provisional alphabetic skip units greyed out with `Skip` pre-checked
- show concise skip reasons such as the triggering entity key and cached status
- let the human uncheck `Skip` to restore the unit
- write effective `in_scope` overrides for triggering `out_of_scope` entities
  only when the human restores a provisional alphabetic skip

Asymmetric update rule:

- human keeps a provisional skip checked: no entity-level change
- human checks `Skip` on a normal unit: no entity-level change
- human unchecks a provisional alphabetic skip: triggering entities become
  effective `in_scope`

Rationale:

- final review is already required for yomi quality, so skip correction should
  piggyback on the same UI
- provisional skips reduce downstream cost without requiring a separate
  promotion-candidate review loop
- source-aware audit records remain available if the LLM cache proves noisy

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
- treat single alphabetic letters as deterministic low-value exceptions rather
  than whitelist entries; they should be extracted for auditability but should
  not be sent to the alphabetic LLM judge
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
- `scope_triage`: `gpt-5.4-mini` through the `economy` profile unless evals
  justify a stronger model
- `yomi_reading`: `gpt-5.5` for production-quality reading comparison,
  `gpt-5.4-mini` for dev flow checks
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
- `./prepare --yomi-unit-mode comma_span --yomi-auto-accept-profile off --llm-profile yomi_reading=smoke dev 10`
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
- the following `./next` should judge unresolved alphabetic entity types with
  the configured LLM mode and update the cached effective entity status
- the following `./next` should project cached alphabetic status back to units
  as provisional skip reasons before general scope/yomi processing
- the following `./next` should queue raw-text scope triage
- the following `./next` should run or resume scope triage and write
  `units.scope_triaged.jsonl`
- the following `./next` should build the mechanical yomi JSONL
- the following `./next` should add the yomi auto-accept artifact
- the following `./next` should build per-target safety evidence and queue LLM
  readings for unresolved kanji/Latin targets
- the following `./next` should run or resume the yomi-reading LLM task
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

For yomi review, use the same transport model as alphabetic review: the cluster
writes a review-pack JSON file, GitHub Pages displays it, the browser keeps
local draft state, and the reviewer returns exported JSON through GitHub
Issues/comments/attachments. The page should default to the latest review pack
but keep older packs readable as immutable history.

The review pack should be a flat, continuous list grouped by visible document
separators rather than a hard document-switching UI. Each item still carries
`doc_id`, `unit_id`, and stable local/global indexes. This keeps review and
range export simple while preserving enough metadata to add document tabs or a
sidebar later. Document boundaries should be visually clear, for example:

```text
Document 3 / ja_cc_level2:0000000004
```

The first final-yomi review UI should be ruby-first and look like normal text,
not like a table of pipeline metadata. For each sentence/unit, show only:

- ruby-rendered text
- a `Skip` checkbox
- a compact `...` menu for range marks such as `from here` and `to here`

Do not show document IDs, unit IDs, target IDs, or signal details in the normal
view. Keep them in the review pack for audit/debugging, but hide them from the
main workflow.

Yomi targets are edited inline by tapping/clicking the ruby span, not by using
dropdowns. Unresolved targets should have a visible highlight. Safe targets
should not be visually noisy, but they may still be tappable if the pack has
useful candidate readings. A tap cycles through candidates, for example:

```text
近々(きんきん) -> 近々(ちかぢか) -> 近々 -> 近々(きんきん)
```

The candidate list should be derived from recorded evidence:

- current mechanical/hybrid reading
- LLM reading when it differs
- corpus-frequency dominant reading when available
- stable dictionary reading when available
- no-ruby / remove reading

Changed spans should use a separate color from unresolved highlights. Removing
the ruby or choosing an available alternate reading is a span-level override,
not a whole-sentence rejection.

No-ruby is the normal way to request strong-model handling. If consecutive
targets are canceled in the same sentence, group them into one repair span. Do
not ask the human reviewer to decide whether web search is needed. The
strong-repair prompt/model should make that decision from the local target
context, rejected readings, and entity-like cues, and should record whether web
search was actually used.

Whole-sentence escalation should be reserved for a future advanced fallback if
real examples require it. Strong-model handling must be a separate later stage,
and its output should return to a final confirmation UI.

The final confirmation UI for strong-model outputs should be different from
the quick review UI. It should show ruby-rendered text and also expose raw
editable structured data, so a human can directly correct the result before it
enters the final corpus.

### 10.2 Review state model

Separate three things:

- immutable review pack
- device-local draft state in the browser
- append-only review submissions returned through GitHub

The browser should persist local drafts by `review_stage` and `pack_id` so a
reviewer can leave the page and return later.

### 10.3 Reviewed ranges and overrides

For review export, a reviewed range matters more than explicit marks on every
approved item.

Recommended behavior:

- by default, the whole pack is in scope for export
- optional `from` / `to` markers narrow the reviewed range
- items outside that range remain visible but faded
- within the reviewed range, no explicit mark means "accept the proposed
  action"
- the submission payload should therefore contain reviewed range metadata plus
  sparse item-level overrides

### 10.4 Yomi Audit Queues

Yomi review should distinguish queue semantics from UI widgets. The same
checkbox-style UI can serve both high-throughput audit and focused review if
the queue context is explicit.

Recommended yomi queues:

- `bulk_ok_audit`: units with automatic `OK`, including mechanical and LLM
  `OK`; the reviewer scans quickly and marks only suspicious/problematic
  sentences
- `focused_review`: units that remained `Review` after triage and repair; the
  reviewer inspects carefully and accepts only after resolving the issue
- `skip_audit`: optional sampling of `Skip` units to check non-target decisions

The checkbox can be identical in both main queues: "problem found" or "needs
attention." The meaning of an unchecked item depends on queue context:

- in `bulk_ok_audit`, unchecked means accepted by default after fast scanning
- in `focused_review`, unchecked means accepted after focused inspection
- checked means the sentence should go to correction or remain unresolved

This keeps the UI simple while still changing the reviewer's posture. `OK`
queues are designed for anomaly detection over many items; `Review` queues are
designed for deliberate sentence-by-sentence validation.

### 10.5 Multiple partial submissions

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

### 10.6 Current yomi final-review application path

After `final_review_prepared`, the concrete pipeline stages are:

- `final_review_applied`
- `yomi_strong_repair_queued`
- `yomi_finalized`

`final_review_applied` consumes local JSON submissions under
`data/review_submissions/yomi_final/`. These are the files exported by the
GitHub Pages review UI or imported from GitHub Issues by a separate ingestion
step. The replay semantics match the review UI:

- reviewed ranges define coverage
- no explicit override inside a reviewed range means accept
- sparse overrides can set sentence skip or target-level reading choices
- target-level `No ruby` means the current reading was rejected and should be
  grouped with adjacent canceled targets for focused strong repair
- sentence skip dominates operational processing; target choices on skipped
  rows are retained as audit data but do not update rendered yomi or trigger
  strong repair
- later overlapping submissions overwrite earlier ones

At `final_review_applied`, `./next` first runs the open-Issue ingestion path and
then applies the local submission store. The importer is non-fatal: network
errors are recorded in the import summary, after which the stage behaves as if
no new submissions were imported.

Manual ingestion commands remain available for debugging:

```bash
python scripts/import_yomi_final_review_issue.py --issue-number 1
python scripts/import_yomi_final_review_inbox.py
```

The old alphabetic Issue importer is intentionally retired. Alphabetic
classification remains cached as provisional evidence, but final human
overrides are collected through the yomi final-review path.

If no matching submission exists, the stage blocks as a human review gate.

`yomi_strong_repair_queued` writes a target-group queue for cases that need the
stronger repair model:

- consecutive target-level `No ruby` overrides as `repair_scope:
  "target_group"` and `repair_order: 1`

The queue row should carry the grouped targets, any human-selected local
constraints, and enough sentence context for the strong model to decide whether
web search is needed. Sentence-level escalation is legacy/fallback plumbing, not
the preferred path.

Canceled target readings should carry `rejected_readings` in the strong queue.
That gives the strong/web repair prompt negative evidence such as
`史輝` is not `ふみてる` in a publisher-name context, or `元` is not `もと`
inside `真光元`.

`yomi_strong_repair_llm_completed` runs `config/llm/yomi_repair.toml` on the
queue and applies valid target-group repairs to:

```text
data/units/<batch>/units.yomi.strong_repaired.jsonl
```

The applier is deliberately narrow. It accepts only valid parsed JSON arrays
whose concatenated surfaces exactly match the rejected span, and it converts
readings to the pipeline's katakana `surface/READING` representation. Missing
results, parse errors, sentence-scope repairs, invalid readings, or surface
mismatches remain blocking.

Future work: after a strong repair is accepted in final confirmation, promote
the repaired surface span into an exact learned default for later batches. This
is useful for multi-token or boundary-crossing repairs such as `一発` becoming
`いっぱつ` or `池尻中学校` becoming `いけじり ちゅうがっこう`. Promotion should
use the whole confirmed surface span by default, not infer a broader regex or
subspan rule unless separately approved.

The strong-repair confirmation UI can also accept a human-edited segmentation
for the rejected local span. The intended first UI is a boundary-toggle editor:
characters inside the rejected span are joined with `=` and split with `/`, and
one reading input is shown for each resulting segment. The submitted
`manual_segments` remain canonical `surface/reading` data. They override the
LLM repair only after validation confirms that segment surfaces concatenate
exactly to the rejected span and readings are valid kana.

`yomi_finalized` consumes `units.yomi.strong_repaired.jsonl` when it exists, but
strong repair results are still candidates. If the strong queue is non-empty,
finalization blocks until a later human confirmation stage marks the strong
repair apply summary as confirmed. Missing or incomplete strong-repair apply
summaries also block finalization.

`yomi_finalized` writes the no-escalation final output when the strong queue is
empty. If the queue is non-empty, it blocks rather than pretending the batch is
done.

`./next --auto` may be used to advance repeatedly through non-human stages. It
stops on human gates, incomplete stages, confirmation requirements, blocking
errors, or final completion. Single-step `./next` remains the debugging default.

### 10.7 Decoder model refresh as maintenance

Refreshing the `yomi-decoder` language model should not be a standard per-batch
stage. It should be an explicit maintenance workflow run after one or more
batches have been finalized.

Division of responsibility:

- `yomi-corpus` exports reviewed additions in a stable decoder-training format
- `yomi-decoder` accepts base corpus plus one or more extra reviewed corpora
- `yomi-decoder` writes model artifacts to a caller-specified output directory
- `yomi-corpus` records and selects the decoder model used for later batches

Track-scoped model policy:

- keep `dev` and `working` decoder additions separate by default
- `dev` refreshes may be frequent and experimental
- `working` refreshes should use only reviewed working material, or dev material
  that has been explicitly promoted
- each track records its latest decoder model path
- each new batch copies the track's latest model path into its own batch state
- a running batch never changes decoder model implicitly

Recommended artifact layout:

```text
data/decoder_corpora/dev/<batch>.txt
data/decoder_corpora/working/<batch>.txt
data/decoder_models/dev/<model-id>/
data/decoder_models/working/<model-id>/
```

The low-level shape is:

```bash
python /path/yomi-corpus/scripts/export_decoder_corpus.py \
  --batch reviewed_batch_name

python /path/yomi-decoder/scripts/build_model.py \
  --base-corpus /path/core_SUW_yomi_final.txt \
  --extra-corpus /path/yomi-corpus/data/exports/decoder_corpus/reviewed_batch_name.txt \
  --output-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD
```

and later:

```bash
python /path/yomi-decoder/scripts/decode.py \
  --model-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD \
  ...
```

The operator-facing refresh command is:

```bash
python scripts/refresh_decoder_model.py --track dev
python scripts/refresh_decoder_model.py --track working
```

It exports finalized corpora for that track, builds a new decoder model, and
updates that track's latest decoder model pointer. New batches copy that
pointer into their batch manifest/state for reproducibility.

Batch manifests should record enough information to reproduce decoder behavior:

- decoder executable/version
- base corpus version
- extra corpus input manifest
- model directory or model ID
- build timestamp and build parameters

This keeps model refreshes as deliberate experiment boundaries rather than
implicit side effects of finishing a batch.


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


## 12. Immediate Next Steps

The skeleton, unit extraction, mechanical yomi generation, LLM execution modes,
review-pack generation, and GitHub-Issue review import are now implemented.
Current work should focus on closing the real review loop and then making later
batches benefit from reviewed data:

1. regenerate any active review packs after candidate-selection changes
2. import and apply final-review submissions for the active dev batch
3. finalize batches that have no strong-repair queue
4. implement the real `yomi_strong_repair` stage for canceled ruby target groups,
   with model-side web-search judgment when context is insufficient
5. harvest accepted repairs into conservative learned default rules
6. feed human skip/unskip decisions back into alphabetic token decisions where
   appropriate
7. export finalized yomi into the decoder supplemental corpus and refresh the
   track-local decoder model
8. repeat small dev batches until the process is stable enough to set strict
   `working` defaults
