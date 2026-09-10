# Refill Throughput

The refill loop treats completion of a distinct batch as progress, even if
concurrent human review makes the Bulk Review ready count stay flat or fall.
It continues until the target is reached or a genuine stopping condition occurs.
Repeating the same completed batch stops with `no_progress`; the iteration cap
also remains in place. A falling ready count alone must not trigger a timer gap.

Mechanical JSONL generation uses bounded chunks configured by
`[generation] batch_size` in `config/yomi/default.toml` (32 by default).
Each chunk invokes Sudachi and the decoder once, using their existing stdin
interfaces. Decoder model/lexicon loading is amortized across the chunk without
a persistent daemon or changes to beam width, N-best, or reading policy.
Set the batch size to 1 to use the original per-sentence path.

Results are checked for count and input order. Original source-surface mapping
and all downstream normalization remain shared with the single-sentence path.
Multiline/empty analysis inputs use that path because stdin is line-oriented.
Failures propagate rather than silently dropping inputs. Progress updates occur
after each chunk finishes. Plaintext fallback generation remains per sentence.

On 2026-09-07, the first 32 sentences of `dev_batch_1053` took 25.68 seconds
individually versus 1.05 seconds in one chunk, with equal complete MechanicalYomi
objects. This is a local mechanical-stage benchmark, not an end-to-end refill
speedup estimate: LLM latency and other stages still contribute.
