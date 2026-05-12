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
- For classical/non-target detection and mechanical "safe" decisions, collect
  raw features first and defer real deterministic gating until reviewed data
  exists.
- Use cheap LLM triage, then more expensive contextual repair, then human
  review.

Prompt iteration scaffold:

- Fixed eval sets live under `data/evals/<task>/`.
- Sync experiment runs live under `runs/experiments/<task>/<run_name>/`.
- Each run writes `items.jsonl`, `results.raw.jsonl`, `results.parsed.jsonl`,
  `scored.jsonl`, `summary.json`, and a prompt snapshot.
- Use `scripts/run_prompt_experiment.py` to run one prompt version on an eval
  set.
- Use `scripts/compare_prompt_experiments.py` to compare two runs and inspect
  changed failures.

Default model policy:

- use `gpt-5.4` for normal judgment and repair tasks
- reserve `gpt-5.4-pro` for a tiny last-resort rescue tail
- use `gpt-5.4-nano` only for plumbing and instrumentation checks
- treat `gpt-5.4-mini` as opt-in per task, not the default path

Alphabetic entity policy:

- unresolved Latin/alphanumeric entity types are judged in batches
- whitelist/blacklist promotions should be proposed from accumulated evidence
- use a temporary promotion-candidate threshold of `3` consistent observations
  for both whitelist and blacklist directions
- humans review only promotion candidates before global list entries are added

Review transport policy:

- assume static browser UI rather than cluster-hosted writable UI
- keep the review UI in this same repository rather than a separate UI repo
- isolate the static review app in its own directory so it does not mix with
  the Python pipeline code
- GitHub Pages is the preferred first host for review HTML
- GitHub Issues are the preferred first mailbox for returned review submissions
- browser UI should persist local drafts and support partial range-based
  submissions

Pipeline orchestration policy:

- keep local pipeline state per batch and a current-batch pointer per track
- use `working` as the implicit default track and `dev` as an explicit secondary
  track
- `working` is the strict protected track; `dev` is the relaxed experimental
  track
- `./prepare 100` prepares the next working batch, while `./prepare dev 10`
  prepares the next dev batch
- `./next` advances the current working batch by one implemented automatic
  stage; `./next dev` does the same for the dev track
- `./next --force-stage <stage>` reruns the current completed stage; on
  `working`, an overwrite prompt appears only if existing artifacts would
  actually be replaced
- `./status` and `./status dev` report the current batch and stage for each
  track
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
- supported decoder overrides and safe auto-acceptance are separate decisions:
  a supported decoder/Sudachi disagreement may be used as a tentative
  correction because review is still the default, but a unit is only safe when
  Sudachi and decoder evidence agree under full-span support checks
- numeric runs are grouped and emitted with an empty reading, such as `2021/`;
  number pronunciation is intentionally left to a future dedicated number
  reading module
- yomi quality is judged primarily by reading correctness, not ideal
  segmentation; over-split katakana or morphology is acceptable for now if the
  readings are correct
- after yomi generation, `yomi_auto_accepted` adds
  `analysis.mechanical.yomi.auto_accept` for low-risk units with no kanji, no
  alphabetic letters, and no unresolved non-numeric readings
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
