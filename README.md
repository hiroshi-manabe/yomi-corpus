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
