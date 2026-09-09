# Queue-Oriented Mechanical Preflight

Run `python scripts/queue_preflight.py dev --chunk-size 10`
as a detached, low-priority process. This is diagnostic only, not a production
refill or an LLM worker.

Before each chunk, reread the live processing-order cursor and upcoming slots.
Choose the earliest unchecked source documents, including reordered vocabulary
campaign documents, scanning the remaining queue in bounded pages. There is no
default limit. Sleep 300 seconds when caught up, then check again.
`--documents N` optionally bounds a run.

Each chunk uses a disposable source file and isolated pipeline workspace. Run
all deterministic stages through `yomi_reading_queued`, refusing any LLM stage.
Synthetic document numbering inside that workspace does not alter live IDs.
Source line identities are recorded in each report and the worker status.

State is under `data/preflight/queue/<track>/`:

- `status.json`: current chunk, progress, and failure/report location.
- `completed.json`: successful source checks with text hashes and report paths.
- `worker.lock`: prevents concurrent queue-preflight workers for the same track.
- `failures.json`: isolated document failures, separate from successful checks.
- `history.jsonl`: append-only outcomes and version metadata.

Successful checks are keyed by source-file content hash and source line number.
Old checkpoints are reused. Code/configuration digest and model version are
recorded but do not invalidate successful checks automatically. Each chunk runs
in a fresh subprocess loading current code. Restart after supervisor changes.

Failed groups are retried individually. Token/validation failures are retained
with their workspaces; later documents continue. Ordinary restarts skip recorded
failures; `--retry-failures` retries them once in the new run. Successful
workspaces are deleted. Unknown/infrastructure errors cause backoff, not
document-failure checkpoints. Three consecutive document failures also cause
backoff. A child timeout (one hour by default) kills its process group and
backs off. Configure intervals with `--poll-seconds` and `--timeout-seconds`.
Production is unchanged: no live documents are silently skipped.

On 2026-09-07 this replaced the sequential replay of the old source. The old
replay checked only mechanical generation and is not equivalent to this
all-deterministic-stage check. Neither check guarantees reading accuracy or
validates LLM responses and human submissions.
