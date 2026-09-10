# Historical Speculative Request Replay

Run `python scripts/replay_speculative_requests.py --run-dir <experiment-dir>`.
This experiment does not construct an API client or change production state.

The initial windows are documents 1891-1990 (sequential), 2071-2170 and
2171-2270 (vocabulary campaign). For each target batch, compare its recorded
decoder with the decoder recorded when the batch 50, 100, or 200 slots earlier
was prepared. Use actual processing slots, not original source line numbers.

Use retained model binaries, training text, frequency tables, and stable-surface
tables. Audit their hashes and reject any model whose recorded finalized-batch
membership contains the target batch. Do not rebuild from mutable corpus paths
when a historical model snapshot already exists.

This is a controlled replay, not a claim of bit-identical historical operation:
current code, dictionary, safety policy, and prompts are held fixed. Mutable
learned exact rewrites are disabled on both sides to avoid later human edits
leaking into segmentation. Human/LLM annotations are removed from input units.
Future historical-lexicon reconstruction can refine this approximation.

Each isolated job generates mechanical readings, safety decisions and request
queues only. Request identities hash the complete response-request body built
by the production backend, excluding transport/background mode and custom IDs.
Administrative IDs are retained separately for diagnosis. Identical requests
are deduplicated for cost accounting; occurrence-level reuse is also reported.

Outputs: `audit.json`, per-job input/intermediate/request files, timing files,
and `summary.json` with reusable, wasted, missing requests and examples.
Prompt-token counts use `o200k_base` and omit protocol overhead. Output lengths
are estimated from recorded successful results using per-surface medians and a
global fallback. Cost ratios are sensitivity scenarios: Batch multiplier 0.5,
output/input price weights 1, 4 and 8, not verified current price quotations.
No claim is made about Batch completion timing or response correctness.

Jobs are resumable within the same frozen experiment inputs. Use a new run
directory after changing experiment settings, code, dictionary or prompts.
