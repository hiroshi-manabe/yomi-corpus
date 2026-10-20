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
coverage is keyed by stable sentence identity. Partial progress records displayed
source-character positions, so reading and token-boundary corrections preserve
inspection credit. Source text is assumed immutable. Hidden tabs pause playback. Speed is not a qualification
criterion. Skip and Exclude sentences are omitted. Completion requires all and
the current eligible sentences; documents with none cannot earn a count.
Newly eligible sentences require coverage; sentences subsequently skipped or
excluded do not invalidate otherwise complete receipts.
Frame timing is the base interval plus an optional per-character delay for
displayed ruby annotation text (not base-text characters or unannotated kana).
The extra delay defaults to zero to preserve existing playback speed and is saved
with the other browser settings.
The playback line reserves a fixed height from the selected base, kanji, and ruby
sizes. Character types, ruby presence, and per-frame horizontal fitting do not
move the playback controls or settings below it.

Coverage, settings, immutable submission drafts, and pending receipts are stored
in IndexedDB, independently of the existing editor's localStorage quota. The
playback bookmark is saved per document range by sentence and source-character
position. Reloading or returning to a range resumes paused; boundary changes map
the position to its new frame. A removed
sentence falls back to the beginning of the range. The restart button explicitly
returns to the first frame without clearing viewing coverage. The
global ready-to-submit count uses compact eligible-sentence summaries across all
loaded ranges, not only the displayed range. Older browser coverage is backfilled
from previously visited documents. Completed legacy coverage migrates using saved
token counts; partial legacy coverage is converted when its token mapping is still
available. Ambiguous old partial records are retained rather than guessed.
Counts use the last loaded eligible sentence sets;
the viewer reloads all ready documents before preparing a receipt, and the server
revalidates them on submission. Submission includes ready documents across all
ranges, not just the currently displayed range. Existing immutable drafts require
explicit confirmation before replacement with a new submission ID; users who
already created an Issue must mark it submitted first to avoid double counting. The
partial-document action gathers unseen frames across visited
documents. Submit complete documents as `rapid_review_receipt` JSON in an Issue
(inline or attached). Mark the draft submitted only after creating the Issue.
Retries retain the same submission ID; each submission increments each document
at most once. Schema 2 receipts record sentence IDs with boolean values. Legacy
schema 1 receipts remain accepted independently of their reading hashes. Review
sync validates sentence ownership and coverage of current eligible IDs, records
receipts under `data/state/rapid_review/`, and closes accepted Issues. A separate
green Corpus Map badge shows historical visual-review counts, not correction
counts or a guarantee that the current revision has been checked.

Tests: `node --test tests/rapid_review.test.mjs` and
`PYTHONPATH=src python -m unittest discover -s tests -p test_rapid_review.py`.
