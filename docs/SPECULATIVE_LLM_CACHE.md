# Speculative Reading Request Cache

Status: implementation plan, agreed 2026-09-09. This does not enable a worker.

## Objective

Prepare LLM reading responses for up to 500 upcoming documents, best effort,
without delaying review, issue processing, publication, or ordinary refill.
The ordinary ready-document target remains 100. Speculation supplements that
buffer; it does not create review packs or reserve canonical document numbers.

Human review can consume 100 documents in roughly an hour. Earlier controlled
decoder replay found high exact-request reuse across model updates, making
speculation plausible. That experiment does not establish completion latency
or guarantee future reuse. Reduced waiting is the primary objective; discounted
Batch execution is an additional benefit, not a requirement for correctness.

Scope initially covers ordinary reading requests only, not Escalated Repair.
No change to safety decisions, prompts, review stages, or human validation.

## Exact Request Cache

Use the production `build_response_create_kwargs` request builder in
`src/yomi_corpus/llm/backend.py`; do not maintain a second prompt implementation.
Hash a versioned, canonical JSON serialization of the endpoint and complete
response-affecting request body. Preserve strings and array order exactly.
Include model, input, reasoning, verbosity, output format, output limit, tools,
and any future response-affecting options by default. Explicitly exclude only
transport-only background mode and Batch custom IDs. Keep credentials out.

Decoder version, document ID, and processing-order position are provenance,
not request identity. A decoder update or reorder therefore does not invalidate
an identical request. Model or prompt changes naturally produce different keys.
Use separate track stores initially to avoid accidental dev/working coupling.

Store the exact request, raw successful response, response ID, usage, tool
metadata, timestamps, and originating job. Keep attempts separately from the
reusable response so failures cannot overwrite a success. Record code/config,
dictionary/decoder identifiers, source identity, and order snapshot as job
provenance without retaining all intermediate pipeline files.

The foreground always constructs its request normally, then checks the cache:

1. Reparse and validate a completed response using current item metadata and
   current production validation. Rebind administrative IDs to the current item.
2. On a miss, pending request, invalid response, unavailable store, or bounded
   cache-access timeout, make the normal request immediately.
3. Never wait for a speculative Batch. Concurrent duplicate calls are acceptable.
4. Record successful foreground responses through the same cache interface.

Do not cache failures as answers. Do not bypass current validation because an
older parser accepted a response. Track rejected cache uses with the validation
version so repeated incompatible hits can be avoided without deleting history.
When multiple valid responses exist, use a deterministic first-success policy;
retain later attempts for audit rather than silently replacing the answer.

## Storage and Concurrency

Prefer SQLite for requests, attempts, Batch jobs, and preparation checkpoints,
with short transactions, unique request keys, bounded lock waits, and atomic
completion writes. Never hold a database transaction during an API call or
mechanical preparation. Keep raw payloads in the database initially.

The repository is on a shared filesystem. Verify SQLite locking and durability
for the actual deployment location before choosing journal mode; do not assume
WAL works safely there. If necessary, use persistent host-local storage with
backups and a single-host ownership rule. Cache loss must only increase API
work, never lose canonical review data. Job records need stronger persistence
than disposable cache entries because losing them can orphan paid requests.

Use an independent worker lock. Do not hold review-sync, refill, decoder-build,
or publication locks during speculative work. Reuse existing Batch lifecycle
helpers where possible, without coupling their scheduling to foreground work.

## Lookahead Worker

Read the authoritative processing-order queue, not sequential source lines.
Take up to 500 not-yet-prepared or claimed documents after ordinary preparation.
Exclude already claimed/ready documents and identify sources by stable identity.
This is a moving horizon, not 500 additional documents per invocation.

Run ordinary deterministic preparation and queue construction in a temporary
workspace using a consistent current model/config snapshot. Reuse the same
production functions for segmentation, safety decisions, and prompt rendering.
Do not write normal batch state or mutate the queue cursor. Persist requests,
minimal provenance, and checkpoints before removing successful intermediates.
Retain bounded failure diagnostics, not entire successful preparation trees.

Checkpoint inspected documents even when they produce no requests. Existing
successful or pending request keys prevent repeat submission. Do not restart
the entire horizon on every decoder update; normal refill resolves changed
requests through exact cache misses. Revalidate queue membership before new
submission after reordering. Already submitted work can finish and remain cached.

Submit eligible requests in Batch jobs, persist identifiers, and exit without
waiting. A periodic collector imports completed items independently of new
preparation, including successful items from partially failed jobs. Configure
horizon, preparation chunk size, polling interval, outstanding-job limits,
per-run submission limits, retry backoff, and retention. Begin with small chunks
and low process priority. Do not start new preparation while refill is active
or the ordinary ready pool is below target; yield between bounded chunks if
foreground work appears. Collection can continue while preparation is paused.

## Recovery and Accounting

- Persist submission intent before network submission. If a crash leaves an
  ambiguous submission outcome, reconcile remote jobs before resubmitting; do
  not claim exactly-once delivery across a remote API and a local transaction.
- Resume collection after restarts. Import results idempotently by job/item ID.
- Use bounded network timeouts and backoff. Rate limits and transient failures
  do not block foreground requests or permanently blacklist documents.
- Keep terminal failures visible; retry only under a bounded policy, not a
  tight loop. Malformed outputs never count as successful cache hits.
- Report pending jobs, horizon coverage, cache hits/misses/rejections, duplicate
  calls, estimated wasted speculation, latency, and actual billed usage.
- Count paid calls once at their originating attempt. A cache reuse event has
  zero new API usage; retain original usage separately for provenance rather
  than charging it again to each consuming batch.
- Prune old unused responses by configurable age/size, preserving pending job
  recovery records and compact usage/provenance history. Normal result artifacts
  must remain independently auditable after cache eviction.

## Implementation Order

1. Add canonical request identity, storage, and accounting with unit tests.
   Keep reuse disabled by default; verify filesystem behavior and concurrent
   access before enabling it. Capture successful normal calls first.
2. Add foreground reuse and current-parser validation. Test hit/miss behavior,
   unavailable storage, changed settings, invalid cached output, administrative
   ID rebinding, and races with collectors. Confirm no foreground Batch waiting.
3. Add resumable Batch submission/collection with crash-window and partial-result
   tests. Exercise a small explicit offline preparation run before scheduling.
4. Add processing-order-aware speculative preparation and its independent
   scheduler. Test reorders, model replacement, zero-request documents, depleted
   ready queues, worker restarts, cleanup, and production-priority yielding.
5. Run a small dev horizon first, inspect cost and foreground latency, then
   increase to 500. Keep both speculative submission and cache consumption
   independently disableable; neither rollback requires a corpus migration.

Acceptance: ordinary refill succeeds unchanged with the worker stopped or cache
missing; successful exact hits avoid a call; pending work never delays a miss;
no speculative artifacts enter human review; restarts do not lose tracked jobs;
usage is not double-counted; issue-processing latency does not regress.
