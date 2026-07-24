# Alphabetic Entity Judge Evaluation

`gold_v1.jsonl` is a manually labeled, deliberately simple regression set for
the entity-level decision that drives provisional sentence skips.

Policy represented by the labels:

- `in_scope`: an entrenched abbreviation, technology term,
  service, or broadly established name that routinely appears in Latin script
  inside modern Japanese
- `out_of_scope`: a one-off or obscure project, organization, product, shop,
  person, event, foreign title, code identifier, metadata fragment, or noisy
  residue whose validity in context does not justify routine reading-review
  effort

The important distinction is not whether a string is a legitimate proper name.
A legitimate but obscure local name is normally `out_of_scope`. The resulting
unit is only provisionally skipped and remains visible to a human in Bulk
Review.

Numeric measurements with an explicit recognized unit, such as `1kg` or
`30km`, are resolved deterministically before this judgment and are therefore
excluded from this LLM evaluation.

The set contains 95 historical cases, approximately balanced by expected label. The
`development` split contains 72 cases and the `holdout` split contains 23.
Twenty-three cases are marked `boundary`; disagreement on those cases should be
inspected rather than treated as an absolute specification failure.

Primary reporting should include:

- accuracy and confusion matrix
- false `in_scope` count and rate, because these cases create avoidable reading
  and human-review work
- results split by `clear` versus `boundary`
- development and holdout results separately

The checked-in JSONL is authoritative and self-contained. Its contexts were
copied from local historical batch artifacts, which are intentionally ignored
by Git, and its expected labels were assigned manually without using the old
LLM decision. Edit the JSONL and summary directly when the policy changes; the
test suite checks the intended size, label counts, splits, and required fields.
