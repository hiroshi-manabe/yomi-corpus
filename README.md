# yomi-corpus

This repository is the orchestration and governance layer for building a large
Japanese corpus with readings.

It is intentionally separate from:

- `../llm-jp-corpus-v4/`, which prepares the filtered source corpus
- `../yomi-decoder/`, which provides an N-gram reading decoder

The first design draft lives here:

- [docs/PIPELINE_DESIGN.md](/panfs/panmt22/users/hmanabe/yomi-corpus/docs/PIPELINE_DESIGN.md)
- [docs/WORKING_PIPELINE.md](/panfs/panmt22/users/hmanabe/yomi-corpus/docs/WORKING_PIPELINE.md)
- Review UI (GitHub Pages): https://hiroshi-manabe.github.io/yomi-corpus/

Initial project stance:

- Keep source records immutable.
- Treat this repo as a staged pipeline, not a grab bag of scripts.
- Separate "confidence", "repairability", "modern-Japanese status", and
  "human-reviewed status" instead of collapsing everything into one flag.
- For non-target detection and mechanical "safe" decisions, collect
  raw features first and defer real deterministic gating until reviewed data
  exists.
- Use cheap LLM triage, then more expensive contextual repair, then human
  review.

Prompt iteration scaffold:

- Prompt optimization is a separate pre-production phase, not something to
  do continuously inside corpus batch progression.
- Fixed eval sets live under `data/evals/<task>/`.
- Experiment runs live under `runs/experiments/<task>/<run_name>/` or another
  explicit run directory.
- Each run writes `items.jsonl`, `results.raw.jsonl`, `results.parsed.jsonl`,
  `scored.jsonl`, `summary.json`, and a prompt snapshot.
- Use `scripts/run_prompt_experiment.py` to run one prompt version on an eval
  set. The default is sync mode; add `--llm-mode background` for resumable
  Responses background runs, or `--llm-mode batch` for Batch API runs.
- Use `scripts/compare_prompt_experiments.py` to compare two runs and inspect
  changed failures.
- Exploratory prompt search should usually use sync or background Responses API
  calls, not the Batch API, so failures can be inspected and prompts can be
  revised quickly. Background mode is preferred when many independent requests
  would make sequential sync execution slow.
- For `yomi_triage`, first sweep prompt families and `gpt-5.4-mini` reasoning
  effort settings for both accuracy and cost; promote only the strongest prompt
  candidates to `gpt-5.5` for production-quality evaluation.
- For cache-sensitive prompt tuning, use the Responses `input_tokens` endpoint
  for exact GPT-5-family input counts. Local `tiktoken` estimates are fine for
  rough work, but the API count is the source of truth when aiming just over
  the 1024-token prompt-cache threshold.
- Freeze a prompt version before large-scale corpus processing; production
  runs should use only small regression checks unless a deliberate prompt
  upgrade is being evaluated.

Default model policy:

- use `gpt-5.5` for normal judgment and repair tasks
- reserve `gpt-5.5-pro` for a tiny last-resort rescue tail
- use `gpt-5.4-nano` only for plumbing and instrumentation checks
- treat `gpt-5.4-mini` as opt-in per task, not the default path
- batches should store an explicit `llm_policy` task-to-profile map, separate
  from yomi-specific policy
- initial LLM profiles are capability/cost tiers: `smoke` (`gpt-5.4-nano`),
  `economy` (`gpt-5.4-mini`), `standard` (`gpt-5.5`), and `strong`
  (`gpt-5.5-pro`)
- track defaults should live in a small project config file rather than in
  Python code; at prepare time CLI explicit overrides should win over the
  configured track default, and later stages should use the stored batch policy

Alphabetic entity policy:

- unresolved Latin/alphanumeric entity types are judged once and cached globally
- cached judgments are operational: an `out_of_scope` entity causes provisional
  skip for units that contain it
- provisional skip is not deletion; final review shows the same `Skip` checkbox
  pre-checked and greyed so the human can restore the unit
- if a human restores a provisional alphabetic skip, the triggering entity is
  treated as `in_scope` from then on; human skip decisions do not change entity
  status
- judgment source is kept for audit/debug, but behavior only needs
  `in_scope`, `out_of_scope`, or `unknown`

Review transport policy:

- assume static browser UI rather than cluster-hosted writable UI
- keep the review UI in this same repository rather than a separate UI repo
- isolate the static review app in its own directory so it does not mix with
  the Python pipeline code
- GitHub Pages is the preferred first host for review HTML
- GitHub Issues are the preferred first mailbox for returned review submissions
- browser UI should persist local drafts and support partial range-based
  submissions
- a separate review-UI project is a long-term option after review-pack and
  submission schemas stabilize, but the current repo-local UI keeps iteration
  cheap while those contracts are still changing
- a long-term review-queue goal is large durable pipeline batches with smaller
  browser-selected work slices, imported from GitHub Issues and replayed into
  per-batch pending lists
- finalized batches harvest exact strong-repair rewrite defaults and
  supplemental furigana display entries under `data/lexicon/`

Pipeline orchestration policy:

- keep local pipeline state per batch and a current-batch pointer per track
- use `working` as the implicit default track and `dev` as an explicit secondary
  track
- `working` is the strict protected track; `dev` is the relaxed experimental
  track
- `./prepare 100` prepares the next working batch, while `./prepare dev 10`
  prepares the next dev batch; add `--yomi-unit-mode` or
  `--yomi-auto-accept-profile` to override yomi policy, and repeat
  `--llm-profile TASK=PROFILE` to override per-task LLM policy
- `./next` advances the current working batch by one implemented automatic
  stage; `./next dev` does the same for the dev track
- `./next --force-stage <stage>` reruns the current completed stage; on
  `working`, an overwrite prompt appears only if existing artifacts would
  actually be replaced
- `./next --llm-mode sync|background|batch` temporarily overrides the execution
  mode only when the stage being run calls the LLM
- `./status` and `./status dev` report the current batch and stage for each
  track
- `./status --stages` and `./status dev --stages` print only the completed
  current stage and the next stage without advancing the pipeline
- `./set-stage dev <stage>` rewinds the current dev batch stage without
  deleting artifacts; `working` requires confirmation or `--yes`
- `./publish-review` commits and pushes only generated review-page artifacts
  (`docs/review/manifest.json` and referenced review pack JSON files)
- treat OpenAI Batch waits and human-review waits as explicit pipeline states,
  not special cases

Yomi generation scaffold:

- deterministic generation now has a local harness under `src/yomi_corpus/yomi/`
- `scripts/generate_mechanical_yomi.py` runs Sudachi plus `../yomi-decoder/`
  over units and writes updated `analysis.mechanical.yomi`
- the current mechanical baseline uses Sudachi B-mode segmentation, then uses
  decoder evidence for supported reading overrides
- decoder-driven reading changes are allowed broadly when the exact decoder
  entry has real N-gram support; unigram-only fallback output should not
  override Sudachi
- the decoder-side definition of `piece_orders[0] >= 2` is expected to exclude
  singleton 2-grams; support means repeated corpus evidence, not a one-off
  transition
- when splitting one Sudachi token into multiple decoder entries, each later
  entry must start with cross-boundary support (`piece_orders[0] >= 2`), not
  merely gain support internally after an unsupported boundary
- stable two-kanji confidence experiments use the hybrid rendered tokens as
  decision units and project decoder evidence onto those spans; a two-kanji
  token is stable only if the raw SudachiDict CSV inventory has exactly one
  reading for that surface, including component-only entries with `-1,-1`
  connection IDs; proper nouns are allowed when their reading is unique
- yomi auto-accept behavior should be controlled by the per-batch
  `yomi_policy.auto_accept_profile`; track names only supply defaults
  (`working=strict`, `dev=stable_two_kanji` for now)
- yomi triage/repair work-item granularity should likewise be controlled by
  `yomi_policy.unit_mode`; supported values should start with `sentence` and
  `comma_span`, while the final corpus output remains sentence-level
- LLM model choice should be controlled by `llm_policy` rather than by
  hardcoded track checks inside the pipeline
- supported decoder overrides and safe auto-acceptance are separate decisions:
  a supported decoder/Sudachi disagreement may be used as a tentative
  correction because review is still the default, but a unit is only safe when
  Sudachi and decoder evidence agree under full-span support checks
- after the hybrid strategy, regex-based post-hybrid repair rules from
  `config/yomi/post_hybrid_repairs.tsv` may rewrite known systematic rendered
  yomi errors; each applied rule is logged under
  `analysis.mechanical.yomi.post_hybrid_repairs`
- numeric runs are grouped and emitted with an empty reading, such as `2021/`;
  number pronunciation is intentionally left to a future dedicated number
  reading module
- yomi quality is judged primarily by reading correctness, not ideal
  segmentation; over-split katakana or morphology is acceptable for now if the
  readings are correct
- after alphabetic checks, units with cached `out_of_scope` alphabetic entities
  are marked as provisional skip; raw-text scope triage still handles general
  non-target material before yomi generation
- after yomi generation, `yomi_auto_accepted` adds
  `analysis.mechanical.yomi.auto_accept` only for low-risk units where Sudachi
  and the decoder agree and the decoder candidate has full repeated N-gram
  support
- after scope triage and yomi auto-acceptance, the yomi-reading queue asks for
  independent LLM readings only for unresolved kanji/Latin targets that were
  not suppressed by deterministic safety evidence
- LLM work uses a generic resumable job layer shared by alphabetic judgment,
  yomi triage, yomi repair, and rescue repair; sync, background, and batch
  modes report completed/total progress and support interruption and resume
- `./next` should print concise operator-facing progress by default and write
  full structured summaries to pipeline logs; `./next --json` should keep the
  verbose machine-readable output
- yomi triage/repair should support both `sentence` and `comma_span` work-item
  modes; the final corpus artifact remains sentence-level in both modes, and in
  `comma_span` mode any `Skip` span excludes the whole parent sentence while
  `Review` spans proceed locally only if no sibling span is `Skip`
- `scripts/export_yomi_outputs.py` is the main operator helper for generating
  the normal pipeline artifact; it defaults to `aligned_hybrid` JSONL only
- `scripts/export_yomi_debug_compare.py` is the dedicated debug helper for
  producing side-by-side diff inputs under `<batch-dir>/debug/`
- `scripts/export_yomi_plaintext.py` is kept as a compatibility wrapper for the
  same debug comparison export
- `scripts/run_yomi_experiment.py` runs one named combination strategy on a
  fixed eval set
- `scripts/compare_yomi_experiments.py` compares two strategy runs
- current strategy names include `aligned_hybrid_v1`, `sudachi_only_v1`,
  `decoder_only_v1`, `agreement_prefer_decoder_v1`, and
  `agreement_prefer_sudachi_v1`
- for yomi evaluation, correct readings matter more than coarse vs. fine
  segmentation; over-segmentation by the morphological analyzer is not itself a
  failure if the readings are still correct
