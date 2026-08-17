# Review UI Source

This directory is the ignored staging area for the generated static review UI.

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

The helper generates the complete site under `docs/review/`, stages that output
on the `gh-pages` branch, and runs `git push`. Generated files in this directory
are intentionally not tracked on the main branch. Configure GitHub Pages to
deploy from `gh-pages` / root. Use `./publish-review --dry-run` to inspect the
exact path set first; note that it still regenerates this ignored staging area.
