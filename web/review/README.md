# Review UI Source

This directory is the source area for the static review UI.

Current intended split:

- `web/review/`: editable source files for the review app
- `docs/`: publishable static output for GitHub Pages

The Python pipeline should not depend on frontend tooling living here. The
review app should read immutable review-pack JSON and emit review-submission
JSON that can be returned through GitHub Issues.
