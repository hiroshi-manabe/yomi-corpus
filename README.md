# yomi-corpus

This repository is the orchestration and governance layer for building a large
Japanese corpus with readings.

It is intentionally separate from:

- `../llm-jp-corpus-v4/`, which prepares the filtered source corpus
- `../yomi-decoder/`, which provides an N-gram reading decoder
- `../openai/`, which already contains useful OpenAI batch-processing helpers

The first design draft lives here:

- [docs/PIPELINE_DESIGN.md](/panfs/panmt22/users/hmanabe/yomi-corpus/docs/PIPELINE_DESIGN.md)
- [docs/WORKING_PIPELINE.md](/panfs/panmt22/users/hmanabe/yomi-corpus/docs/WORKING_PIPELINE.md)

Initial project stance:

- Keep source records immutable.
- Treat this repo as a staged pipeline, not a grab bag of scripts.
- Separate "confidence", "repairability", "modern-Japanese status", and
  "human-reviewed status" instead of collapsing everything into one flag.
- Use deterministic processing first, then cheap LLM triage, then more
  expensive contextual repair, then human review.

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

- unresolved entity types are judged in batches
- whitelist/blacklist promotions should be proposed from accumulated evidence
- humans review only promotion candidates before global list entries are added

Review transport policy:

- assume static browser UI rather than cluster-hosted writable UI
- GitHub Pages is the preferred first host for review HTML
- GitHub Issues are the preferred first mailbox for returned review submissions
- browser UI should persist local drafts and support partial range-based
  submissions
