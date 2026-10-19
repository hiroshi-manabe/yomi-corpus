# Review UI Source

This directory contains the editable source files for the static review UI.

Current intended split:

- `web/review/`: editable source files for the review app
- `docs/`: generated static output staged before publishing
- `gh-pages`: publish branch containing `index.html` and `review/**`

The Python pipeline should not depend on frontend tooling living here. The
review app should read immutable review-pack JSON and emit review-submission
JSON that can be returned through GitHub Issues.

After a pipeline stage updates the review pack and manifest, publish only those
generated artifacts with:

```bash
./publish-review
```

The helper generates the complete site under the ignored `docs/review/`
staging directory, stages that output on the `gh-pages` branch, and runs
`git push`. Configure GitHub Pages to deploy from `gh-pages` / root. Use
`./publish-review --dry-run` to inspect the exact path set first; note that it
still regenerates the ignored staging directory.
# Rapid Visual Review

`rapid.html` is a read-only viewer of finalized documents. It does not edit readings.
It packs existing tokens into frames without splitting ruby groups; sentence
coverage is the union of displayed token positions, keyed by a SHA-256 revision
of `[text, yomi_tokens]`. Hidden tabs pause playback. Speed is not a qualification
criterion. Excluded tombstones are omitted; skipped sentences remain reviewable.

Coverage, settings, immutable submission drafts, and pending receipts are stored
in IndexedDB, independently of the existing editor's localStorage quota. The
partial-document action gathers unseen frames across visited
documents. Submit complete documents as `rapid_review_receipt` JSON in an Issue
(inline or attached). Mark the draft submitted only after creating the Issue.
Retries retain the same submission ID; each submission increments each document
at most once. Review sync validates current finalized sentence revisions, records
receipts under `data/state/rapid_review/`, and closes accepted Issues. A separate
green Corpus Map badge shows historical visual-review counts, not correction
counts or a guarantee that the current revision has been checked.

Tests: `node --test tests/rapid_review.test.mjs` and
`PYTHONPATH=src python -m unittest discover -s tests -p test_rapid_review.py`.
