# Decoder Refresh Scheduling Migration

## Goal

Finishing a review batch must not imply that its corpus is immediately included
in a new decoder or N-gram model. Review submission processing is
latency-sensitive; model rebuilding is maintenance work. The accumulated
finalized corpus is already large enough that delaying a small number of new
batches has negligible operational cost.

The steady-state ownership model is:

| Process | Responsibility |
| --- | --- |
| `review-sync` | Import and apply Issues, advance document state, close consumed Issues, and publish review state |
| `refill-worker` | Prepare new Bulk Review documents |
| `decoder-refresh-worker` | Periodically inspect finalized corpus state and rebuild eligible decoder/N-gram artifacts |

No model-build request or model-maintenance policy should be part of a normal
`review-sync` pass.

## Target Policy

For dev, run a cheap decoder-refresh eligibility check periodically and rebuild
only when both conditions hold:

- at least 20 finalized batches are not represented in the latest successful
  model;
- at least 24 hours have elapsed since the latest successful refresh.

This is intentionally conservative. A manual worker invocation may override
the thresholds when an experiment needs a fresh model immediately. Working
remains disabled until its independent repository and operating policy are
ready.

Eligibility is derived from canonical finalized-batch state and the manifest of
the latest successful decoder model. It must not depend on a mutable current
batch pointer or on which batch happened to finish most recently.

## State And Failure Semantics

- A successful model manifest records every finalized batch included in the
  build. Later checks compute the set difference against current finalized
  batches.
- The model pointer changes only after export, model construction, and artifact
  validation succeed.
- A failed build leaves the previous model active and records a timestamped
  worker summary. The next scheduled check retries if the policy remains
  eligible.
- Review state, Issue closure, review publication, and refill are never blocked
  by decoder-refresh failure or eligibility.
- The decoder worker retains its independent per-track lock, preventing timer
  overlap and manual/scheduled overlap.
- Existing durable request files are accepted during migration so an already
  queued refresh is not lost. Review-sync stops creating or reasserting them.
  After existing requests have been consumed, scheduled eligibility is the
  sole automatic trigger.

## Migration Steps

### 1. Remove Review-Sync Ownership

1. Remove decoder-refresh policy flags from `./review-sync`.
2. Remove request planning, request writes, trigger writes, and decoder-refresh
   result fields from the review-sync pass.
3. Keep review-sync focused on Issue application, state advancement, runtime
   status, and review publication.
4. Update tests to make any decoder-refresh filesystem mutation during
   review-sync a regression.

### 2. Make The Worker Self-Scheduling

1. Move refresh eligibility helpers out of review-sync ownership.
2. Let `./decoder-refresh-worker <track>` calculate eligibility even when no
   request file exists.
3. Preserve legacy request metadata in summaries when a request exists, but do
   not require it for a scheduled run.
4. Clear a consumed legacy request after either a successful refresh or a
   terminal no-work result. Keep it after retryable failure.
5. Preserve `--dry-run`, threshold overrides, and `--skip-kenlm` for manual
   diagnostics.

### 3. Replace The Path Trigger

1. Add a version-controlled user-level systemd timer for dev decoder refresh.
2. Run the eligibility check periodically; the check is cheap when thresholds
   are not met.
3. Configure dev with `min_new_batches = 20` and
   `min_interval_minutes = 1440`.
4. Disable the old decoder-refresh path unit after the timer is installed.
5. Leave working disabled.

## Verification

The migration is complete when:

- applying/finalizing a batch does not create a decoder request or trigger;
- review-sync completes without waiting for decoder maintenance;
- a scheduled worker with fewer than 20 new batches exits as `waiting` without
  changing the model pointer;
- an eligible worker builds one model containing all finalized batches, not
  only the threshold-crossing batches;
- a failed build leaves the prior model active and is retried by a later timer;
- simultaneous timer/manual starts are rejected by the existing worker lock;
- the dev timer is active and the legacy path unit is disabled.

## Rollback

Disable the timer and invoke `./decoder-refresh-worker dev` manually with
explicit threshold overrides. The previous successful model remains usable
throughout rollback. Restoring request emission in review-sync is unnecessary:
manual or scheduled worker execution derives the same pending batch set from
canonical state.
