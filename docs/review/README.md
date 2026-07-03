# Review UI Source

This directory is the source area for the static review UI.

Current intended split:

- `web/review/`: editable source files for the review app
- `docs/`: publishable static output for GitHub Pages

The Python pipeline should not depend on frontend tooling living here. The
review app should read immutable review-pack JSON and emit review-submission
JSON that can be returned through GitHub Issues.

After a pipeline stage updates the review pack and manifest, publish only those
generated artifacts with:

```bash
./publish-review
```

The helper stages and commits `docs/review/manifest.json` plus review-pack JSON
files referenced by that manifest, then runs `git push`. Use
`./publish-review --dry-run` to inspect the exact path set first.

Tracking policy:

- `docs/review/` is intentionally tracked because GitHub Pages serves it
  directly.
- source review packs and pipeline intermediates under `data/` are generated
  local state and remain ignored by `.gitignore`.
- if `docs/review/manifest.json` or pack JSON changes only because of a
  regenerated timestamp or object-key ordering, discard that noise instead of
  committing it.
