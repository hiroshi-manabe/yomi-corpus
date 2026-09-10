# Durable submission receipts

Confirmed submission is evidence independent of task drafts and queue snapshots.
The browser records a receipt per source document ID and review stage in
`yomi-corpus:submission-receipt:v1:*`. Pack migration, task cleanup, archive
navigation, reloads, and absent server records must not erase that evidence.
Existing submitted tasks are migrated before cleanup. Submitted task data is
backed up once per task; receipts reference it so a lost draft can be recovered.

Local confirmation means locally submitted. A server acknowledgment means server
processing. Later review stages use distinct keys, and finalized documents retain
their normal resolved presentation. Explicit reopening records a local tombstone
so migration of an old submitted task cannot undo the user's reopen action.
Reopening does not override a server acknowledgment.

The issue watcher publishes durable `receipt_history` alongside active `records`.
History remains when an issue disappears from the active response; it supplies
the same evidence to other devices. Browsers remember both collections, scoped
to the originating review stage, and cannot revert status merely because a later
response omits a record. Active conflict information takes precedence.

This does not prove an Issue was created when a user confirms locally. Nor can it
recover a local submission already erased before receipt storage was introduced,
unless another saved task or server acknowledgment still provides the evidence.
Clearing browser storage removes local-only evidence.

Task identity is independent of its display number. New tasks receive UUID-based
IDs and a persistent browser-wide display counter stored under
`yomi-corpus:task-identity:v1:*`. Pack changes do not reset that counter. Legacy
submitted tasks use persistent aliases based on review stage, submission time
and original label, so duplicate fragments map to one identity while separate
submissions with the same old label receive distinct numbers. The assigned
identity is carried by `task_uid` through task migration and receipt recovery.
Numbers are local to a browser profile, not globally shared between devices.

Verification: `node tests/submission_receipts.mjs` exercises task-independent
persistence, other submissions, reopen/resubmit, migration tombstones, stage
isolation, missing acknowledgments and history on a fresh device.
`tests/test_issue_watch.py` verifies history survives an empty issue response.
