# Event-Driven Issue Acknowledgment Migration

## Purpose

Review Issues should become visible to every browser soon after GitHub accepts
them, without running the complete review synchronizer every 30 seconds.

The target lifecycle is monotonic:

```text
locally submitted
  -> server acknowledged / processing
  -> Bulk Review applied
  -> Escalated Repair or finalized
  -> application failed, when manual recovery is required
```

The browser must not return a submitted document to an ordinary unmarked state
between these transitions. Multiple Issues that cover the same document must be
shown as a conflict rather than silently selecting one device's submission.

## Process Split

Introduce a lightweight `issue-watch` worker alongside the existing workers:

| Process | Trigger | Responsibility |
| --- | --- | --- |
| `issue-watch` | approximately every 30 seconds | discover and acknowledge new review submissions |
| `review-sync` | watcher demand plus five-minute recovery timer | import, apply, advance, close Issues, and publish canonical state |
| `refill-worker` | independent timer | prepare new Bulk Review material |
| `decoder-refresh-worker` | independent maintenance timer | evaluate accumulated finalized batches and rebuild decoder artifacts |

`issue-watch` must not apply review data, run pipeline stages, call reading
LLMs, rebuild indexes, or finalize batches. Its normal pass should perform only
GitHub metadata retrieval, envelope validation, acknowledgment-state updates,
and sync notification.

The existing five-minute `review-sync` timer remains enabled. It is a recovery
path for missed watcher events, transient GitHub failures, interrupted workers,
and manual Issue edits. It is no longer the expected acknowledgment latency.

## Acknowledgment Contract

The watcher recognizes only Issues or comments containing a parseable review
submission envelope. Before acknowledgment it validates enough structure to
avoid advertising unrelated Issues as work:

- supported schema and submission type;
- known review stage and pack ID;
- non-empty submission ID;
- document IDs that can be mapped to the referenced pack;
- Issue/comment provenance and payload hash.

This is envelope validation, not application validation. Token overrides,
segmentation edits, exclusion confirmation, and current-stage applicability
remain the responsibility of `review-sync`.

Persist acknowledgment state atomically under:

```text
data/state/issue_watch/<track>.ledger.json
data/state/issue_watch/<track>.acknowledgments.json
```

Each acknowledged submission records at least:

- submission ID and payload hash;
- Issue number and optional comment ID;
- pack ID, review stage, and document IDs;
- first-seen and last-seen times;
- acknowledgment state: `pending`, `processing`, `applied`, `failed`, or
  `invalid`;
- the canonical review-sync run or application summary that consumed it, when
  known.

The payload hash prevents an edited Issue from being mistaken for the version
already acknowledged. An edited payload is a new watcher event even when the
Issue number is unchanged.

## Browser State Artifact

Publish a compact acknowledgment artifact separately from full review packs.
It maps stable document IDs to active submission acknowledgments and contains a
monotonic revision. The UI combines it with browser-local drafts and canonical
document state in this order:

1. canonical applied, escalated, finalized, or failed state;
2. watcher acknowledgment (`server processing`);
3. local submitted state;
4. ordinary actionable state.

An acknowledgment must remain published until the same submission is
represented by a replacement state in the successfully pushed
`origin/gh-pages` pack. Local pipeline advancement is not sufficient evidence:
generation or push may still fail, leaving browsers on the previous pack.
Closing an Issue, changing an archive revision, or observing a different
submission must not clear the acknowledgment by itself. If the remote-tracking
pack cannot be read, retain the acknowledgment conservatively.

If two active acknowledgments include the same document, publish all matching
submission and Issue IDs and mark the document `submission conflict`. The UI
should show a distinct warning badge with links to the Issues. The watcher does
not decide precedence. Existing importer/replay policy or human intervention
resolves the conflict.

Publication is event-driven. A no-change 30-second poll writes no repository
files and performs no Pages deployment. New, changed, consumed, failed, or
conflicting acknowledgment state updates only
`review/issue-acknowledgments.json` through the GitHub Contents API. A local
content-hash marker records successful publication, while each poll verifies
the remote blob so a concurrent full-site push cannot silently restore stale
state. Optimistic `sha` updates retry when `review-sync` advances `gh-pages`
concurrently, so acknowledgment latency does not depend on the full review-site
generation lock.

Adding a GitHub `server-processing` label is useful but secondary. The label
improves visibility on GitHub and across devices, while the acknowledgment
artifact is the machine-readable UI contract. Label mutation must be
idempotent and excluded from payload-change detection so it cannot trigger a
polling loop.

## Event-Driven Sync Trigger

The watcher writes a durable trigger containing the unconsumed acknowledgment
revision rather than assuming that one `systemctl start` call was accepted:

```text
data/state/review_sync/<track>.trigger.json
```

The trigger includes a revision, creation time, and pending submission IDs. A
systemd path unit or a non-blocking service start wakes `review-sync`.

Important race behavior:

- if `review-sync` is idle, the watcher starts it promptly;
- if it is already active, the durable trigger remains pending;
- after a sync pass, the worker clears only the trigger revision whose
  submissions its summary consumed;
- a newer trigger written during the pass remains and causes another pass;
- the existing per-track review-sync lock remains authoritative;
- repeated watcher polls and repeated service starts are harmless.

This coalesces several Issues arriving close together into one sync pass while
preventing an Issue discovered during a long pass from waiting indefinitely.

## GitHub Polling

Use authenticated GitHub API access. A 30-second interval is approximately 120
polls per hour and is acceptable for the authenticated account, but the worker
must still minimize requests:

- request only open Issues updated since the previous cursor;
- retain issue number, comment cursor, `updated_at`, and payload hash;
- use conditional requests or ETags where the API path supports them;
- back off on rate-limit or transient network responses;
- report remaining rate-limit information in the watcher summary;
- do not classify rate limiting as an invalid submission.

The cursor advances only after the corresponding acknowledgment ledger update
is durable. Full periodic reconciliation should still run occasionally so a
bad cursor or missed edit cannot permanently hide an Issue.

## Systemd Topology

Add version-controlled user units:

```text
yomi-corpus-issue-watch-dev.service
yomi-corpus-issue-watch-dev.timer
yomi-corpus-review-sync-dev.path
```

Initial policy:

- watcher timer: 30 seconds;
- watcher service: one bounded pass, with its own per-track lock;
- review-sync path/service: started by durable trigger changes;
- review-sync timer: retain the five-minute fallback;
- no watcher restart loop inside Python; systemd owns scheduling;
- `Persistent=true` is unnecessary for the 30-second timer because the
  five-minute sync provides recovery after downtime.

The watcher lock and review-sync lock must be independent. The watcher may read
canonical state while sync is active, but it must publish atomically and never
modify pipeline artifacts owned by sync.

## Implementation Phases

### Phase 1: Read-only watcher

1. Extract shared Issue-envelope discovery and validation from the importer.
2. Add a CLI that polls once and writes a diagnostic summary without labels,
   publication, or sync triggers.
3. Verify detection against historical Bulk Review, Escalated Repair, and
   finalized-correction Issues.
4. Confirm that unrelated, malformed, stale-pack, and edited Issues are
   reported distinctly.

### Phase 2: Durable acknowledgment

1. Add the acknowledgment ledger and payload hashes.
2. Generate the compact browser artifact with a monotonic revision.
3. Update UI reconciliation so local submitted state is replaced only by the
   matching acknowledgment or canonical application state.
4. Add conflict presentation for overlapping active submissions.
5. Publish only on state changes under the shared publication lock.

### Phase 3: Event-driven review sync

1. Add the durable review-sync trigger and systemd path unit.
2. Make review-sync summaries list consumed submission IDs and trigger
   revision.
3. Clear only consumed demand and immediately rerun when newer demand remains.
4. Retain the five-minute timer and verify missed-event recovery.

### Phase 4: GitHub labels and rollout

1. Add the optional `server-processing` label after acknowledgment succeeds.
2. Remove the label only after application, explicit invalidation, or failure.
3. Enable the 30-second timer for `dev` only.
4. Measure acknowledgment latency, API usage, duplicate wakeups, publication
   latency, and sync duration before considering `working`.

## Verification

Automated tests must cover:

- one Issue is acknowledged once across repeated polls;
- multiple new Issues are coalesced into one durable trigger;
- an Issue arriving during active sync causes a follow-up pass;
- Issue edits produce a new payload hash and acknowledgment revision;
- malformed Issues never receive processing acknowledgment;
- rate limiting preserves cursors and pending demand;
- local submitted state never becomes ordinary/unmarked before acknowledgment
  or canonical application;
- Bulk Review application transitions to Escalated Repair or finalized state;
- application failure remains visible and leaves the Issue open;
- two devices submitting the same document produce a visible conflict;
- no-change polls do not modify generated files or publish gh-pages;
- watcher and sync publication cannot overwrite a newer acknowledgment
  revision.

Run a real-browser DOM test against locally generated `docs/` artifacts for the
three primary transitions and the conflict badge. Then perform a `dev` smoke
test with two synthetic Issues while the sync service is deliberately held
active.

## Rollback

Disable the watcher timer and path unit. The five-minute review-sync timer then
restores the previous behavior without changing submission or pipeline data.
Acknowledgment artifacts and labels are advisory; canonical document state and
the existing importer remain authoritative throughout the migration.

## Initial Implementation

`./issue-watch dev --publish gh-pages` performs one bounded poll. It records
recognized submissions in `data/state/issue_watch/dev.ledger.json`, publishes
the active subset directly as `issue-acknowledgments.json` without regenerating
or checking out the full Pages tree, and starts the existing
`yomi-corpus-review-sync-dev.service` once for each newly seen payload hash.
An open but temporarily unprocessable Issue does not create a tight sync loop;
the independent five-minute review-sync timer owns all retries.

The browser treats an active acknowledgment as server processing before the
pipeline importer advances the document. Two active submissions naming the
same document are visibly marked as a conflict. After an Issue closes, an
imported-but-unapplied submission remains acknowledged. Even after local
application, the watcher retains the acknowledgment until `origin/gh-pages`
contains the advanced or failed document state; review-sync remains responsible
for validation, application, and closure.

The deployable `yomi-corpus-issue-watch-dev.timer` runs every 30 seconds. Install
or refresh it with `./ensure-issue-watch-timer`; the existing five-minute
review-sync timer remains enabled as the recovery path. The initial rollout
uses direct systemd service activation rather than a path unit, reducing moving
parts while retaining durable ledger state and bounded retries.

The heavy review-sync and refill timers use `OnUnitInactiveSec=5min`, not
`OnUnitActiveSec`. A pass that itself lasts longer than five minutes therefore
gets a five-minute idle window after completion instead of being relaunched
immediately and monopolizing the shared publication lock.

Review-pack preparation is deliberately publication-free. The refill worker
may create a Bulk Review pack but never generates the complete Pages tree.
`review-sync` applies at most one global action budget, observes a soft runtime
deadline before starting each action, and performs at most one coalesced
full-site publication after the pass. The 30-second watcher remains the only
fast-path publisher, and it updates only `issue-acknowledgments.json`.
