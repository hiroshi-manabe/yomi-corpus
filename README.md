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

- keep local pipeline state per batch rather than relying on ad hoc operator
  memory
- provide a read-only `status` command and a mutating `advance` command
- let `advance` run automatic stages until it reaches a real blocking condition
- treat OpenAI Batch waits and human-review waits as explicit pipeline states,
  not special cases

Yomi generation scaffold:

- deterministic generation now has a local harness under `src/yomi_corpus/yomi/`
- `scripts/generate_mechanical_yomi.py` runs Sudachi plus `../yomi-decoder/`
  over units and writes updated `analysis.mechanical.yomi`
- the current mechanical baseline uses Sudachi B-mode segmentation, then adds a
  narrow hybrid layer for contextual reading fixes and decoder-informed segment
  recovery
- `scripts/export_yomi_outputs.py` is the main operator helper for generating
  variant-specific batch artifacts; it defaults to JSONL only, and plain-text
  dumps are opt-in for diff/debug work
- `scripts/export_yomi_plaintext.py` is the operator-facing helper for
  generating only the diff-friendly plain-text dumps
- `scripts/run_yomi_experiment.py` runs one named combination strategy on a
  fixed eval set
- `scripts/compare_yomi_experiments.py` compares two strategy runs
- current strategy names include `sudachi_only_v1`, `decoder_only_v1`,
  `agreement_prefer_decoder_v1`, and `agreement_prefer_sudachi_v1`
- for yomi evaluation, correct readings matter more than coarse vs. fine
  segmentation; over-segmentation by the morphological analyzer is not itself a
  failure if the readings are still correct
