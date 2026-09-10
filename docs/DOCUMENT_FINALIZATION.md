# Document-Level Finalization

Preparation batches remain the scheduling and eventual harvesting unit. A
document no longer has to wait for the other documents in its preparation batch
before appearing in the finalized corpus.

## Lifecycle

- After Bulk Review is applied and the escalation queue is constructed, a fully
  reviewed document with no escalation targets is finalized independently.
- A document with escalation targets is finalized only after its Escalated
  Repair human submission has been applied successfully. LLM completion alone
  is not sufficient. A failure in another document does not prevent completion.
- Skip and Exclude retain their existing per-sentence semantics, including
  all-skipped/all-excluded documents.
- Publication moves finalized documents from active review to Corpus Map in the
  same generated snapshot. Other documents in the batch remain active.

Escalated Repair LLM execution is independent of document finalization. Dev now
uses nonblocking API Batch cohorts for new repair work; see
[Escalated Repair Batch Execution](ESCALATED_BATCH_EXECUTION.md).

## Storage And Recovery

`data/units/<batch>/document_finalization.json` is the commit manifest for
independently finalized documents. It records document IDs, unit IDs, completion
timestamps, and validation failures. Canonical units remain in the existing
`units.yomi.final.jsonl`, `units.yomi.skipped.jsonl`, and
`units.yomi.excluded.jsonl` files.

Writers run under the existing review-sync serialization. Each output file is
replaced atomically, followed by the manifest, then document state. Publication
only exposes manifest-listed documents while the batch is unfinished. A retry
can discard uncommitted rows and append the document again without duplication.
Committed rows are preserved, including Corpus Map corrections made while other
documents are still being reviewed. Final batch closing must not regenerate
those rows from older review artifacts.

Each document must have exactly its original unit coverage and reviewed status
on every unit. Keep rows use the existing finalized-token validator. Invalid
documents stay out of the archive; failures are recorded in the manifest and
prevent final batch closing, without blocking unrelated documents.

Completed historical batches continue using their existing files without
requiring a manifest. Migration of unfinished batches runs the same idempotent
document finalizer; it does not repeat LLM calls or change accepted readings.

## Batch Closing

Once all documents have finished, the batch closes as before. Harvesting learned
readings, segmentation corrections, supplemental ruby data, and subsequent
decoder corpus inclusion remain batch-based. Their timing does not prevent
individual documents from being archived and corrected earlier.
