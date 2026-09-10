# Escalated Repair Batch Execution

Dev uses API Batch for new Escalated Repair requests. Bulk Review preparation
and working-track execution settings are unchanged. This is an execution change,
not a change to prompts, models, web-search policy, or human approval gates.

## Flow

1. Apply Bulk Review and construct the escalation queue as before. Documents
   without escalation finalize independently.
2. Snapshot new requests into an immutable cohort, submit it, and return control
   to review-sync. Do not wait for Batch completion inside the synchronization
   pass.
3. Subsequent passes poll each outstanding cohort once. Newly arriving requests
   form new cohorts; existing requests are not resubmitted.
4. When the current request set has terminal results, run the existing response
   validation and bounded retry rounds. Each retry round has its own cohort
   directory and submits only missing/invalid requests, not successful ones.
5. Apply proposals and publish the existing Escalated Repair UI. Finalization
   still requires human review; completed LLM requests are not human acceptance.

Previously published repair tasks remain available while additional requests are
pending. New proposals are materialized when the current preparation batch's
request set (and any response retries) is ready. Preparation batches still
contain ten documents; API cohorts are independently frozen request sets.

## Identity And Recovery

Each request key includes the item ID, endpoint, complete API request body, and
parser name. Equal item counts do not imply equal queues. Changed prompts,
surfaces, context, or model parameters produce new keys. Removed requests cannot
overwrite results for the current queue. Reordering alone does not resubmit.

State lives in:

```
data/llm/jobs/<batch>_yomi_strong_repair/batch_cohorts/
data/llm/jobs/<batch>_yomi_strong_repair_retry<N>/batch_cohorts/
```

Each `cohort-*/snapshot.json` freezes request bodies and parsing metadata.
`state.json` records upload IDs, remote Batch IDs and status; raw output/error
files and parsed `results.json` are retained. Root `status.json` summarizes all
cohorts. Output and error files from expired/cancelled jobs are collected too,
so successful requests survive partial failure. Incomplete API responses are
invalid even if their text happens to contain parseable JSON.

Submission intent is saved before the API create call. If its response is lost,
later passes reconcile the cohort metadata against remote jobs rather than
blindly creating another Batch. An unresolved outcome remains pending and is
recorded for diagnosis. The bounded search checks up to 1,000 recent remote jobs;
an older unreconciled submission may require operator investigation.

Network operations have a 30-second timeout and no SDK retries; failures are
recorded per cohort and do not stop other cohorts from being polled. This is
nonblocking with respect to remote completion, not a promise of zero network
latency. Existing review-sync serialization protects local writes.

Rate-limit responses wait five minutes, then retry only affected requests in
separate immutable transport-attempt cohorts. They do not consume the bounded
model-response retry rounds. Successful results from the cohort are retained.
Ordinary malformed/missing responses use the existing bounded response retries.
Batch token usage is priced as Batch usage; web-tool usage remains recorded.

## Rollout

The default applies to newly prepared dev batches. Existing dev batches can be
migrated only before their repair execution starts; update their saved execution
policy as well as their unit manifest under the review-sync lock. Leave existing
background jobs and completed repair outputs on their original policy.

On 2026-09-10, isolated live tests verified `gpt-5.4-mini` Batch requests with the
current web-search settings. A forced-search request made two web-search calls
and returned the reading for `真光元`. Evidence is retained under
`data/analysis/escalated_batch_smoke_20260910/`, outside production review data.

The [official Batch guide](https://developers.openai.com/api/docs/guides/batch)
describes asynchronous submission, retrieval and output/error files. Tool
compatibility was tested against the actual API rather than inferred merely
from support for the `/v1/responses` endpoint.
