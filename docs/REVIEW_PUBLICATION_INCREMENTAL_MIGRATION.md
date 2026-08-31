# Incremental Review Publication Migration

## Problem

Review submission latency currently includes a full static-site snapshot build.
Each state-changing `review-sync` pass reads every finalized batch, deletes and
recreates every finalized-document archive file, rebuilds the monolithic search
index, creates a temporary `gh-pages` worktree, copies the complete site into
it, and asks Git to compare the resulting tree. Git ultimately commits and
pushes only changed objects, but local generation, checkout, copying, hashing,
and cleanup still scale with the complete finalized corpus.

The migration must reduce this local work without weakening the current
snapshot guarantees. The published branch must remain a complete standalone
site, stale archive files must be removed, interruption must not corrupt the
next publication, and unchanged generated content must remain byte-for-byte
stable.

## Scope

The first rollout contains only two phases:

1. retain and incrementally synchronize a managed `gh-pages` worktree;
2. reuse unchanged finalized-document archive shards by `archive_revision`.

Search-index sharding, event-driven immediate reruns, and separation of review
application from archive maintenance remain later work. During phases 1 and 2,
`archive/dev/search.json`, archive indexes, active packs, and manifests continue
to be rebuilt on every state-changing publication.

## Invariants

- `origin/gh-pages` remains the authoritative published baseline.
- Every publication starts by fetching and resetting the managed worktree to
  that baseline. An interrupted or local-only prior attempt cannot leak into a
  later push.
- The managed worktree is runtime state under ignored `data/state`; it is never
  committed on `main` and is never used as a source of canonical corpus data.
- Synchronization copies changed files, creates missing files, and removes
  destination files absent from the generated `docs` snapshot.
- Git stages the complete managed worktree with `git add --all`, so deletions
  remain explicit and auditable.
- Archive reuse is allowed only when the existing shard has the expected
  schema, identity, bounds, document count, and `archive_revision`.
- Reuse never trusts modification times. Content identity is determined from
  the canonical revision embedded in the existing JSON payload.
- A malformed, missing, mismatched, or legacy shard is regenerated normally.
- The archive index and search index are generated from current canonical
  documents, not from reused shard payloads.

## Phase 1: Managed `gh-pages` Worktree

Replace the temporary worktree created for every publication with a persistent
managed worktree, initially:

```text
data/state/review_site/gh-pages-worktree/
```

For each publication:

1. fetch `origin/gh-pages` with the existing remote timeout;
2. create the managed worktree when absent, or validate and reuse it;
3. hard-reset and clean it to `origin/gh-pages`;
4. synchronize `docs/index.html`, `docs/review/**`, and `.nojekyll` without
   recopying byte-identical files;
5. remove stale published paths;
6. stage all changes, commit only when the index differs, and push as before.

If the managed directory exists but is not a valid worktree, remove it, prune
stale Git worktree metadata, and recreate it. The existing publication lock
continues to serialize the full publisher and the lightweight acknowledgment
publisher.

The first run pays the ordinary checkout cost. Later runs avoid checking out,
copying, and deleting the full branch tree. Network pushes remain incremental
as they already are.

## Phase 2: Revision-Based Archive Reuse

Archive shard size is currently one document, and each document already has a
deterministic `archive_revision`. Stop clearing `docs/review/archive` before
generation. For each expected document shard:

1. compute the current canonical document and revision as today;
2. inspect the existing shard at the deterministic sequence-based path;
3. leave the file untouched when its schema, identity, range, count, and
   revision all match;
4. otherwise write the complete current payload atomically;
5. after all tracks are generated, delete JSON files and obsolete track
   directories not present in the expected output set.

This phase avoids serializing and writing unchanged per-document JSON, while
retaining the existing all-batch canonical scan needed to compute revisions.
It deliberately does not optimize the large search index yet. Generation
metadata should report written, reused, and removed shard counts so operational
measurements can guide the next phase.

## Verification

Automated coverage must include:

- first creation and later reuse of a managed publication tree;
- changed, unchanged, added, and deleted files during snapshot synchronization;
- reset behavior after an interrupted or dirty worktree;
- unchanged archive shards retaining their bytes and modification times;
- changed revisions rewriting only their shard;
- removed finalized documents deleting stale shards;
- malformed prior shards being regenerated;
- archive index and search output remaining equivalent to a clean build;
- a no-change second publication producing no Git commit.

Before enabling the timer path, run the review-site and publisher test suites,
perform two consecutive local generations, and compare manifests, indexes, and
archive file hashes. Then run one real `gh-pages` publication and confirm that
the second no-change publication does not rewrite or commit archive shards.

## Rollback

Phase 1 can be rolled back by removing the managed worktree with
`git worktree remove --force`, pruning worktree metadata, and restoring the
temporary-worktree publisher. No canonical data migration is involved.

Phase 2 can be rolled back by restoring archive-directory clearing and
unconditional shard writes. Existing reused shards are already complete
canonical artifacts, so no archive data conversion is required.

## Later Phases

After measuring phases 1 and 2:

1. split archive search into immutable or range-based shards;
2. publish immediate queue/application state independently from archive search;
3. retain durable watcher demand and rerun review-sync immediately when a
   submission arrives during an active pass;
4. consider incremental canonical-document collection if full finalized-batch
   scanning becomes the next dominant cost.
