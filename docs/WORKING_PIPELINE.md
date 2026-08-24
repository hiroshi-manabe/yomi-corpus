# Working Pipeline Draft

This document is a working summary of the current intended pipeline. It is more
concrete than the general design note and intentionally reflects the latest
decisions, even where details are still unsettled.


## 1. Current Direction

The project should stay simple.

At this stage:

- the original source JSONL should remain unchanged
- units should be derived from source documents and stored sequentially
- unit-local analysis should live inside each unit record rather than being
  split into separate `candidate`, `decision`, and `review` tables
- deterministic rules should run first
- the LLM should only be used for units that deterministic rules cannot mark as
  "human-check only"
- the final authority is still human review


## 2. Scope of the Main Judgments

The pipeline now has two different judgment granularities:

- sentence-like units for non-target status and yomi correctness
- batch-level alphabetic entities for the minor-alphabetic problem

For sentence-like units, the main questions are:

1. Is this non-target material?
   Examples:
   - old kana
   - old orthography
   - classical Japanese
   - kanbun
   - foreign-language text
   - garbled text

2. Is the current mechanically generated yomi correct with high confidence?

For Latin/alphanumeric material, the main question is:

- which entity types in the batch should be treated as naturally acceptable in
  modern Japanese context, and which should be treated as out of scope?

The practical goal is not perfect theoretical classification. The goal is to
spend effort where it helps and avoid the long tail that would consume a large
fraction of time for little corpus value.


## 3. Data Model

## 3.1 Source documents

The source JSONL stays as-is. No transformed "document record" needs to replace
it. It is enough to maintain stable references back to the original input.

Each batch run should still assign internal document IDs such as:

- `doc_id`
- `source_file`
- `source_line_no`

The original JSON payload is not rewritten.

## 3.1.1 Batch artifacts vs. global state

The pipeline should distinguish:

- immutable batch-local artifacts
- cross-batch global state

Batch-local artifacts include generated yomi, safety evidence, LLM readings,
and human review packs.

Cross-batch global state includes reusable reading evidence, harvested repair
rules, and decoder-model metadata.

The batch artifacts describe one run. The global state carries knowledge
forward to later batches.

## 3.2 Units

Units are sentence-like spans derived from each source document.

They should be sequential inside a document.

Required unit fields:

- `doc_id`
- `unit_id`
- `unit_seq`
- `char_start`
- `char_end`
- `text`

Recommended reference fields:

- `source_file`
- `source_line_no`
- `split_rule_version`

Important invariant:

- `text` must always be recoverable from the original document text by
  `char_start` and `char_end`

No explicit previous/next-unit context fields are needed for now.

If the pipeline later chooses not to use some units, that should be expressed
through flags, not by changing the unit order.

## 3.3 Unit-local analysis

Rather than separate `candidate`, `decision`, and `review` record families,
each unit should contain nested analysis blocks.

Suggested shape:

```json
{
  "unit_id": "ja_cc_level2:0000000123:u0007",
  "doc_id": "ja_cc_level2:0000000123",
  "unit_seq": 7,
  "char_start": 418,
  "char_end": 457,
  "text": "毎週水曜日はお昼のコンサート「Concerts de Midi（ミディ・コンサート）」が開催されています。",
  "analysis": {
    "mechanical": {
      "non_target": {
        "value": false,
        "certain": false,
        "signals": []
      },
      "minor_alphabetic_sequence": {
        "value": true,
        "certain": false,
        "matches": ["Concerts de Midi"]
      },
      "yomi": {
        "rendered": "...",
        "certain": false,
        "sudachi": {},
        "ngram_decoder": {}
      }
    },
    "llm": {
      "non_target": null,
      "minor_alphabetic_sequence": null,
      "yomi_is_correct": null,
      "yomi_repair": null
    },
    "human_review": {
      "pass1": null,
      "pass2": null
    }
  }
}
```

This is intentionally denormalized. Simplicity is more important than perfect
relational cleanliness at this stage.


## 4. Unit Segmentation

The source JSONL records may contain long document text. Annotation and review
should happen at smaller sentence-like units.

Initial segmentation policy:

- split mainly on `。`, `！`, and `？`
- optionally respect source line breaks if they are already meaningful
- keep exact document offsets for each unit

The unitizer does not have to be linguistically perfect yet. It only needs to
be stable, reversible, and good enough for batch processing.


## 5. Mechanical First Pass

The first pass should process a small batch of documents, for example:

- 100 documents per run initially

That size is small enough to inspect manually and large enough to expose common
failure patterns.

For each unit, the mechanical pipeline should produce:

### 5.1 Reading analysis

The mechanical pass produces reading candidates and evidence, not a scope
decision. Its signals include:

- Sudachi behavior
  The current mechanical baseline should use Sudachi B-mode with
  `sudachidict_full` rather than default C-mode/core dictionary behavior, so
  compounds are first split into middle units while full-dictionary lexical
  entries such as `断捨離/ダンシャリ` remain available before hybrid refinement.
  Sudachi full is still only a candidate source: invalid non-kana readings must
  be filtered downstream, and tokens spanning spaces need explicit handling if
  they matter operationally.
- The yomi renderer must not emit one ruby-bearing token whose surface spans an
  ASCII/NBSP space. If Sudachi full returns a token such as
  `Led Zeppelin/レッドツェッペリン`, split it on spaces, preserve the space as a
  non-ruby token, and look up each component independently. If all component
  readings concatenate to the full reading, use them. If exactly one component
  can be inferred as the residual of the full reading after subtracting the
  other component readings, use that residual. Otherwise use the independently
  available component readings and leave unresolved components without ruby.
  This is a pipeline-wide invariant, not only a rendering rule. LLM reading
  targets, review interaction regions, strong-repair output, and finalized
  canonical tokens must preserve each source whitespace boundary. Repair output
  omits whitespace items; the application layer restores the original spaces as
  separate non-ruby tokens.
- Apply the same structural policy to Japanese middle dots (`・` and `･`) and
  parentheses in every Sudachi POS, including proper names and kana-only names
  such as `ラ・カンパネラ`. Split at the separator and look up each
  non-separator component independently. Known abbreviations
  such as `（株）`, `（有）`, `（社）`, and `（財）` use short readings. If the
  parenthesized content is a one-character weekday abbreviation, normalize it
  contextually to `月/ゲツ`, `火/カ`, `水/スイ`, `木/モク`, `金/キン`,
  `土/ド`, or `日/ニチ`; standalone occurrences retain their ordinary
  context-dependent readings. If the
  component readings conflict with the full token reading, leave the affected
  lexical components unresolved for LLM reading generation instead of guessing.
  Other punctuation in proper-name tokens remains intact because it can be part
  of an established spelling and reading, as in `ZE:A`, `M&A`, or `COVID-19`.
- Treat `〜` and `～` attached to a non-proper-name lexical token as expressive
  vowel lengthening while preserving the source surface: for example,
  `な〜/ナー` and `う～ん/ウーン`. Do not apply this rule to standalone wave
  marks, numeric ranges, or proper names.
- N-gram decoder behavior
- script and orthography heuristics

The exact rules are intentionally deferred until reviewed data exists.

For the current hybrid yomi policy, supported decoder-driven reading changes can
be used broadly. If the decoder differs from Sudachi on the same surface span
and the exact decoder entry has real N-gram support, the hybrid output may use
the decoder reading as a tentative override. Unigram-only fallback output should
not override Sudachi.

The decoder is expected to apply a count threshold before reporting order-2
support. In downstream yomi-corpus rules, `piece_orders[0] >= 2` therefore
means repeated support in the decoder corpus, not merely one observed
transition. A wrong candidate may still rank high, but it should not become
trusted support if its relevant boundary is backed only by a singleton 2-gram.

When the hybrid strategy splits one Sudachi token into multiple decoder entries,
each later decoder entry must have cross-boundary support: its first piece order
must be at least 2. Internal support after an order-1 boundary is not enough to
justify replacing Sudachi's whole-token segmentation.

The default strategy is `ngram_grouping_preferred_v1`. It may merge contiguous
Sudachi tokens when the top decoder entry covers exactly the same source span
and every decoder `piece_order` is at least 2. It does not split a Sudachi token
to follow decoder boundaries. This lets strong evidence repair under-segmented
or malformed Sudachi output such as `戦/セン 争/争`, without accepting a merge
whose first piece has only unigram support.

For stable two-kanji confidence experiments, the decision unit should remain the
hybrid rendered token, normally inherited from Sudachi plus accepted hybrid
overrides. Decoder evidence should be projected onto that token's character
span. Decoder-only subpieces such as `古/コ 本屋/ホンヤ` must not make the
hybrid token `古本屋/フルホンヤ` safe. A stable two-kanji token may forgive only
its own missing support; it must not forgive the boundary into a following
non-stable token such as `入っ/ハイッ`.

Stability should be judged from the raw SudachiDict CSV surface-to-reading
inventory, not from `Dictionary.lookup()` or the decoder. Include ordinary
entries and component-only entries whose left/right connection IDs are `-1`.
The token is stable only when that raw inventory has exactly one reading for
the surface. POS should not be a gate: proper nouns such as `群馬/グンマ` can be
stable if unique, while common/proper ambiguous surfaces such as `大麻`
(`タイマ` and `オオアサ`) are not stable.

After the hybrid strategy, the pipeline may apply a small post-hybrid repair
memory to the rendered yomi string. The first implementation treats all repair
rules as regular-expression substitutions over whitespace-separated rendered
pairs, using `config/yomi/post_hybrid_repairs.tsv`. This layer is for known
systematic fixes such as `若しくは/モシクワ -> 若しくは/モシクハ` and
`身近/ミジカ -> 身近/ミヂカ`. It also normalizes Sudachi's formal
`私/ワタクシ` default to the more ordinary `私/ワタシ`; other readings remain
available as review candidates. This layer does not make a unit safe by itself. Each
application must be logged in `analysis.mechanical.yomi.post_hybrid_repairs`
with the rule ID, matched string, replacement, count, and source.

Numeric runs should be grouped and excluded from normal yomi reading decisions.
For example, Sudachi-style `2/ニ 0/レイ 2/ニ 1/イチ` should become `2021/`.
Number pronunciation is a separate future module, not part of the current yomi
pipeline.

Finalized canonical yomi is stored as versioned compact JSON token arrays:
`{"token_schema_version":1,"tokens":[[surface,reading],...]}`. A rendered
`surface/reading` string is only a compatibility or editor projection. This
avoids ambiguity for literal `/`, whitespace, and backslashes.

The canonical token format should be structurally validated
before any automatic or LLM `OK` is trusted:

- if `surface` contains kanji or Latin letters, `reading` must be non-empty and
  contain only katakana plus `ー`
- if `surface` is digits only, `reading` must be empty; `2021/` is valid, but
  `2021/2021` is invalid
- otherwise, `reading` must equal `surface` with hiragana converted to
  katakana and all non-kana characters left unchanged; for example `です/デス`
  and `。/。` are valid

Finalization enforces this deterministically for symbol/kana-only tokens, so
legacy spoken symbol readings such as `～/カラ` normalize to `～/～`. Invalid
kanji/Latin readings are never guessed: they may be recovered only when saved
human final-review evidence supplies exactly one structurally valid reading.

Historical finalized files are migrated with
`scripts/migrate_finalized_yomi_tokens.py`. Dry-run is the default. Apply mode
requires a backup directory, stages every converted file first, and replaces
none of them if any token stream cannot be aligned exactly to source text. A
post-migration dry run must report zero anomalies and zero changed units.

This validation is separate from yomi correctness. A structurally invalid unit
remains ordinary reading-review work and cannot be accepted merely because
another signal considers it low risk.

Original source whitespace must be preserved code point for code point.
Sudachi and decoder adapters may temporarily convert ASCII space `U+0020` to
NBSP `U+00A0`, but returned token boundaries must immediately be projected onto
the original text. Compact token arrays therefore retain ASCII space, source
NBSP, full-width space, tabs, and other whitespace exactly. The editable
compatibility projection escapes literal slash as `\/`, backslash as `\\`,
ASCII space as `\s`, source NBSP as `\u00a0`, and full-width space as
`\u3000`. See `SOURCE_SURFACE_MIGRATION.md` for the implementation and
existing-artifact migration plan.

Yomi review should prioritize reading correctness over ideal segmentation.
Katakana expressions may be over-split by the current analyzers, but this is not
a failure if the readings remain correct. A later token-merge layer can be
added if downstream consumers need cleaner segmentation.

The first yomi auto-accept pass should run after mechanical yomi generation and
write a separate `units.yomi.auto_accept.jsonl` artifact. It should mark
`analysis.mechanical.yomi.auto_accept.value=true` only when the unit has no
unresolved non-numeric readings, Sudachi and the decoder agree on the rendered
output, and either the decoder's top candidate is supported from start to end by
repeated N-gram evidence or the same support check passes after the stable
two-kanji relaxation. The selected criterion should come from the batch's
explicit `yomi_policy.auto_accept_profile`, not directly from the track name.
Tracks only provide defaults: `working` should default to `strict`, while `dev`
can default to `stable_two_kanji` so the relaxation is exercised under
realistic pipeline runs. It remains an audited criterion: accepted rows should
still record which profile was used and whether the stable two-kanji relaxation
was involved. This is a review-skip flag, not a general proof of correctness.

N-gram confidence remains a debug metric rather than a pipeline decision. The
current useful experiment is to split only on `、`, reject alphabetic-containing
units, keep empty-reading kanji-like content inside the span as unsafe, exempt
kana-only or symbol-only adjacent boundaries, and require all other adjacent
entry boundaries to have the later entry start with `piece_orders[0] >= 2`.
Coverage should be interpreted by character count and by kanji-like span
coverage, not only by raw span count.

The stable two-kanji relaxation is a second debug experiment layered on top of
that idea. It starts from the hybrid rendered output, asks whether each hybrid
token has projected repeated decoder support, and then allows a token-local
fallback only for two-kanji compounds that have exactly one reading in the raw
SudachiDict CSV inventory. This is meant to reduce false negatives caused by
sparse decoder vocabulary, not to trust decoder over-segmentation.

As a profile, this relaxation should remain available on any track. For example,
`dev` can run with `auto_accept_profile=off` or `strict` for conservative
debugging, and `working` can later run with `stable_two_kanji` once the full
production pipeline is ready. The chosen profile must be stored with the batch
so reruns remain reproducible even if track defaults change.

Use separate thresholds for reading changes and review-skipping safety. A
decoder reading that differs from Sudachi and has repeated 2-gram support can be
treated as an ordinary tentative override signal; a high error rate is
acceptable because review is still the default. A safe auto-accept signal should
be much more conservative: Sudachi and decoder should agree, and the relevant
span should be supported from start to end by the stricter repeated-2-gram
boundary rule.

### 5.2 Alphabetic and mixed-script material

Alphabetic and mixed-script tokens follow the same mechanical and LLM reading
path as Japanese-script tokens. Deterministic rules may provide standard letter
names, unit readings, or token boundaries, but no alphabetic whitelist,
blacklist, or LLM entity classifier decides corpus scope. Uncertain readings
remain ordinary unresolved targets for Bulk Review.

### 5.3 Mechanical yomi

The mechanical pass should produce:

- one best yomi candidate for the full unit
- raw agreement and confidence signals only; no sentence-level `certain` flag
  yet

This should be based on Sudachi plus `yomi-decoder` plus mechanical agreement
signals that can later be calibrated against reviewed data.

Segmentation policy for yomi evaluation:

- if the readings are correct, overly fine segmentation should not by itself be
  treated as an error
- examples like `やってしまった` becoming `やっ/ヤッ て/テ しまっ/シマッ た/タ`
  are acceptable as a morphological-analysis choice
- evaluation should focus on reading correctness first, and segmentation only
  secondarily when it causes a real reading or usability problem


## 6. Interpretation of "Certain"

For reading tasks, "certain" is evidence-backed and local rather than a broad
sentence-level scope label.

Current policy:

- do not assign `certain=true` mechanically for yomi safety
- collect raw features now and define certainty rules only after reviewed data
  accumulates

The effective branch is therefore yomi correctness and uncertainty only. Scope
remains an explicit human disposition in Bulk Review.


## 7. Alphabetic and Mixed-Script Readings

Treat these as reading problems, not scope-classification problems. Standard
acronyms, units, and dictionary-supported names may use deterministic
candidates. Anything unresolved goes through the normal targeted reading LLM
and remains editable by the reviewer.


## 8. Classical Japanese and Kanbun

Scope classification for this material is now a human-review decision.

Current working idea:

- rely first on how well Sudachi and the N-gram system can analyze the unit
- combine that with orthographic and script-level heuristics
- store those signals as features for later learning
- expose uncertain readings to ordinary Bulk Review

Potential signals:

- old kana
- old orthography
- unusual auxiliary patterns
- script mixtures that rarely occur in modern prose
- systematic analysis failures from Sudachi or the decoder

The exact decision boundary is still unclear and should be refined by looking at
real examples and failure cases.


## 9. LLM Stage

The active LLM path asks one primary question: for each unresolved kanji/Latin
target, what reading does the LLM assign in context? Escalated Repair is a
separate later task for rejected local spans.

The LLM should no longer be asked to decide yomi correctness directly as
`OK/Review/Skip` in the main path. That label set remains useful as historical
eval context, but the production direction is more diagnostic: compare an
independent LLM reading against the mechanical Sudachi/hybrid reading, then use
agreement and disagreement as review-routing signals.

### 9.0 Retired Scope-Triage Design

The remainder of this subsection records the former classifier semantics only
for migration context. It is not active pipeline behavior. New batches perform
no semantic machine `Keep`/`Skip`/`Exclude` classification; all prepared units
proceed through reading generation and ordinarily start Bulk Review at implicit
`Keep`.

There is one deterministic encoding-anomaly exception. Text containing CJK
Radicals Supplement (`U+2E80-U+2EFF`) or Kangxi Radicals
(`U+2F00-U+2FD5`) starts Bulk Review at recoverable `Skip`. Such characters can
look like ordinary kanji while preventing reliable tokenization, as in `⻑` or
`⽇`. The original text and mechanical yomi remain available, and the reviewer
can explicitly toggle the unit back to `Keep`; this rule never produces
terminal `Exclude`.

Scope triage is a compact three-way task over raw text. The model returns
exactly one token:

- `Keep`: process the unit normally
- `Skip`: omit recoverable non-target material from the corpus while retaining
  it for human confirmation and possible later restoration
- `Exclude`: recommend terminal exclusion of sensitive material

Non-lexical units containing no letters or numbers, such as a standalone `！`,
`？`, or emoji, bypass the LLM and receive a deterministic `Keep`. They are
harmless corpus material and do not justify a paid scope decision. This rule
does not apply to Latin, Greek, Cyrillic, or other alphabetic text, which still
uses the existing alphabetic and scope-triage policy.

Provisional skips still retain and display their mechanical/hybrid ruby in
Bulk Review. If no reading span is editable, the UI uses the full Python-built
ruby-token representation as a read-only fallback rather than showing plain
text. A later strong repair may change segmentation, but deterministic token
rules run after the LLM result: in particular, a replacement segment whose
surface is numeric-only always receives an empty reading regardless of the
reading proposed by the model.

Historical symbol-only units that predate this rule can be reconciled with
`scripts/restore_symbol_only_skips.py`. Run it without `--apply` first, then
apply only an anomaly-free report. The migration preserves the hybrid yomi and
skip history, records deterministic restoration provenance, and does not count
the restoration as a human correction.

`Skip` covers foreign prose, old Japanese prose, kanbun, Chinese, garbled text,
spam, and similar non-target material. The prompt should avoid project-internal
terms such as "kobun/kanbun stage" except as examples; the operational concept
is simply target vs. non-target.

Obvious source corruption is also non-target. Skip text with severe,
nonstandard OCR damage or flattened ruby in which base characters and readings
have been interleaved into the source, rather than trying to reconstruct the
author's intended sentence. This rule does not cover an isolated ordinary typo
or routine web noise; those remain modern Japanese target text.

`Exclude` covers privacy or reputational-risk material that identifies a
private person together with sensitive negative information. Examples include
arrest, criminal suspicion, accusations, scandals, disciplinary action,
illness, or similar private/reputational details. This is a conservative
labor-saving rule: the corpus has enough ordinary modern Japanese text, so when
the former scope-triage prompt was unsure about this risk, it chose `Exclude`.

All machine labels remain provisional through Bulk Review. `Skip` is a durable
but recoverable state: it remains visible as subdued ruby in Corpus Map and can
be restored through the correction workflow. `Exclude` becomes terminal only
after explicit human confirmation. Finalized browsing then shows a content-free
`Removed` tombstone at the original position, while the original text is omitted
from published packs and archives, search, evaluation exports, corpus output,
and decoder training. The tombstone keeps only stable identity, order, reason
category, and confirmation provenance for audit and idempotency.

Scope status does not suppress reading analysis. `Keep`, `Skip`, and `Exclude`
all receive the same mechanical yomi generation, deterministic per-target
safety checks, and LLM reading calls for unresolved targets. The labels affect
the default review controls and final disposition, not the upstream yomi data
shape. The small extra cost is preferable to maintaining a separate,
failure-prone skipped-unit reconstruction path.

Retrospective exclusion follows the same path and must be dry-run-first,
idempotent, and atomic across final/skipped artifacts plus published archive and
search shards. Old immutable decoder models are marked as predating the
exclusion and are no longer selected; they are not rewritten in place. The
migration requires exact unit IDs and must not widen a few sensitive units into
a whole-document exclusion. For document 13, only the previously reviewed
units about alleged violations, arrest, or related private-person reporting are
excluded; its other finalized and recoverably skipped units remain unchanged.

`Skip` should be decided by the dominant language and style of the unit, not by
isolated orthographic markers. A modern Japanese sentence remains target text
when old kana, old kanji, kanbun, Chinese, or foreign text appears only inside
short titles, names, bibliographic strings, or incidental quoted fragments. For
example, a modern bibliographic explanation that mentions titles such as
`東京掃苔録`, `東都掃苔記`, or `日本醫事新報` should not become `Skip` solely
because those titles contain old characters.

Longer quotations and citations use a stricter labor-saving exclusion rule. If
the unit contains even one full sentence of old kana, kanbun, Chinese,
foreign-language text, or other non-target running text, label the whole unit
`Skip`. Modern Japanese is abundant enough that losing the surrounding modern
frame is acceptable, and this avoids a later review pass over whether text is
target or non-target. The exceptions are compact embedded material such as
proverbs, fixed expressions, short titles, proper names, journal/book names, and
bibliographic labels; those do not make the unit `Skip` by themselves.

Bulk Review presents two compact, mutually exclusive toggle buttons: archive
box (`Skip`) and shield-with-X (`Exclude`). `Keep` is implicit when neither is
active. Clicking the active draft choice returns to `Keep`; selecting one
choice clears the other. The sentence immediately changes to subdued gray or
warning red. The controls have tooltips and accessible labels. The
range-selection control is removed; it has not proved useful in operation. Confirming
`Exclude` requires an additional submission confirmation because its lifecycle
is intentionally different from recoverable `Skip`.

The Bulk Review and Escalated Repair queues each have a persisted `Take next`
document-count selector with values from 5 through 50 in increments of 5. Each
queue remembers its own browser-local selection, defaults to 5 when no valid
preference exists, and naturally takes fewer documents when the queue contains
fewer than the selected count. Escalated Repair can use the same range because
a document often contains only a small amount of actual repair work.

Historically this task used a separately configured profile and was only a
scope gate; it was not expected to notice yomi errors.

### 9.0.1 LLM Reading Generation

LLM reading generation is the main yomi-quality signal being developed now.
The default input is plain source text with exactly one target marked by
`**...**`. The model returns JSON with exactly one key: the marked target
surface, and the value is the target's reading in hiragana.

Example:

```text
お**話**しします。 -> {"話":"はな"}
```

The marked target should normally be a kanji run or Latin-containing target
inside a Sudachi/hybrid token, not an arbitrary whole sentence. The prompt must
tell the model to use unmarked text only as context and to exclude following
kana/okurigana from the target reading.

The first implementation should stay deliberately simple:

- start from the stored Sudachi/hybrid token sequence
- render a plain marked-source context for the prompt
- optionally store a no-space furigana context as debug metadata
- split each annotated token into target chunks by furigana alignment
- query only chunks that need an independent reading signal
- keep the original full token yomi as the canonical artifact

Deterministic skip hooks can reduce query volume:

- skip exact target surfaces whose current reading matches the dominant reading
  in the stable surface-reading lexicon pinned to the batch's decoder model
- later, skip targets whose N-gram evidence is overwhelmingly dominated by one
  reading, for example at least 99.5% of observed support

Those skips are query suppression, not final acceptance by themselves. Their
source and reason should be logged so later audits can separate mechanical
confidence from LLM agreement.

LLM reading results should be applied as comparison metadata:

- `match`: LLM reading equals the current mechanical reading
- `mismatch`: LLM reading differs from the current mechanical reading
- `missing_or_parse_error`: the response could not be trusted
- `skipped`: deterministic rule suppressed the query

Agreement is a candidate signal for bulk review or future auto-acceptance, but
it should remain distinguishable from N-gram safety, stable surface-reading
corpus safety, and human approval. Disagreement should route the target or unit
to focused review or later repair. Ambiguous cases such as `辛い` should
eventually be handled by explicit ambiguity policy rather than hidden inside an
`OK` label.

This per-target design is intentionally less ambitious than asking the LLM to
repair whole sentences. It avoids relying on the model's token-boundary
judgment, which has been weak in earlier experiments. Larger-span candidate
matching, such as asking for the reading of `給料日直後` and choosing among
Sudachi candidates, can be added later if simple per-token/per-chunk queries
miss too many segmentation errors.

The prompt is short enough that prompt-cache tuning is not a priority for this
task. Accuracy, parse stability, and clean comparison metadata matter more.

### 9.0.2 Per-Target Safety Evidence

The current whole-unit auto-accept experiments ask whether an entire sentence
or comma-span is safe, for example because Sudachi and the decoder agree and
the span has repeated N-gram support. A more useful future direction is
per-target safety: every yomi-bearing target gets its own evidence record, and
only targets without enough evidence are sent to the LLM or highlighted for
human review.

Candidate per-target signals:

- `safe_by_stable_surface_lexicon`: the model-pinned corpus artifact contains
  the exact target surface with a dominant reading at the configured count and
  share thresholds, and the mechanical reading matches it.
- `safe_by_local_stable_span`: a contiguous window of two through six final
  canonical tokens matches a segmentation-pooled stable surface/reading row,
  and that exact token-surface segmentation was observed for the dominant
  reading; all targets fully contained in the window skip the LLM. Thus
  `月/ガツ 末/マツ` evidence is not reused for exact-token `月末`.
- Canonical token boundaries may intentionally override Sudachi morphology for
  an explicit exact sequence. For example, `皆/ミナ 様/サマ` is normalized to
  `皆様/ミナサマ` after hybrid generation and again as a finalization backstop.
  This is a selected corpus convention, not an automatic compound joiner.
- `safe_by_corpus_frequency`: a trusted training/evidence corpus shows the
  same exact full-token `(surface, reading)` pair, or target-level pair as a
  fallback, dominates with at least 95% share and a minimum count threshold.
- `safe_by_ngram`: the target's local reading is supported by repeated N-gram
  evidence, not just by a one-off transition. Decoder refreshes now emit a
  diagnostic `ngram_reading_transitions.tsv` sidecar that measures each adjacent
  surface pair's competing reading pairs. Its default strong-transition gate is
  at least five observations and 95% share. This signal is not yet active for
  auto-acceptance: the evaluation must first specify how both incoming and
  outgoing transitions around a target are combined.
- `safe_by_llm_agreement`: an independent LLM reading query returns the same
  reading as the mechanical reading.
- `unresolved`: no safety signal applies, the LLM disagrees, or the LLM result
  is missing/malformed.

The unit-level status should be derived from target-level evidence:

- if every yomi-bearing target has a safety signal, the unit can enter bulk
  review or a later auto-accept experiment
- if any target is unresolved, the unit remains reviewable and the unresolved
  targets should be highlighted

In the human UI, this should support two review modes without changing the
underlying data. Reviewers can skim unhighlighted units quickly, while units
with highlighted unresolved targets get focused attention. The highlight should
encode evidence strength, not just a binary bad/good label. For example,
LLM-only agreement may be visually weaker than stable dictionary plus corpus
frequency support.

Safety signals are not final truth. They are risk labels. The output must keep
the source of each signal, counts, thresholds, and corpus/model versions so
future audits can estimate false-accept rates separately for dictionary
evidence, corpus-frequency evidence, N-gram evidence, LLM agreement, and human
approval.

Corpus-frequency evidence is explicitly probabilistic. For example, a training
corpus may show `大麻/タイマ` as overwhelmingly dominant, while a rare place name
reading such as `おおあさ` still exists. That does not invalidate the signal; it
means `safe_by_corpus_frequency` should be interpreted as "low-risk enough to
de-emphasize for bulk review," not "this surface has no other valid reading."
Rare proper-noun readings are an accepted residual risk unless later audits show
they are frequent enough to need exceptions or weaker highlighting.

The lookup first checks the full current token and reading. Thus corpus evidence
for `思っ/オモッ` can mark its contained `思/オモ` target safe even though the
corpus has no standalone `思` row. If exact full-token evidence does not pass,
the lookup next tries trailing-kana stem evidence. For a token ending in a run
of hiragana, remove that entire run and remove its exact katakana equivalent
from the reading: `思い知る/オモイシル` and `思い知っ/オモイシッ` both become
`思い知/オモイシ`. Apply the identical transform to evidence-corpus
tokens and the current candidate. Naturally kana-less surfaces stay in a
separate namespace and are never merged into a trimmed family. Target-level
frequency remains the final fallback. Signals record the selected evidence
scope, surface, reading, and normalization rule.

This normalization is deliberately not a lemma reconstruction. It can also
produce useful target-reading evidence such as `赤かぶ/アカカブ -> 赤/アカ`.
That is acceptable because the decision concerns the reading of the written
target, and both historical evidence and the current candidate use the same
transform. Ambiguous stems remain blocked by the ordinary count/share policy:
in the inspected 1,303,044-token human-read corpus, `行` split between
`オコナ` and `イ`, `入` split between `ハイ` and `イ`, and `勝` split among
`カ`, `ガ`, and `マサ`, so none reached 95%. The same experiment found 1,057
of 1,297 normalized keys with at least five observations passed 95%; on the
finalized dev units, normalized evidence newly covered 200 of 8,599 target
occurrences beyond exact-token evidence.

Concrete per-target safety records should live under the unit's yomi analysis,
for example `analysis.safety.yomi.targets[]` or an equivalent versioned path.
The exact path can change during implementation, but the record shape should be
stable enough for review UI and audit tools:

```json
{
  "target_id": "ja_cc_level2:0000000020:u0014:r0009c01",
  "surface": "学校",
  "token_surface": "学校",
  "target_start": 18,
  "target_end": 20,
  "token_index": 8,
  "chunk_index": 0,
  "mechanical_reading": "ガッコウ",
  "mechanical_reading_hiragana": "がっこう",
  "is_safe": true,
  "review_status": "safe",
  "highlight_level": "none",
  "accepted_signal_names": ["safe_by_corpus_frequency"],
  "status_reason": "accepted_by_corpus_frequency",
  "signals": [
    {
      "name": "safe_by_corpus_frequency",
      "value": true,
      "count": 337,
      "surface_total_count": 337,
      "share": 1.0,
      "threshold": 0.95,
      "min_count": 5,
      "evidence_artifact": "..."
    }
  ]
}
```

A failing signal should also be recorded, because negative evidence is useful
for debugging and review:

```json
{
  "surface": "方",
  "mechanical_reading": "カタ",
  "is_safe": false,
  "review_status": "unresolved",
  "highlight_level": "strong",
  "accepted_signal_names": [],
  "status_reason": "no_accepted_safety_signal",
  "signals": [
    {
      "name": "safe_by_corpus_frequency",
      "value": false,
      "count": 435,
      "surface_total_count": 729,
      "share": 0.5967,
      "threshold": 0.95,
      "min_count": 5,
      "evidence_artifact": "..."
    }
  ]
}
```

The `方` example is a negative control: it correctly fails the corpus-frequency
rule because its readings are split across `カタ`, `ホウ`, and `ガタ`.

Recommended fields:

- target identity and alignment: `target_id`, `surface`, `token_surface`,
  `target_start`, `target_end`, `token_index`, `chunk_index`
- current reading: `mechanical_reading`, `mechanical_reading_hiragana`
- summary flags: `is_safe`, `review_status`, `highlight_level`,
  `accepted_signal_names`, `status_reason`
- evidence: `signals[]`, where each signal stores its own source-specific
  counts, thresholds, artifact versions, model/profile, or N-gram details

`review_status` should be a small controlled vocabulary, initially:

- `safe`: at least one accepted safety signal applies
- `unresolved`: no accepted safety signal applies
- `skipped`: the containing unit is non-target material

`highlight_level` is UI guidance, not truth:

- `none`: hide/de-emphasize in bulk review
- `weak`: low-risk but worth faint display, for example LLM-only agreement if
  desired
- `strong`: unresolved or conflicting evidence; reviewer should focus here

`status_reason` is a short derived explanation, not a replacement for
`signals[]`. Examples: `accepted_by_corpus_frequency`,
`accepted_by_llm_agreement`, `no_accepted_safety_signal`, `llm_disagreement`,
or `llm_parse_error`.

The unit-level yomi safety summary should be derived mechanically from these
records, for example `all_targets_safe`, `unresolved_target_count`,
`safe_signal_counts`, and `safety_policy_version`. Do not overwrite or collapse
source-specific evidence into a single boolean; later audits need to know which
signal made each target look safe.

Implementation status:

1. Corpus-frequency evidence generation and loading is implemented.
   - Add a script such as `scripts/build_yomi_surface_reading_stats.py`.
   - Input is a configured source corpus path, initially
     `/panfs/panmt22/users/hmanabe/yomi-decoder/data/raw/core_SUW_yomi_final.txt`.
   - Output a stats artifact plus a manifest with source path or ID, size,
     checksum when feasible, mtime, normalization settings, filters, script
     version, and generation time.
   - Add small committed fixture corpora and loader/generator tests.
   - Decoder refreshes must additionally generate model-local frequency stats
     from the same base corpus and finalized batch exports used to build that
     decoder model. A batch's pinned `decoder_model_dir` selects both decoding
     artifacts and frequency evidence. The static base-corpus stats path remains
     a compatibility fallback for older model directories only.
   - Decoder refreshes also generate `stable_surface_readings.tsv` and its
     manifest. Its builder enumerates contiguous one-to-four-token spans,
     groups all tokenizations under the concatenated surface, and emits only
     surfaces whose dominant concatenated reading has at least five observations
     and at least 95% share. Segmentation counts are retained for audit. This
     artifact is the runtime stable-surface signal. New processing resolves it
     from the batch's pinned decoder model and does not fall back to raw Sudachi
     uniqueness when an older model lacks the artifact.
   - Decoder refreshes additionally generate
     `ngram_reading_transitions.tsv` and its manifest from the same ordered
     corpus inputs. The sidecar retains every observed reading variant for
     surface bigrams occurring at least five times, allowing candidate readings
     to be rejected as unseen, non-dominant, below count, or below 95% share.
     It is diagnostic until an evaluated target-level transition policy is
     enabled.
   - Exact-token corpus-frequency acceptance requires confirmation from that
     segmentation-pooled artifact. This prevents a token-local majority such as
     `一日/ツイタチ` from suppressing competing `一/イチ 日/ニチ` evidence.
2. `src/yomi_corpus/yomi/safety.py` now implements pre-LLM deterministic
   per-target safety.
   - Before safety evaluation, standalone uppercase Latin letters use their
     Japanese letter names for all A-Z, including full-width forms. Lowercase
     unit symbols and multi-letter established forms retain their own readings.
   - Reuse the same target extraction logic as `llm_readings.py` so safety
     records and LLM queue items share stable target IDs.
   - Build deterministic per-target records with stable dictionary and
     corpus-frequency signals first.
   - Scan adjacent windows of two through six final canonical tokens against
     the pinned stable-surface lexicon. An accepted window marks every covered
     target safe with auditable surface, reading, count, share, token bounds,
     and artifact version. This handles locally conclusive phrases such as
     `数/スウ 日/ジツ 後/ゴ` even when an unrelated token blocks whole-unit
     auto-acceptance.
   - Standalone lower-case `w`/`ｗ` runs, for example `ｗ` or `ww`, are treated
     as internet laughter markers. They are marked safe with `No ruby` as the
     preferred candidate and skipped by the LLM reading queue.
   - Keep that rule narrow: uppercase `W`, embedded alphabetic strings, and
     lexicalized cases such as `W主演`, `W杯`, `Wii`, `Web`, or `WiFi` must stay
     normal yomi/alphabetic targets.
   - A Sudachi token whose first three POS fields are
     `補助記号,ＡＡ,顔文字` is accepted deterministically as one
     `surface/カオモジ` token and omitted from the LLM reading queue. The narrow
     exception is a surface consisting entirely of Japanese lexical characters
     inside one matching pair of ASCII or full-width parentheses. Thus
     `（笑）`, `（泣）`, `（汗）`, and `（苦笑）` retain their semantic
     normalization/review paths, while genuine kaomoji containing characters
     such as `ノ`, `ツ`, or `シ` remain eligible for automatic `カオモジ`.
     Unrecognized or partially segmented kaomoji likewise remain reviewable or
     skippable rather than being joined by a speculative regular expression.
   - Known one-character semantic parentheticals are split after hybrid
     rendering so punctuation never receives ruby. The current deterministic
     forms are `（/（ 株/カブ ）/）`, `（/（ 有/ユウ ）/）`,
     `（/（ 笑/ワライ ）/）`, `（/（ 涙/ナミダ ）/）`,
     `（/（ 汗/アセ ）/）`, `（/（ 泣/ナキ ）/）`, and
     `（/（ 苦笑/ニガワライ ）/）`, with equivalent handling for ASCII
     parentheses. `株` and `有` intentionally use the short readings `カブ` and
     `ユウ`, not `カブシキガイシャ` and `ユウゲンガイシャ`. Review-pack
     and finalization normalization also repair older artifacts that stored the
     entire parenthetical as one token.
   - The yomi-reading queue stage writes `units.yomi.safety_pre_llm.jsonl` and
     `yomi_safety_pre_llm_summary.json`, then queues only targets not already
     marked safe.
   - Direct safety from raw decoder `piece_orders` remains pending. Local span
     safety instead uses the segmentation-pooled stable-surface artifact, which
     supplies explicit frequency and ambiguity thresholds.
3. Apply LLM reading results back into safety.
   - The LLM target's `current_reading` is the current hybrid rendered reading
     when the rendered token stream aligns one-to-one with Sudachi tokens; raw
     Sudachi readings are only a fallback.
   - On exact LLM/hybrid agreement, add `safe_by_llm_match` and update
     `accepted_signal_names`, `is_safe`, `review_status`, and `highlight_level`.
   - On valid LLM/hybrid disagreement, keep the target unresolved but default
     Bulk Review to the LLM candidate. The reviewer can still cycle back to
     the current hybrid candidate explicitly.
   - If a unit was already whole-unit auto-accepted, project that decision into
     each target as `safe_by_unit_auto_accept` so target-level review does not
     show false unresolved highlights.
   - On yomi-reading format/key parse errors after parser salvage, retry with
     the same prompt and task config up to 3 total attempts. Retry results
     override earlier attempts for the same item ID.
   - Prompt construction keeps the full source row in artifacts but clips units
     longer than 200 characters to 80 characters before and after the target,
     adding `…` on omitted sides. Initial calls and retries use the same rule,
     and result metadata records the applied context window.
   - On mismatch, missing result, or parse error after retry, keep the target
     unresolved and set `status_reason` to the relevant failure.
4. Materialize explicit final artifacts.
   - `units.yomi.safety_pre_llm.jsonl`: implemented deterministic target safety
     before LLM.
   - `yomi_reading_input.jsonl`: implemented unresolved targets sent to LLM.
   - `yomi_reading_retry2_input.jsonl` and `yomi_reading_retry3_input.jsonl`:
     implemented parse-error targets resent with the same prompt.
   - `units.yomi.llm_readings.jsonl`: currently implemented post-LLM artifact
     containing both LLM reading judgments and merged target safety records.
   - `units.yomi.safety.jsonl`: optional future alias/final target safety
     artifact if the review/export stages need a narrower file.
   - Summaries should count total targets, safe-by-signal counts, queued LLM
     targets, LLM agreement, LLM disagreement, parse errors, and unresolved
     targets.
5. Integrate review/debug output.
   - Add an export or UI input that highlights targets by `highlight_level`.
   - Keep old whole-unit `auto_accept` as legacy/debug until the per-target
     safety path is trusted enough to replace it.

Suggested milestones:

1. Stats generator/loader only; inspect coverage on the source corpus.
2. Deterministic safety with stable dictionary plus corpus frequency.
3. LLM queue based on unresolved safety targets.
4. LLM agreement merged into final safety records.
5. Highlighted review/debug export.
6. Decide whether whole-unit auto-accept should be renamed debug-only or removed
   from the normal path.

### 9.0.3 Corpus-Frequency Evidence Interface

Corpus-frequency safety requires this project to read evidence derived from
the decoder/source training corpus. The main pipeline should consume a stable
stats artifact rather than reading decoder-internal runtime files directly.
That artifact can be exported by the decoder, or generated inside this project
from a configured source corpus. For current experiments, generating it here is
often more flexible because this repo can control normalization, filtering, and
threshold policy directly.

Minimum fields:

- `surface`
- `reading`
- `count`
- `surface_total_count`
- `share`
- `corpus_version` or `source_corpus_version`
- optional `exported_at`, `source_corpus_path_or_id`, `decoder_version`, and
  `normalization_version`

The generated artifact must contain separate exact and `trailing_kana_stem`
namespaces. A trailing-kana row stores the normalized surface/reading pair and
its family totals. Generation is valid only when the removed surface suffix is
non-empty hiragana, at least one kanji or iteration mark remains, and the
reading ends in the exact katakana conversion of that suffix. Entries that do
not satisfy all conditions contribute only to exact statistics.

The loader should answer questions such as: "Does this surface have one
dominant trusted reading above the configured threshold, and does the current
mechanical reading match it?" It should cache the stats in memory for a
pipeline run, but should record the source artifact path, version, threshold,
and minimum count in every generated safety summary.

If this project generates the stats, the source corpus path should be a config
value and the full corpus may live outside git. The generation command should
write a manifest containing the source corpus path or ID, checksum when
feasible, normalization settings, filters, script version, output path, and
generation time.

Current milestone-1 command:

```bash
python scripts/build_yomi_surface_reading_stats.py
```

It reads `[corpus_frequency]` from `config/yomi/default.toml` and writes the
ignored generated artifacts:

- `data/generated/yomi_surface_reading_stats.tsv`
- `data/generated/yomi_surface_reading_stats.manifest.json`

Use `--no-checksum` for quick exploratory runs when a SHA-256 of the full source
corpus is unnecessary.

Initial corpus-frequency safety defaults:

- `min_count = 5`
- `min_share = 0.95`

Below 20 observations, the share threshold still requires every observed
reading to agree; at larger counts it permits a small minority. These defaults
came from inspecting exact-boundary samples with count 5. They
are intentionally "low-risk enough for de-emphasis" thresholds, not proof that
no alternate reading exists.

Important cautions:

- A high-frequency reading can encode corpus bias or systematic annotation
  errors, so it should suppress LLM calls only when thresholds are conservative
  and the evidence remains auditable.
- The threshold should include both share and count; `1/1 = 100%` is not enough
  evidence.
- Surface normalization must be explicit. If the decoder normalizes old
  characters, Latin width, kana, or symbols differently from this project, the
  evidence artifact must say so.
- Changing the source/training corpus changes safety decisions. Pipeline
  outputs must therefore record the exact evidence artifact version for
  reproducibility.
- The raw corpus may be large or have licensing/format constraints. Prefer
  committing small fixture corpora and derived fixture stats for tests, while
  keeping full corpora and large evidence artifacts in configured local paths.

### 9.1 Triage Unit Modes

The canonical output unit remains sentence-level. However, the work item sent to
LLM triage and later yomi repair can be either the sentence or a comma-delimited
span.

Supported modes:

- `sentence`: one sentence-like unit is one triage/repair work item
- `comma_span`: split each sentence-like unit at `、` and use the resulting
  spans as triage/repair work items

The selected mode should be stored in batch state or yomi config as
`yomi_policy.unit_mode`, alongside `yomi_policy.auto_accept_profile`. LLM model
choice is separate and should be stored as `llm_policy`, a task-to-profile map.
Tracks should only provide defaults; the actual per-batch values should be
explicit so reruns stay reproducible.

Example policy:

```json
{
  "unit_mode": "sentence",
  "auto_accept_profile": "strict"
}
```

Example LLM policies:

```json
{
  "yomi_reading": "standard",
  "yomi_repair": "standard",
  "yomi_rescue": "strong"
}
```

```json
{
  "yomi_reading": "background",
  "yomi_repair": "background",
  "yomi_rescue": "background"
}
```

Allowed values for now:

- `unit_mode`: `sentence`, `comma_span`
- `auto_accept_profile`: `off`, `strict`, `stable_two_kanji`
- LLM profiles: `smoke`, `economy`, `standard`, `strong`
- LLM execution modes: `sync`, `background`, `batch`

Suggested defaults are `working={unit_mode=sentence,
auto_accept_profile=strict}` and
`dev={unit_mode=sentence,auto_accept_profile=stable_two_kanji}`.
Operators should still be able to run dev with `off` or `strict`, and later run
working with `stable_two_kanji` once that policy is trusted. Track defaults
should also choose LLM profiles per task. `yomi_reading` should default to `standard`
even on dev: cheaper mini-model mistakes create false engineering problems and
make prompt/pipeline evaluation noisier. Working likewise uses `standard` for
ordinary reading work and `strong` for rescue.
Track defaults should also choose execution modes per task. `background` should
be the normal default for both
`dev` and `working`, because it submits independent requests, polls until
completion by default, avoids slow sequential calls, and can be resumed by
rerunning `./next` after interruption. `sync` is
best for prompt exploration, smoke tests, tiny runs, and tasks where immediate
failure inspection matters. `batch` is best for very large low-urgency tasks
where latency is acceptable and cost/rate-limit behavior matters. In batch
mode, `./next` should submit any missing remote chunks, poll roughly once per
minute by default, show aggregate API request-count progress when available,
and resume from stored local state after interruption. `sentence` is the safer
unit-mode default while the pipeline is still stabilizing.
`comma_span` should be available, especially for dev experiments, because it
can raise the automatic `OK` rate and reduce downstream review volume. Its cost
is more API calls and extra reconstruction logic.

These defaults should be source-controlled configuration, not hidden Python
constants. A minimal shape is:

```toml
[tracks.working.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "strict"

[tracks.working.llm_policy]
yomi_reading = "standard"
yomi_repair = "standard"
yomi_rescue = "strong"

[tracks.working.llm_execution_policy]
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"

[tracks.dev.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "stable_two_kanji"

[tracks.dev.llm_policy]
yomi_reading = "standard"
yomi_repair = "economy"
yomi_rescue = "standard"

[tracks.dev.llm_execution_policy]
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"
```

The prepare-time precedence should stay simple:

- explicit `./prepare` CLI override
- configured track default

After preparation, every later stage and rerun should use the stored resolved
batch policy rather than re-reading current track defaults.

Avoid adding a wider global override layer unless this narrower config becomes
operationally painful.

In `comma_span` mode, each span work item should keep parent metadata:

- span ID
- parent unit ID
- span sequence number
- span text and rendered yomi
- offsets or rendered-pair ranges when practical
- optional previous/next span and full parent sentence context

Sentence-level aggregation from span labels is:

- `Skip` if any span is `Skip`
- `Review` if no span is `Skip` and at least one span is `Review`
- `OK` only if all spans are `OK`

`Skip` wins over everything else. If any span is `Skip`, the whole parent
sentence is excluded from the normal yomi corpus pipeline. No other span in that
sentence proceeds to repair, even if that span was labeled `Review`; all span
labels are kept only as audit metadata.

If there is no `Skip`, `Review` remains span-local in `comma_span` mode. Later
repair should target only the reviewed span, while attaching broader context
when needed. The context is not limited to the parent sentence; it can include
neighboring spans, previous/next sentence, or source metadata. The final
dataset, however, is always sentence-level, so repaired spans must be merged
back into their parent sentence before export.

The default policy should be:

- one prompt per judgment task
- one parser per judgment task
- one eval set and failure bank per judgment task

The main reason is not only implementation simplicity. It is error isolation.
If one prompt handles exactly one judgment, prompt iteration, regression
analysis, and human review alignment all become much easier.

If the cost later proves too high, the prompts can be merged or restructured,
but only after task-level evals show that the merged version preserves accuracy
and parsing stability.

Prompt optimization should happen as a concentrated pre-production phase, not
inside ordinary batch progression. The project should first build a sufficiently
large gold set for each LLM task, run many prompt candidates against that fixed
set, compare quality and token cost, and freeze the winning prompt before using
it over large corpus batches. Iterating prompts while processing the corpus
would make early batches less reliable and would make improvement cost scale
with corpus size.

Exploratory prompt optimization should use synchronous Responses API calls, not
the Batch API. The point at this stage is fast inspection and revision: run a
candidate, inspect failures and usage, edit the prompt, and rerun. Batch mode is
for production throughput and later regression-scale checks after a prompt
family is already promising.

Historical yomi-triage evals used four conceptual labels: `OK`, `Fix`,
`Ambiguous`, and `Skip`. `Fix` meant the current yomi was wrong but the correct
reading could be determined from the unit/context and repaired. `Ambiguous`
meant the unit was target Japanese, but the correct reading could not be safely
determined from the available context. Those labels remain useful for analyzing
old prompt runs and for future repair-stage evals, but they are not the current
main-path LLM interface.

Experiment note, 2026-05-18: a small 60-row comparison tested direct
`OK/Fix/Ambiguous/Skip` triage against a two-stage `OK/Review/Skip` first pass
plus a downstream `Fix/Ambiguous/OK/Skip` router. With `gpt-5.4-mini`,
end-to-end `3-way -> router` reached 47/60, while direct 4-way reached 36/60;
neither route recovered any of the 6 conceptual `Ambiguous` rows. With
`gpt-5.5`, `3-way -> router` reached 49/60 and direct 4-way reached 45/60. The
`gpt-5.5` router could identify ambiguity when run on gold Review rows
(15/19 overall), but the first 3-way pass still sent all 6 gold `Ambiguous`
rows to `OK`, so they never reached the router in the true end-to-end route.
Conclusion for now: do not depend on triage-time `Ambiguous` detection. The
active main path uses independent LLM reading generation instead of direct yomi
correctness or scope triage.

Each example should store the original sentence, the exact mechanical yomi
annotation that the model will see, the expected conceptual label, and optional
human notes that are not included in the production prompt. The eval set should
include clear mechanical errors, genuinely ambiguous cases that must remain
reviewable, acceptable variant readings that should not trigger unnecessary
repair, and non-target examples. Optimization priorities are: avoid dangerous
label errors, preserve parse stability, improve accuracy, then shorten prompt
tokens.

The prompt search should explicitly include few-shot variants. A no-example
prompt is worth testing, but the expected winner is likely to be a short prompt
with a small number of boundary examples. Useful prompt families include:

- zero-shot label definitions
- terse one-example-per-label prompts
- boundary-example prompts for `辛い`, `私/ワタクシ`, embedded old-character
  titles, and full non-target quotations
- slightly longer examples with short reasons, only if terse examples fail
- compressed or ungrammatical prompts, if they preserve label behavior

During exploration, `gpt-5.4-mini` is acceptable as the default search model.
For mini, sweep reasoning effort as an experimental parameter, not a fixed
assumption. Compare at least `low` and `medium`, and include higher settings if
the API/model supports them and early results suggest accuracy is effort-bound.
Every run should record model, reasoning effort, prompt path, input/output token
counts, cached-token counts if reported, estimated cost, parse errors, confusion
matrix, and dangerous errors. The final production prompt should still be chosen
from `gpt-5.6-sol` behavior after the mini search narrows the candidate set.

Runtime model selection should use named LLM profiles rather than raw model
names spread across pipeline branches. The batch should store `llm_policy`, and
the runner should resolve each task's profile into the actual task config
overrides used for model, reasoning effort, and any expensive tool settings.

Initial profile meanings:

- `smoke`: transport and instrumentation checks only, usually `gpt-5.4-nano`
- `economy`: cheaper flow validation and prompt/pipeline debugging, usually
  `gpt-5.4-mini`
- `standard`: normal corpus-quality judgment/repair, usually `gpt-5.6-sol`
- `strong`: exceptional last-resort repair/check settings, usually
  `gpt-5.5-pro` or web-search-enabled tasks

Track defaults should choose profiles per LLM task. Per-batch overrides should
allow realistic dev dry runs with `standard`, cheap plumbing checks with
`smoke`, and explicit expensive rescue runs without changing the track itself.
Artifacts should record both the named profile and the resolved model settings
for auditability.

The track/task-to-profile default should come from the same project defaults
config as the other policy defaults. Profile definitions live in the LLM profile
config, but the
prepared batch should remain the reproducibility boundary.

Prompt-cache tuning should be deliberate. OpenAI prompt caching starts at 1024
input tokens, so the reusable static prefix should eventually be tuned to be
just over that threshold before the variable `{text}` and `{rendered}` fields.
Do not guess this boundary from character count. Use local `tiktoken` counts for
quick iteration, then use the Responses `input_tokens` endpoint for exact
GPT-5-family input counts when the prompt is close to the threshold. Token-count
API calls should be treated as operationally negligible compared with real eval
runs, so exact counting is acceptable when it helps stabilize cache behavior.

The target is not to add filler. If extra examples are useful for accuracy, put
them in the static prefix and use them to cross the cache threshold. If an
equally accurate prompt is far below 1024 tokens, keep it short; otherwise, when
the prompt is already near 1k tokens, prefer moving it to a cache-friendly
static prefix around 1050-1150 exact API-counted tokens.

Likely current split:

- `yomi_reading`: per-target reading prompt that returns one JSON object for
  the marked kanji/Latin target
- `yomi_repair`: separate prompt because repair should not be mixed into
  ordinary judgment prompts

## 9.1 Inputs to the LLM

For LLM reading generation, the LLM should receive plain source text with
exactly one marked target. Surrounding tokens should not normally include
furigana annotations; they are context only. A no-space furigana rendering may
be stored as debug metadata for comparison, but it is not the default prompt
input for `yomi_reading`. The LLM should output the target reading, not a
sentence-level correctness label and not a full rewritten sentence.

For alphabetic material, the LLM should instead receive unresolved entity types
plus example sentences from the batch.

For yomi-facing prompts, the stored full yomi annotation remains the source of
truth, but the prompt may use derived display forms to reduce visual noise. The
preferred candidate for human/LLM-facing judgment is no-space furigana-style
text: readings are rendered inline and token spaces are removed. For example:

```text
こんな感じ（かんじ）でラミントンもいろいろなタイプが販売（はんばい）されていて、お値段（ねだん）も＄2.50～＄3くらいでお手頃（てごろ）価格（かかく）です。
```

This is only an LLM/human-facing representation. The stored artifact should
still keep the full rendered form, such as `こんな/コンナ` and `で/デ`, so that
auditing, deterministic reconstruction, and N-gram feedback remain lossless.

The no-space display is intentionally more natural than a token-spaced view. It
should reduce model complaints about segmentation artifacts such as `ラミン
トン` when segmentation repair is out of scope. For N-gram decoder training,
debugging, and alignment inspection, keep token-spaced views available:

```text
full:            送っ/オクッ て/テ
compact:         送っ/オクッ て
furigana spaced: 送（おく）っ て
furigana prompt: 送（おく）って
```

Because raw corpus text may already contain both `（...）` and `(...)`, prompt
display must reserve full-width parentheses for yomi annotations. Escape source
parentheses before adding yomi annotations:

- source `（` -> `-LRB-`
- source `）` -> `-RRB-`
- source `(` -> `-lrb-`
- source `)` -> `-rrb-`

Example:

```text
raw:             荷物を送って、（明日）届く。
furigana prompt: 荷物（にもつ）を送（おく）って、-LRB-明日-RRB-届（とど）く。
```

This is a reversible display-layer transform only. Stored source text, full
token yomi, debug output, and N-gram feedback should keep the original
parentheses unless a specific exported view says otherwise.

No-space furigana display should mark rare fused numeric yomi tokens with a
leading `|`. For example, canonical `1人/ヒトリ` displays as
`|1人（ひとり）`, while ordinary separated tokens `1/ 人/ニン` display as
`1人（にん）`. The `|` marker is not source text; it exists only to prevent the
LLM from confusing a digit that belongs to a yomi-bearing token with a normal
digit token handled by the future number-reading module.

### 9.1.1 Lexicalized numeric compounds

The default numeric policy still treats an arbitrary digit run as opaque and
stores it with no reading. Japanese nevertheless has a small set of common
digit-plus-suffix forms whose pronunciation cannot be recovered by simply
reading the number and suffix independently. Handle these with an explicit,
table-driven language-specific normalization step after hybrid generation.
This is a deliberate exception, not a general number-reading module.

Before applying lexicalized compounds, merge a syntactically complete Arabic
numeric expression into one no-reading token. Internal thousands separators,
decimal points, and an optional leading sign are part of that token, so
`2,035.28/`, `-2.4/`, and `＋１，０００．５/` are canonical. Require valid
three-digit comma grouping and digits on both sides of a decimal point. Keep
currency marks, percent signs, and measurement units as adjacent separate
tokens; for example, `-2.4/ kg/キロ`.

For a numeric value followed by `kg`, `km`, or `mm`, keep the number as a
separate no-reading token and use the ordinary shortened Japanese reading as
the mechanical default: `5/ kg/キロ`, `5/ km/キロ`, and `5/ mm/ミリ`. Retain
`キログラム`, `キロメートル`, and `ミリメートル` as review candidates for
formal or technical contexts. Apply this as a narrow table-driven rule with
NFKC/case normalization, not as a general policy for abbreviating
measurement-unit readings.

The deterministic table covers both ASCII and full-width digits:

- dates: `2日` through `10日`, plus `14日`, `20日`, and `24日`
- people: `1人` and `2人`
- the native counter: `1つ` through `9つ`

Store these as fused tokens with their lexicalized reading, for example
`2日/フツカ`, `24日/ニジュウヨッカ`, `2人/フタリ`, and `9つ/ココノツ`.
They are deterministic pipeline decisions and do not create independent LLM
reading targets. They are nevertheless exposed as safe, editable units in Bulk
Review. The deterministic reading is the default, but the reviewer can cancel
the ruby for the complete compound (for example, `6日`) and send that local
span through escalated repair. Keep the rule inventory centralized so its
behavior and future additions remain auditable.

Sudachi may combine the date suffix with `間`, as in `3/ 日間/カカン`. Normalize
that shape to `3日/ミッカ 間/カン`; the lexicalized date rule still applies, but
`間` remains an ordinary separate token.

`1日` is the one contextual exception. Keep it fused during mechanical
processing, using `1日/イチニチ` as the fallback while preserving an upstream
supported `1日/ツイタチ` result. Expose the whole `1日` span, rather than only
`日`, to safety checks, LLM reading inference when unresolved, and Bulk Review.
Its review candidates must include both `いちにち` and `ついたち`. At finalization,
convert an accepted `いちにち` choice to canonical tokens `1/ 日/ニチ`; keep an
accepted `ついたち` choice fused as `1日/ツイタチ`. This late expansion avoids
forcing the review UI to reconcile two tokenizations while preserving the
desired final representation for the compositional `いちにち` reading.

After applying this explicit irregular-compound table, split a simple mixed
Arabic/full-width-digit Sudachi token at its numeric boundary. This handles
ordinary compositional forms such as `2級/ニキュウ`, `3階/サンガイ`, `小5/ショウゴ`,
and `中2/チュウニ`, producing `2/ 級/キュウ`, `3/ 階/ガイ`, `小/ショウ 5/`, and
`中/チュウ 2/`. Derive the lexical component's reading by subtracting the
independently obtained digit reading from the combined Sudachi reading. If that
does not work, use an isolated Sudachi lookup of the lexical component as a
fallback. This order preserves contextual readings such as `中/チュウ` and
`階/ガイ` that an isolated lookup may not return.

Keep this split deliberately conservative. It applies only when the surface has
exactly one digit run and one non-digit component, and only when the remaining
lexical reading is valid katakana. Preserve the original token when derivation
fails, as with lexicalized alphanumeric forms such as `2nd/セカンド`. Explicit
irregular compounds such as `1人/ヒトリ`, `2日/フツカ`, and `1つ/ヒトツ` always
take precedence over this generic boundary split.

Furigana rendering should use the Sudachi-derived annotated-form dictionary
when possible. The dictionary can map `(surface, reading)` pairs such as
`送っ/オクッ` to `送（おく）っ` and `読み仮名/ヨミガナ` to
`読（よ）み仮名（がな）`. If a pair has no unique mapping, the renderer should
fall back conservatively rather than inventing a fragile annotation.

Treat this as a display/projection layer, not as the corpus format. The
canonical accepted corpus remains a token sequence of `surface/reading` pairs.
Furigana text such as `振（ふ）り仮名（がな）` is a review/UI projection derived
from those pairs.

When a document or unit is finally accepted, any non-dictionary or inferred
furigana projection that the reviewer saw and accepted should be persisted as
validated projection metadata. Store at least the `surface`, normalized
`reading`, accepted annotated form, converter method/confidence, source
(`human_accepted_review_ui` or equivalent), dictionary version, and the
batch/unit IDs where it was accepted. Future UI rendering may prefer these
accepted projections before falling back to dictionary/scored alignment, and a
later promotion step may fold repeated accepted projections into the
annotated-form dictionary. This cache is audit/training evidence; it does not
replace the canonical `surface/reading` corpus representation.

LLM-proposed fixes may include neighboring kana or symbol tokens when the
needed correction crosses token boundaries. For example, a proposal may replace
`外出/ガイシュツ て` with `外/ソト 出/デ て`. The application layer should not
blindly string-replace the displayed prompt text. It should align the proposed
`from` span back to the original full token sequence. Apply automatically only
when the match is unique and structurally valid; otherwise keep the item for
human review.

Implementation plan for yomi display modes:

- Add shared rendering utilities for yomi display modes. Do not duplicate this
  logic inside individual prompts or pipeline stages.
- Preserve the existing full `rendered` string in all stored unit artifacts.
  Add derived text only at prompt-build time, or store it as explicit debug
  metadata if caching/debugging requires it.
- Support at least `full`, `compact`, and `furigana_no_space`; a spaced
  furigana debug mode is also useful.
- Allow yomi prompt experiments to omit the separate source `Text:` line. In
  no-space furigana mode, the displayed yomi already contains the source surface
  plus readings, so a yomi-only prompt can test whether the extra raw-text line
  is redundant or distracting.
- Use plain marked source text first for per-target LLM reading generation
  (`yomi_reading`). A mini-model comparison on a 150-item stratified sample did
  not show an accuracy drop relative to no-space furigana context, while input
  tokens fell by roughly 25%.
- Keep the `yomi_reading` prompt extremely short unless later evidence shows a
  regression. A GPT-5.5 test on the same 150-item sample with
  `目が**痛**い。->{"痛":"いた"}\n{marked_text}->` produced no parse errors and
  fewer input tokens than the earlier explanatory prompt. The main failure mode
  was not prompt format, but target extraction/expectation boundaries such as
  `日々`: the queued target must be the whole readable unit (`日々/ひび`), not
  only the first kanji (`日/ひび`).
- Use no-space furigana first for LLM proposal tasks where the model benefits
  from seeing existing yomi annotations, such as `yomi_review_resolution` and
  possibly later `yomi_check`.
- Keep full and spaced views available for full-sentence repair prompts until
  there is a deterministic expansion/alignment layer for applying model output.
- Add a task-level config switch so prompt experiments can compare full,
  compact, and furigana display without changing code.
- Update every prompt that receives a derived yomi display to state the display
  policy. For no-space furigana, the prompt should say that token spaces are
  intentionally omitted and segmentation artifacts are not the target unless
  explicitly requested.
- For local-fix proposal prompts, tell the model to copy the displayed local
  span in `fixes.from` and use the desired full local yomi annotation in
  `fixes.to`. The downstream application layer handles alignment to the full
  stored token sequence.

Prompt merging should be treated as a later optimization question, not the
starting architecture. Only tasks with the same unit, same context needs, same
model policy, and similar failure modes should even be considered as merge
candidates.

## 9.2 Yomi repair

If the LLM does not judge the current yomi to be certainly correct, that unit
should be sent to a second prompt that actually repairs the yomi.

So the yomi path becomes:

1. mechanical yomi
2. mechanical auto-accept for low-risk units
3. LLM triage for the remaining units: `OK`, `Review`, or `Skip`
4. optional review routing for `Review` units: `Fix`, `Ambiguous`, operational
   `OK` for first-stage false positives, or `Skip`
5. LLM repair path for `Fix`
6. bulk audit for `OK` items and focused review for unresolved items

The second routing step is deliberately downstream of the first triage step.
It lets the first prompt stay compact while collecting evidence about whether a
future single wider label set would be safe. In router output, operational
`OK` means "the first triage over-called Review"; in the conceptual gold data,
that item is simply `OK`.

Regex-based repair rules may still be useful here, and this is the area where
regex currently seems more justified than for whitelist/blacklist classification.


## 10. Rule Generation from LLM or Human Decisions

The project also wants a second-order learning loop: use hard decisions to
expand deterministic coverage over time.

## 10.1 Trigger point

Current idea:

- do not generate new rules immediately from ordinary sentence-level LLM
  judgments
- instead, generate candidate rules after human review has confirmed the
  judgment

That should reduce noise.

## 10.2 Scope decisions

Do not harvest machine non-target or alphabetic-scope rules. Scope is an
explicit human disposition in Bulk Review: implicit `Keep`, recoverable `Skip`,
or terminal `Exclude`. Reading-rule harvesting remains independent of those
choices.

## 10.4 Yomi repair rules

For yomi correction, regex-like repair rules still seem reasonable because many
useful fixes may be boundary or formatting corrections rather than semantic
reinterpretations.

This area needs experimentation.

Future task: learn exact surface-span defaults from confirmed Escalated Repairs.
When an Escalated Repair changes a local span and the final human confirmation
accepts it, record the whole repaired surface span as a default for future
batches. Examples:

- `一発 -> いっぱつ`
- `池尻中学校 -> いけじり ちゅうがっこう`

These learned defaults should apply to the exact surface span, even if the
original analyzer split it differently. This is intentionally narrower than
general regex promotion: `池尻中学校` should not automatically imply a global
`中学校 -> ちゅうがっこう` rule unless that broader rule is separately approved.
The rule should be auditable and should record the source batch, repaired
rendering, rejected readings, and whether web search was used.

## 10.5 Review transport and UI state

Because the working environment is a Linux cluster accessed over SSH, the human
review UI should not assume that it can write directly back to the cluster.

Current preferred review transport:

- treat this repository's GitHub Pages review UI as the `dev` review surface
  while the workflow and review-pack schemas are still changing
- keep the static dev review UI in this repository, not a separate UI
  repository
- isolate it in its own web-facing directory so the Python pipeline and static
  frontend remain loosely coupled
- host the static review UI on GitHub Pages
- use GitHub as the return mailbox
- for now, prefer one GitHub Issue per review pack
- submit one review result per Issue comment

This is meant to work from both desktop browsers and iPad browsers without
requiring a writable backend on the cluster.

Practical layout direction:

- keep review-UI source under `web/review/`
- keep Python pipeline code under `src/` and `scripts/`
- publish built static assets under `docs/` through this repo's GitHub Pages
  configuration

This keeps hosting simple while still letting the UI evolve together with the
pack format, submission format, and review workflow.

Long-term task: after the review flow has stabilized over multiple batches,
prepare a separate static review project for the `working` track. That project
should be stable and production-oriented, while this repository's Pages site can
remain a fast-changing dev UI. A split would also keep UI publishing commits and
review traffic out of the corpus/pipeline repository. Do not split early: this
repo should continue to own review-pack generation, submission ingestion,
replay semantics, and corpus state. A split is only attractive once the boundary
is a stable versioned contract:

- review-pack JSON schema
- review-submission JSON schema
- publishing/sync mechanism from pipeline artifacts to the UI project
- GitHub Issue/comment ingestion convention back into this repository

Until then, the monorepo layout is intentionally pragmatic.

Review transport defaults should still be configured as if the split may happen
later. `config/review_transport/default.toml` defines per-track transport
values:

```toml
[tracks.dev]
repo = "hiroshi-manabe/yomi-corpus"
pages_url = "https://hiroshi-manabe.github.io/yomi-corpus/review/"
publish_mode = "local"
```

`repo` is the GitHub Issue mailbox used by `./review-sync`. `pages_url` records
where the static UI is expected to live. `publish_mode` controls the default
artifact behavior for sync: `none`, `local`, or `gh-pages`. Moving `dev` or
`working` review to a separate repository should primarily be a config change
plus a publish adapter update, not a rewrite of review import or replay logic.

Near-term dev trial: the next dev batch should use a small batch size, around
10 documents, to test browser-selected work slices and partial returned
submissions. Treat that run as a workflow/UI implementation trial rather than a
quality benchmark. It should verify that local draft state, range/subset export,
Issue submission import, and later-wins replay work before applying the same
pattern to larger batches.

For final yomi review, use a sentence-level review pack in one continuous list,
but make the normal view look like ruby-rendered text rather than pipeline
metadata. Avoid making each document a separate page unless later batch sizes
require it. Multiple returned tasks are merged by stable item IDs rather than
by a browser-selected range.

Each sentence has compact, mutually exclusive `Skip` and `Exclude` icon
buttons. Neither active means `Keep`. The previous range-mark control is
removed because it has not been useful in actual operation. Rare actions may
remain in a separate overflow menu only when a concrete need appears.

Yomi targets should be edited inline. Unresolved targets are highlighted;
safe targets are visually quiet but may still be tappable if candidate readings
exist. Tapping a target cycles through known candidates and then no-ruby, for
example `きんきん -> ちかぢか -> none`. Candidate readings should come from the
recorded evidence: current mechanical/hybrid reading, LLM reading,
corpus-frequency dominant reading, and stable dictionary reading.

Changed spans should be colored differently from unresolved spans. A changed
span is a local override. A no-ruby choice means the reviewer rejects the
current reading and wants Escalated Repair for that local area. Consecutive
no-ruby targets in the same sentence should be grouped automatically into one
Escalated Repair span. Whether to use web search should be decided by the
Escalated Repair prompt/model from the target context, not by a human review
checkbox.

Whole-sentence escalation should not be a primary control. So far, real cases
look like local boundary/reading failures that can be handled by canceling a
2-3-token area. If a future example truly needs whole-sentence repair, add it as
an advanced/fallback path rather than the default review action.

Escalated Repair spans go through a separate confirmation UI published as the
`yomi_strong_repair_review` stage. The current first pass exposes the source
text, rejected readings, LLM proposal, and before/after yomi, with accept/reject
review decisions. Rejected repairs block finalization rather than being silently
included in the corpus. A later pass can add direct structured editing for
confirmed-but-imperfect repairs.

### 10.5.1 Review packs

The cluster should export immutable review-pack files.

Each review pack should include:

- `pack_id`
- `review_stage`
- ordered review items with stable `item_id`
- `seq` numbers for visual order
- proposed action for each item
- evidence summary and short example snippets

### 10.5.2 Local draft persistence

The browser UI should save draft state locally so that a reviewer can leave the
page and return later without losing progress.

Recommended approach:

- key local draft state by `review_stage` and `pack_id`
- store per-item overrides
- store optional range markers
- restore automatically on reload

This local draft state is device-local. It is not the authoritative shared
state.

### 10.5.3 Reviewed range semantics

For review export, the important concept is reviewed coverage, not
explicit clicks on every approved item.

Default UI behavior:

- all items are initially in the export range
- optional `from` and `to` markers can narrow that range
- if neither marker is set, export all items
- if only `from` is set, export that item and everything after it
- if only `to` is set, export everything before and including it
- if both are set, export only the inclusive interval

Visual behavior:

- items inside the current range should look normal
- items outside the current range should remain visible but faded
- `from` and `to` rows should have distinct marker styling
- overridden rows should be highlighted more strongly than simple in-range rows

### 10.5.4 Sparse overrides

Within the reviewed range, the default interpretation is:

- no explicit mark means the reviewer accepts the proposed action

So the submission should store:

- reviewed range
- sparse per-item overrides such as `reject` or `defer`

This is important because the reviewer may visually inspect many items and only
change a few of them.

### 10.5.5 Multiple submissions

One review pack may produce multiple submissions.

This supports:

- interrupted review sessions
- partial review by range
- accidental multi-device work if it ever happens

Merge rule:

- later submissions overwrite earlier submissions for overlapping items
- overlapping range handling is intentionally simple
- responsibility for accidental overwrite stays with the user

For reviewed-range semantics, a later overlapping submission should reset that
range to default acceptance first, then apply its sparse overrides.

That ensures that an omitted override in a later submission really means
"accept proposal" inside that later reviewed range.

## 10.6 Local pipeline state and orchestration

The pipeline should not depend on the operator remembering which script to run
next.

Current preferred direction:

- keep durable local state for each batch
- keep a current-batch pointer per track
- use `working` as the implicit default track and `dev` as an explicit second
  track
- `working` is the strict protected track; `dev` is the relaxed experimental
  track
- provide `./prepare`, `./next`, and `./status` commands
- let `./next` perform one implemented automatic step per call

This is meant to unify:

- ordinary local processing
- OpenAI Batch submission / polling / fetch
- human-review wait points

### 10.6.1 Per-batch and per-track state

Recommended shape:

- one local state file per batch under `data/pipeline/batches/`
- one track pointer file per track under `data/pipeline/tracks/`
- current stage
- known artifacts
- most recent blocking reason
- timestamp

The exact schema can stay minimal at first and grow with the implemented
stages.

### 10.6.2 Command surface

Current intended commands:

- `./prepare 100`
- `./prepare dev 10`
- `./prepare --yomi-unit-mode comma_span --yomi-auto-accept-profile off --llm-profile yomi_reading=smoke dev 10`
- `./next`
- `./next dev`
- `./next --force-stage yomi_generated`
- `./next dev --llm-mode sync`
- `./next dev --llm-mode background`
- `./next dev --llm-mode batch`
- `./next --status`
- `./next dev --status`
- `./next dev --status --stages`
- `./status`
- `./status dev`
- `./status dev --stages`
- `./set-stage dev yomi_auto_accepted`
- `./set-stage working yomi_auto_accepted --yes`

The implicit no-argument track should be `working`.

`./next --status` is the canonical read-only inspection mode. It uses the same
operator-facing formatting as `./next`, but does not advance the pipeline.
`./next --status --stages` is the terse form that prints only the completed
current stage and the next stage. `./status` is a compatibility wrapper:
without `--stages` it calls `./next --status --json`, and with `--stages` it
calls `./next --status --stages`.

Stages that call the LLM should include `llm` in the stage name. Current
LLM-calling stages are `yomi_reading_llm_completed` and
`yomi_strong_repair_llm_completed`.

Queue-building stages such as `yomi_reading_queued` prepare LLM inputs but do
not call the API, so they should not be treated as
LLM-calling stages. `./next --llm-mode <mode>` is a per-invocation override for
the stage being run. It should be accepted only when that stage calls the LLM;
on deterministic stages it should fail explicitly rather than being silently
ignored. The saved track policy remains unchanged.

`./set-stage <track> <stage>` is an explicit pointer-only rewind tool. It should
change only the saved `current_stage` for the current batch and must not delete
or rewrite artifacts. It refuses forward moves; use `./next` to advance. On the
protected `working` track it requires interactive confirmation or `--yes`.

### 10.6.3 Current one-step progression

`./next` should run one legal automatic step and then stop.

Example behavior:

- if a batch is only prepared, `./next` should build the mechanical yomi JSONL
- the next `./next` should add the yomi auto-accept artifact
- the next `./next` should build a yomi-reading queue from unresolved targets
- the next `./next` should run or resume the configured yomi-reading LLM task
  and write comparison metadata
- after that, later repair/review stages should consume targets or units with
  LLM/mechanical disagreement, parse failure, or unresolved ambiguity;
  agreement cases enter bulk audit or later auto-acceptance experiments
- `./next --force-stage <stage>` should rerun the current completed stage
- on `working`, confirmation should happen only when that rerun would actually
  overwrite existing artifacts

The intended UX is:

- run one command
- let it do one clear thing
- inspect `./status` when needed

### 10.6.4 Resumable LLM Jobs

LLM calls should be orchestrated through a generic resumable job layer. This is
needed because the project uses LLMs for yomi reading generation, ordinary yomi
repair, and rescue repair. Each stage should not invent its own
sync/background/batch lifecycle.

The pipeline stage should own the domain transition, while the LLM job owns
operational execution:

- input rows and request JSONL
- result JSONL
- mode: `sync`, `background`, or `batch`
- task name and resolved LLM profile/model settings
- total item count, completed item count, failed item count
- remote response IDs and remote status for background mode
- remote batch IDs and remote status for batch mode
- timestamps, attempts, and error information

Sync mode behavior:

- process items sequentially at first; small concurrency can be added later
- append each completed result to the job result JSONL immediately
- on resume, skip item IDs already present in the result JSONL
- show progress from completed item count over total item count
- allow the operator to interrupt safely and continue with the same `./next`

Background mode behavior:

- submit one Responses API background request per item, preferably submitting
  all missing items before entering the polling loop
- store each `item_id -> response_id` mapping in the LLM job directory
- on resume, do not resubmit item IDs that already have either a response ID or
  a parsed result
- poll stored response IDs until completion or interruption by default, and
  append completed results immediately
- report progress from parsed completed result count over total item count
- treat incomplete background responses as an active resumable job, not as a
  separate domain pipeline stage
- do not postpone polling indefinitely; OpenAI documents background response
  storage as roughly a 10-minute polling window

Batch mode behavior:

- submit remaining items as one or more OpenAI Batch jobs
- store remote job IDs, request files, and manifest in the LLM job directory
- on resume, poll the stored remote job
- if the remote job is still running, report status and OpenAI
  `request_counts` (`completed`, `failed`, `total`) and do not advance the
  domain stage
- if complete, download and normalize output, then apply results
- if partially failed, resubmit only missing or failed item IDs when possible

The batch output file is available only after completion. Progress before that
comes from the Batch object's `request_counts`, while final result mapping must
come from the downloaded output file and each request's `custom_id`.

Batch API operational limits that should shape implementation:

- one OpenAI batch can include up to 50,000 requests
- one batch input file can be up to 200 MB
- pending batch prompt tokens count against a per-model enqueued-token limit
- batch creation is rate-limited, documented as up to 2,000 batches per hour
- a batch can expire; completed request outputs remain available, and
  unfinished requests are returned as errors

Current implementation: batch mode presents one logical resumable LLM job to the
domain pipeline, while request-count chunking may split it into multiple remote
OpenAI batch jobs.

The task-config knob for request-count chunking is
`batch_max_requests_per_batch`. The production default stays high enough
to preserve single-batch behavior for ordinary jobs, while tests can set a
small value to force multiple remote OpenAI batches.

Suggested storage:

```text
data/llm/jobs/<job_id>/
  manifest.json
  input.jsonl
  requests.jsonl
  results.jsonl
  usage_summary.json
```

Each normalized result row stores authoritative built-in tool-call counts from
the Responses API `output` array, for example `{"web_search_call": 2}`. Merely
enabling a tool does not count as a call. Usage summaries aggregate these counts
and report `estimated_token_cost_usd`, `estimated_tool_cost_usd`, and their sum
as `estimated_total_cost_usd`. Older result rows without `tool_calls` remain
valid and contribute zero tool calls until explicitly backfilled from retained
response IDs.

Suggested manifest fields:

```json
{
  "job_id": "dev_batch_0003_yomi_reading",
  "track_name": "dev",
  "batch_name": "dev_batch_0003",
  "task_name": "yomi_reading",
  "mode": "sync",
  "status": "running",
  "total_items": 559,
  "completed_items": 184,
  "failed_items": 0,
  "profile": "economy",
  "execution_mode": "background",
  "remote_response_ids_path": "responses.jsonl",
  "remote_batch_id": "batch_...",
  "remote_status": "in_progress",
  "model": "gpt-5.4-mini"
}
```

Domain pipeline stages should not need many operational substages such as
`queued`, `submitted`, or `batch_completed`. The normal pattern should be:

- if no LLM job exists for the domain stage, create it and start execution
- if an incomplete job exists, resume or poll it
- if the job is complete, apply results and advance the domain stage

Human review remains a first-class wait state, but OpenAI sync/background/batch
execution should usually be represented as an attached resumable LLM job rather
than as many stage names.

### 10.6.5 CLI Output and Logs

`PipelineWorkspace.advance()` may continue returning a full structured summary
for tests and internal callers. The `./next` command should not print that full
JSON by default. It is too long for ordinary operation and hides the important
state.

Default `./next` output should be concise and human-readable:

```text
Track: dev
Batch: dev_batch_0003
Stage: yomi_reading
LLM job: dev_batch_0003_yomi_reading
Progress: 184/559 completed
Status: running
Next: rerun ./next dev to continue
```

On completion:

```text
Track: dev
Batch: dev_batch_0003
Stage: yomi_reading_llm_completed
Completed: 559/559
Output: data/units/dev_batch_0003/units.yomi.llm_readings.jsonl
Next: yomi_repair
```

Full structured summaries should be written to local logs, for example:

```text
data/pipeline/logs/YYYYMMDD/<timestamp>_<track>_<batch>_<stage>.json
```

`./next --json` should preserve the current machine-readable behavior by
printing the full structured summary to stdout. `./status --json` should also be
available if status becomes human-readable by default later.


## 11. Human Review: Pass 1

The first human review UI should be sentence-based. It can use the same simple
checkbox interaction for both `OK` and non-`OK` queues, but the queue context
changes how the reviewer approaches the work.

Recommended queues:

- `bulk_ok_audit`: automatic `OK` items; scan many sentences quickly and mark
  only sentences with a problem
- `focused_review`: items that still need attention after repair; inspect each
  sentence carefully

The core checkbox can simply mean "problem found" or "needs attention":

- in `bulk_ok_audit`, unchecked means accepted by default after scanning
- in `focused_review`, unchecked means accepted after focused inspection
- checked means keep unresolved or send to correction

This lets LLM `OK` reduce human workload without pretending that LLM `OK` is
infallible. The review pack should still record the queue type and the source
of each automatic `OK`, so later analysis can estimate false-OK rates by
source.

### 11.1 Migration target: document-level review queues

Human-facing review labels should be:

- `Bulk Review` for the normal high-throughput human yomi pass. The internal
  stage and pack IDs may still use `yomi_final_review`.
- `Escalated Repair` for focused repair confirmation after a canceled or
  rejected local yomi span. The internal stage and pack IDs may still use
  `yomi_strong_repair_review`.

The distinction matters because `final review` is an internal pipeline idea,
not a useful phrase for the reviewer. The reviewer is either doing bulk audit
work or focused escalated repair work.

Unified Yomi Review is the sole normal human-facing review entrypoint. Legacy
Bulk Review and Escalated Repair packs may continue to exist as internal
payloads and history while cleanup is incremental, but the public review page
should not expose Classic mode or direct stage selection when Unified sources
are available.

The next dev implementation should prototype the intended `working` workflow.
The main shift is from a single global batch stage to durable per-document task
state. The batch remains the data boundary, but different documents in the same
batch may be at different review phases:

- a document may still be waiting for Bulk Review
- another document may have Bulk Review applied and be waiting for Escalated
  Repair
- another document may have both Bulk Review and Escalated Repair complete
- another document may be skipped

This should be implemented in `dev` first, then copied to `working` only after
the workflow is stable.

Target: keep pipeline batches large, for example 100 documents, but make human
review operate on smaller browser-selected slices. The pipeline batch is
durable state; the UI-selected range or checkbox subset is only a work unit for
one submission.

The target queue lifecycle is:

1. Prepare one large batch.
2. Put documents needing human Bulk Review into a `final_review_pending` list.
3. Let the UI show that list, let the reviewer choose a range or checked
   subset, and persist that in-progress work in browser storage.
4. Starting a UI task creates or names one GitHub Issue for that selected work
   unit; submitting review JSON appends to that Issue.
5. A periodic importer polls Issues/comments, applies matching submissions
   idempotently, updates local document queue state, and closes successfully
   applied Issues as `completed`.
6. Documents with completed Bulk Review are greyed out or removed from the
   Bulk Review list.
7. Documents with canceled/local unresolved readings move into
   `strong_repair_pending`.
8. Escalated Repair review works the same way; completed documents disappear
   from the second list.
9. Documents with no Escalated Repair need can move directly from Bulk Review to
   `complete`.
10. When every document in the batch is `complete` or `skipped`, the
    orchestrator finalizes the batch and prepares the next large batch.

Operational requirements:

- store per-document state, not only one batch-level stage. A minimal state
  vocabulary should include `final_pending`, `final_in_review`,
  `final_reviewed`, `strong_pending`, `strong_in_review`, `strong_reviewed`,
  `complete`, and `skipped`
- define the states as follows:
  - `final_pending`: actionable Bulk Review has not been submitted
  - `final_in_review`: part of the document has Bulk Review applied, but more
    units remain
  - `final_reviewed`: Bulk Review is submitted/applied; backend processing has
    not yet created an actionable Escalated Repair task or marked the document
    complete
  - `strong_pending`: Escalated Repair is needed, but it is actionable only
    after repair proposals/results exist
  - `strong_in_review`: part of Escalated Repair has been reviewed, but more
    repair items remain
  - `strong_reviewed`: Escalated Repair review is submitted/applied and awaits
    final backend resolution
  - `complete`: accepted for corpus output
  - `skipped`: intentionally excluded from corpus output
- treat that per-document state as the only source of active queue membership.
  Bulk Review is a view over `final_*` states, and Escalated Repair is a view
  over `strong_*` states. A document may appear in multiple historical packs,
  but it must be an active member of at most one queue at a time.
- review packs should carry both the canonical document state and explicit
  queue-view metadata such as `queue_member` and `selectable`. The UI should
  use `queue_member` to decide whether a document belongs in a queue panel; the
  Pack Map may still show all documents from the batch.
- Escalated Repair review packs should expose only rows with completed repair
  results. A document can be internally marked as needing repair before the
  repair LLM has run, but it should not become an actionable Escalated Repair
  review task until at least one proposal/result is available.
- in the human-facing dashboard, a document that has left Bulk Review but has
  not yet become an actionable Escalated Repair task should be shown as
  submitted in the Bulk Review bucket, not as `Resolved` or as an active repair
  task. Submitted is an overlay/status, not a fourth bucket. Backend states
  such as `final_reviewed` or non-actionable `strong_pending` remain internal
  processing states until sync/import/LLM repair creates the next actionable
  queue or completes the document.
- submission payloads must carry stable document IDs, item IDs, queue/stage IDs,
  pack IDs, selected range/subset metadata, and source Issue/comment IDs
- browser draft state should be keyed by `pack_id`, `queue_id`, and selected
  document IDs or range
- completed/greyed-out state should be rendered from imported local queue
  state, not from browser-only local storage
- importer replay must be idempotent and tolerate many open Issues/comments
- successfully applied Issues should be closed as completed; invalid or
  mismatched Issues should remain open with a clear reason or be ignored until
  a later sync
- overlapping submissions should continue to use the existing later-wins replay
  rule
- Escalated Repair should be derived from Bulk Review submissions, not from a
  separately prepared manual batch
- periodic sync should be a distinct operator command, not a hidden side effect
  of ordinary review UI generation. `./review-sync dev` imports Issues,
  updates document states, and regenerates review packs when needed. Starting
  the next batch automatically remains a later extension.

Rolling refill target:

- the pipeline should eventually keep Bulk Review stocked with a configurable
  number of actionable documents rather than waiting for one backend batch to
  finish completely
- the refill policy should be document-state based: count selectable Bulk Review
  documents, and if the count is below target, prepare more source documents up
  to a per-pass limit
- for refill accounting, derive coarse pool labels from the canonical document
  state:
  - `unprocessed`: source document has not entered the track ledger/preparation
    flow
  - `prepared`: preprocessing exists but the document is not yet actionable in
    Bulk Review
  - `bulk-ready`: actionable `final_pending` or `final_in_review`
  - `bulk-submitted`: `final_reviewed` or non-actionable `strong_pending` while
    backend work is deciding the next visible bucket
  - `escalated-ready`: actionable `strong_pending` with repair proposals, or
    `strong_in_review`
  - `escalated-submitted`: `strong_reviewed` while backend finalization is
    pending
  - `resolved`: `complete` or `skipped`
- these coarse labels are summaries for counting, map display, and source
  selection. They must not become a second mutable state vocabulary; the stored
  state remains the concrete `final_*`, `strong_*`, `complete`, or `skipped`
  value.
- the first implemented primitive is queue observability: per-batch document
  state summaries and `./review-sync` output report counts for selectable Bulk
  Review documents, submitted Bulk Review documents, selectable Escalated Repair
  documents, submitted Escalated Repair documents, and resolved documents
- `./review-sync <track> --bulk-review-target-ready-docs N --refill-pass-limit M`
  reports a refill plan. With `--dry-run`, the plan includes the exact source
  documents that would be prepared and still reports `will_prepare: false`.
  Without `--dry-run`, if `pool_counts["bulk-ready"]` is below `N`, the sync
  prepares up to `M` source documents and advances the new/current batch through
  automated stages until `final_review_prepared`.
- if a refill batch is already in progress but has not reached
  `final_review_prepared`, the next sync pass resumes that batch instead of
  selecting another source slice. This prevents duplicate batches when LLM
  stages take longer than one sync pass.
- prepared documents may come from multiple backend batches but should appear in
  one human-facing Bulk Review queue
- source selection must be idempotent. A source document already prepared,
  submitted, resolved, skipped, or partially prepared must not be selected as a
  fresh document again.
- refill may run automatic stages and LLM stages, but it must be bounded and
  resumable. If a sync is interrupted, the next sync should continue from the
  stored ledger state.

Durable document ledger:

- maintain a per-track ledger keyed by stable source document ID and source
  order
- assign a stable `track_doc_seq` when a source document first enters that
  track's ledger
- store current state, current queue membership, preparation batch/artifact
  pointers, imported Issue/comment IDs, and latest canonical output pointers
- use the ledger as the source of truth for queue membership; generated packs
  are views or payloads, not authoritative state
- use `track_doc_seq` as the human-facing document number in queues, Pack Map,
  Issues, and correction payloads. Batch-local `doc_seq` can remain inside
  artifacts but must not be the public identifier once documents from multiple
  backend batches are mixed in one workspace.
- review Issue task metadata uses `track_doc_seqs` and `track_doc_ranges`.
  Messages, saved-task summaries, and overlap warnings derive their labels from
  the same stable value and never fall back to batch-local `doc_seq`.
- Issue titles put the workflow first: `[Bulk Review] 861-870`,
  `[Escalated Repair] 861-870`, or `[Finalized Correction] 861`. Finalized
  corrections currently target one document, while queue tasks compact
  consecutive stable document numbers into ranges.
- never renumber existing ledger rows. If a batch is regenerated or resumed,
  reuse the existing `track_doc_seq` for matching `doc_id`.

Corpus Map target:

- add a read-mostly map view over resolved/finalized documents, separate from
  editable review tasks
- use Pack Map visual language: compact document-number tiles, resolved styling,
  and in-place document preview
- generate a compact `docs/review/archive/index.json` with finalized shard
  metadata
- generate sharded `docs/review/archive/<track>/docs_XXXXXX_YYYYYY.json` files
  with finalized text, yomi, ruby display data, and stable `track_doc_seq`
- hide shard mechanics from the ordinary UI as much as possible. The first
  shard can load by default, and later controls can page through ranges when
  the archive grows
- future/unprocessed documents are out of scope for now. If they are needed
  later, add them as a separate corpus-browsing layer rather than mixing them
  into active review queues
- correction of resolved documents should later use a normal auditable
  submission/replay path rather than direct browser-side mutation

### 11.1.1 Current-batch assumption audit

The old linear workflow treated `track.current_batch_name` as the single active
work item. That assumption is now a liability. Refill, background/batch LLM
jobs, submitted review tasks, and delayed finalization can leave multiple
batches actionable at the same time. In that world, `current_batch_name` should
be a source-refill convenience pointer, not the scheduler's authority.

Implemented scheduler behavior and remaining boundaries:

- `PipelineWorkspace.status()`, `advance()`, and `set_stage()` operate only on
  `track.current_batch_name`. This is acceptable for manual `./next` debugging
  but not sufficient for unattended automation.
- `prepare_next_batch()` still updates `track.current_batch_name` to the newest
  prepared batch. This is useful for legacy CLI behavior, but it can hide older
  batches that still need server-side work.
- `review-sync` advances the pointer-selected batch first, then sweeps every
  other batch whose next transition is safe for unattended execution. A blocked
  older batch does not prevent a newer batch from finalizing.
- `maintain_bulk_review_refill()` and
  `advance_current_batch_to_bulk_review_ready()` remain current-batch-oriented
  source-refill helpers. Refill demand itself is calculated from canonical
  document-state counts across every unfinished batch.
- `./next`, `./next --auto`, and `./set-stage` are intentionally current-batch
  commands. Keep them as operator/debug tools, but do not model unattended
  progress on them.
- Issue application is mostly pack-based and can apply non-current packs, but
  it is only reached when `review-sync` decides to touch the corresponding
  batch. The scheduler must therefore enumerate candidate batches.
- Decoder refresh already discovers all finalized batches via batch state files;
  it is less pointer-dependent than review-sync.
- Review dashboard generation publishes the newest actionable pack for every
  batch and stage. The unified UI can therefore contain documents from multiple
  batches, while submissions retain their source pack IDs.

Target model:

- derive scheduler candidates from `data/pipeline/batches/*.json`,
  `data/pipeline/document_states/*.json`, review submission stores, and LLM job
  state
- sweep all batches for server-side actionable transitions:
  final review submissions to apply, strong repair queue/results/review to
  apply, LLM jobs to poll, and batches ready to finalize
- keep `current_batch_name` only for legacy command defaults and source refill
  cursor behavior
- calculate refill demand from document queue counts across all active batches,
  not from the current batch alone
- after finalization, let decoder refresh look at finalized batches globally
  rather than relying on the batch that happened to be current

Operational rules:

1. Keep `./next` and `./set-stage` as current-batch manual/debug tools; they are
   not the unattended scheduler model.
2. Treat batch state as provenance/container state and canonical document state
   as the source for queue membership and refill accounting.
3. The dev sync configuration targets fifty Bulk Review documents and adds at
   most ten documents per refill batch. The target is document-based rather
   than an exact active-batch count, so partially completed batches may leave
   more than five batches visible. Working refill remains disabled until its
   production policy is chosen explicitly.
4. Batch completion order is independent of batch creation order. Finalization,
   archive publication, and decoder refresh must use concrete batch identities,
   never an assumption that the current pointer names the completing batch.

### 11.2 Review sync command

`./review-sync <track>` is the operator command for moving GitHub-Issue review
work back into the local pipeline. It should be an idempotent polling command,
not a long-running daemon at first.

Responsibilities:

- acquire a per-track lock so two syncs cannot overlap
- import matching open GitHub Issues/comments for Bulk Review and Escalated
  Repair
- run the relevant apply stages for the active batch
- close Issues only after every matching submission in that Issue was imported
  and the corresponding pipeline apply step succeeded
- leave invalid, unknown-pack, or not-yet-applicable Issues open
- regenerate and publish review artifacts when queue state changes
- write a machine-readable summary under `data/state/review_sync/`

When a pass imports a new Bulk Review or Escalated Repair submission, it
publishes the durable server-side state once before continuing into slower
pipeline work. This lets the browser move from `locally submitted` to `server
processing` without waiting for unrelated Strong Repair LLM jobs. The normal
end-of-pass publication still publishes applied, escalated, or finalized state.
Both publications run under the same per-track lock; the intermediate publish
never claims that downstream repair or finalization has completed.

Long refill work must not remain a permanent responsibility of this command.
The current implementation can prepare and advance a refill batch, but doing so
holds the per-track sync lock through decoder and LLM work and can prevent Issue
application for many minutes. Split that work into the refill worker described
below before treating the fifty-document queue as an unattended production
service.

Default behavior should be conservative:

- one invocation performs one bounded poll/apply/publish pass
- a `--loop` option may repeat that pass with a sleep interval, making it
  suitable for `cron`, `systemd --user`, or a terminal left open
- closing Issues requires an explicit GitHub-authenticated environment; if the
  close call fails, the sync still reports applied local state but leaves the
  Issue open
- the command should be safe to interrupt and safe to rerun
- review artifact handling is controlled by one option:
  `--publish {none,local,gh-pages}`. The default `local` regenerates
  `docs/review` after a state-changing pass without pushing. `none` applies
  pipeline state only. `gh-pages` regenerates and then runs `./publish-review`.
- decoder model refresh requests are controlled separately in
  `config/review_sync/default.toml` and can be overridden with
  `--decoder-refresh {never,on-finalize,always}`,
  `--decoder-refresh-min-new-batches`, and
  `--decoder-refresh-min-interval-minutes`. It is not a transport setting, and
  `review-sync` never performs the model build itself.

This avoids daemon-specific failure modes while preserving the path to later
automation. Once the command is stable, periodic execution can be done outside
the project with cron or a lightweight scheduler.

Current dev scheduler:

- `./ensure-review-sync-timer` installs or refreshes
  `~/.config/systemd/user/yomi-corpus-review-sync-dev.service` and `.timer`
- the timer runs `./review-sync dev --publish gh-pages` every five minutes
- it is safe to call the helper repeatedly; it rewrites the unit files, reloads
  the user manager, and enables/starts the timer
- because cluster user lingering may be disabled, an optional `.bashrc` guard
  can call the helper quietly on login:

```bash
[ -x /panfs/panmt22/users/hmanabe/yomi-corpus/ensure-review-sync-timer ] && \
  /panfs/panmt22/users/hmanabe/yomi-corpus/ensure-review-sync-timer --quiet
```

Do not hide sync errors in the helper itself. Operational failures should still
be inspected through:

```bash
journalctl --user -u yomi-corpus-review-sync-dev.service -n 80 --no-pager
```

#### 11.2.1 Review sync, refill, and decoder-refresh worker separation

Review synchronization is latency-sensitive; refill and decoder rebuilding are
throughput-sensitive. They must be separate processes with separate execution
policies and locks. Refill remains scheduled, while decoder rebuilding is
event-driven from a durable request emitted by review sync.

`review-sync` should remain a short five-minute operation:

- import and apply Bulk Review and Escalated Repair Issue submissions
- close successfully consumed Issues
- transition reviewed documents and finalize eligible batches
- atomically queue decoder refresh demand according to finalization policy and
  notify the independent decoder worker
- regenerate and publish changed review artifacts and runtime status
- publish newly imported submission state before slower batch advancement, then
  publish the final state again when the pass changes it further
- calculate refill demand, but only enqueue or reserve work rather than running
  long decoder or LLM stages

Review/refill separation is implemented by `./review-sync` and
`./refill-worker`; decoder builds are handled by `./decoder-refresh-worker`.
`review-sync` includes `refill_plan` in its summary but never prepares or
advances a refill batch. `refill-worker` reads the same configuration, resumes
the oldest batch that has not reached `final_review_prepared`, and otherwise
reserves at most `pass_limit` new documents when the aggregate pool is below
`target_ready_docs`.

`refill-worker` should own preparation of new review material:

- atomically reserve the next source documents and create a concrete batch
- advance that batch through deterministic preprocessing, hybrid reading
  generation, and LLM reading
- persist every remote job identifier and resume from durable job state after
  interruption
- commit the batch to the Bulk Review pool only after all required artifacts
  are complete
- avoid publishing intermediate stages; either request publication when the
  batch becomes review-ready or let the next `review-sync` pass publish it
- continue until the aggregate selectable Bulk Review pool reaches the
  configured target

The two processes need explicit lock scopes:

- keep the review import/apply lock short-lived and independent from expensive
  refill work
- give each refill batch its own lock so one resumable batch cannot block Issue
  processing or an unrelated batch transition
- use a short shared state lock only while reserving source documents, assigning
  stable sequence numbers, changing canonical document state, or promoting a
  completed refill batch into the review pool
- never hold a shared lock while waiting for an LLM response, running the
  decoder, or building review-page artifacts
- start with one refill worker per track; add bounded per-batch concurrency only
  after source reservation and API-capacity behavior are proven safe

The implemented lock files are deliberately independent:

- `data/state/review_sync/<track>.lock` protects Issue import/application
- `data/state/refill/<track>.lock` prevents overlapping refill workers
- `data/state/refill/batches/<batch>.lock` protects explicit resumable batch work
- `data/state/decoder_refresh/<track>.lock` protects decoder model builds

Refill summaries are written to `data/state/refill/<track>.last.json` with
timestamped history beside them. The worker always advances the captured batch
with `PipelineWorkspace.advance_batch()`; it does not rely on the track's
mutable `current_batch_name` after reservation.

For dev, install the version-controlled user units from
`deploy/systemd/user/`, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now yomi-corpus-review-sync-dev.timer
systemctl --user enable --now yomi-corpus-refill-dev.timer
systemctl --user enable --now yomi-corpus-decoder-refresh-dev.path
```

Useful diagnostics:

```bash
systemctl --user status yomi-corpus-review-sync-dev.service
systemctl --user status yomi-corpus-refill-dev.service
systemctl --user status yomi-corpus-decoder-refresh-dev.service
journalctl --user -u yomi-corpus-review-sync-dev.service -n 100 --no-pager
journalctl --user -u yomi-corpus-refill-dev.service -n 100 --no-pager
journalctl --user -u yomi-corpus-decoder-refresh-dev.service -n 100 --no-pager
```

Execution modes should also be independent. Interactive review repair may still
prefer background requests for low latency, while refill can optimize for cost
and throughput:

- use OpenAI Batch for large triage or reading workloads when delayed completion
  is acceptable
- use background mode for small dev batches, prompt validation, or work that
  should normally finish sooner
- allow execution mode, batch request limits, polling interval, and concurrency
  to be configured specifically for refill without changing review-stage LLM
  policy
- when Batch is used, submit durable remote batches and let later worker runs
  poll them; the five-minute review synchronizer must not wait for them

The steady-state dev policy keeps roughly 100 ready documents in 10-document
batches, expressed as `target_ready_docs = 100` and `pass_limit = 10`. Refill reacts to aggregate
canonical document state rather than batch creation order. A newer batch may
reach review or finish before an older one without changing Issue application,
finalization, archive publication, or decoder refresh semantics.

With `refill_worker.aligned_batch_size = 10`, the next stable document number
also shapes future batch boundaries. If it is not congruent to `1` modulo 10,
the worker emits one short bridge batch ending in `0`; it then emits full
ten-document batches with ranges such as `1031-1040`. A positive deficit
triggers a full aligned batch even when that temporarily exceeds
`target_ready_docs`, preventing a follow-up partial batch from breaking the
alignment. Existing document numbers and prepared batches never change.

#### 11.2.2 Static runtime status and client polling

The GitHub Pages UI cannot query the cluster directly. Publish a small static
runtime-status artifact alongside the review manifest so the browser can show
the last known scheduler condition and estimate the next check without turning
every five-minute timer invocation into a Pages deployment.

Keep scheduler metadata separate from review data. The review manifest describes
packs and document state; `review/runtime-status.json` describes orchestration:

- schema version and monotonically increasing `state_revision`
- generation time and last successful sync time
- status such as `idle`, `running`, `waiting_for_review`, or `error`
- recurring schedule anchor, interval, and grace period
- the last observed actual start/completion time and schedule drift
- a short current-operation summary and optional error message

Use a recurring schedule rule rather than publishing one disposable next-run
timestamp. Given `schedule_anchor`, `interval_seconds`, and `grace_seconds`, the
browser can calculate future expected slots until the server reports a changed
schedule. The server should update and publish the artifact when workflow state
changes, a run fails, a run remains active unusually long, the schedule changes,
or observed drift exceeds the grace period. If a successful run is on schedule
and makes no visible state change, leave the published status untouched.

Client polling should be adaptive:

- poll every 60 seconds under normal visible-page operation
- from shortly before an expected slot until the grace window ends, poll every
  10-15 seconds
- check immediately when a hidden page becomes visible
- pause or reduce polling to roughly five minutes while hidden
- use exponential backoff after repeated network failures
- request the stable status URL with cache revalidation; if Pages caching is
  too sticky, add a shared 30- or 60-second time-bucket query parameter rather
  than a unique timestamp per browser

Compare `state_revision` first. If it changes and no review task is active,
reload the manifest and queues automatically. If a task is open, preserve all
local edits and show a prominent "Server state updated" action instead. If the
new manifest shows that a task's documents have already left that task's stage,
mark the task stale and require explicit confirmation before any submission.

Displayed times are estimates, not service guarantees. Allow for browser clock
error and GitHub Pages deployment delay, and label future times as "expected"
rather than promising that the cluster will run at that exact moment.

Decoder refresh policy:

- dev defaults to `on-finalize`, `min_new_batches = 1`, and
  `min_interval_minutes = 0`
- working defaults to `never`, with stricter thresholds already documented in
  config for later use
- `on-finalize` queues a durable request when at least one finalized batch is
  not yet represented in the last successful decoder refresh and the
  since-last-refresh thresholds are satisfied.
- `./decoder-refresh-worker <track>` consumes that request under its own lock,
  recomputes eligibility, and performs the export and KenLM build. Review sync
  rewrites `data/state/decoder_refresh/<track>.trigger.json` whenever it creates
  or reasserts pending demand; a systemd path unit starts the worker when that
  trigger changes. There is no independent decoder polling timer.
- requests live at `data/state/decoder_refresh/<track>.request.json`. A failed
  build leaves the request in place. The next review-sync pass re-notifies the
  worker, providing bounded retries without idle polling. A successful build
  clears only the request ID it started with, so a newer request written during
  the build cannot be lost.
- worker summaries are written to
  `data/state/decoder_refresh/<track>.last.json` with timestamped history.
- refresh uses the existing `refresh_decoder_model()` path, exports all
  finalized track batches, rebuilds a track-scoped model, and updates
  `decoder_model_dir` only after a successful build
- refresh failure is non-fatal for review state. The batch remains finalized,
  the error is recorded in the worker summary, and a later worker pass retries.

Browser-local task state is separate from imported pipeline state:

- `Deferred local tasks` are in-progress browser drafts that can be resumed or
  discarded
- `Submitted local tasks` are tasks whose JSON was copied and whose GitHub
  Issue was reported as created, but whose Issue has not necessarily been
  imported yet
- a deferred local task is valid only while each target document remains in
  that task's stage. Submitted tasks instead follow the monotonic lifecycle
  `locally submitted -> server processing -> applied`
- if a submitted document temporarily disappears from active packs before its
  finalized archive entry is published, retain a disabled processing
  placeholder rather than reverting it to an ordinary item or deleting it
- compact finalized document-number ranges in the published archive manifest
  explicitly acknowledge application. Only then may the browser remove the
  submitted overlay. A move from Bulk Review to Escalated Repair likewise
  acknowledges application of the Bulk Review submission
- imported Issue state remains authoritative. Once the importer applies the
  submission, regenerated packs should move those documents to the next queue
  or to resolved state

### 11.3 Bounded LLM polling

LLM stages must be resumable, but one command invocation must not be able to
wait forever.

Policy:

- synchronous calls may still run sequentially, but operational use should
  prefer `background` or `batch`
- background and batch modes submit/poll durable job state under the stage's
  job directory
- a single invocation stops after the configured maximum wait, or earlier if no
  completed-result count increases for the stale-progress timeout
- the default source-level guard is one hour maximum wait and ten minutes
  without completed-result progress
- a stopped invocation leaves the stage incomplete with a `running` job summary
  and a `status_reason` such as `max_wait_seconds` or
  `stale_progress_timeout`
- rerunning `./next` or `./review-sync` resumes polling/submission from the same
  job state, so already completed results are not duplicated

This guard is intentionally at the shared LLM-runner layer. Yomi reading, retry
reading, and Escalated Repair should all get the same failure behavior without
each stage inventing its own timeout logic.

Pipeline status messages should surface the incomplete reason, for example:

```text
LLM background job is running (stale_progress_timeout); rerun ./next to poll or resume.
```

Repository layout migration:

- this repository continues to own pipeline code, review-pack generation,
  submission ingestion, replay semantics, and corpus state
- the current in-repo GitHub Pages UI remains acceptable while schemas churn
- the next target is a dedicated `dev` review repository for Pages and Issues
- a separate `working` review repository should come later, with stricter and
  more stable behavior
- review repositories should own only UI publication and GitHub Issues; this
  repository remains the source of truth after importing and replaying
  submissions

This is now the preferred migration direction, but it should be implemented in
small steps: first document-level state, then issue closing, then periodic sync,
then separate review repositories.

Review dashboard target:

- the review page should have one dashboard rather than a primary stage
  dropdown
- the dashboard should show `Bulk Review`, `Escalated Repair`, and `Deferred local
  tasks` together
- both active queues should allow starting a task from selected documents or a
  selected document range
- Bulk Review and Escalated Repair task screens should share the same shell:
  selected documents, queue ID, local draft key, copy/open-Issue controls,
  `Defer`, and `Complete`
- `Defer` only preserves browser-local work and returns it to `Deferred local
  tasks`
- `Complete` only clears the browser-local draft after the reviewer has copied
  or submitted JSON
- `Copy JSON and Open Issue` should copy JSON, open a pre-titled GitHub Issue
  page, and then ask on browser return/focus whether the Issue was created
- confirmed submissions move to `Submitted local tasks`; these are greyed out
  and disabled locally only while the target documents remain in the submitted
  task's stage. If regenerated queue state moves a target document to another
  stage or to resolved, the local task should drop that document; if no
  documents remain, the task should disappear
- actual pending/completed state must come from imported pipeline document
  state, not from browser-local draft state
- the old stage dropdown can remain temporarily as a debug or deep-link
  fallback, but it should not be the main workflow

Review packs are queue-aware: they embed document-level state plus queue-view
metadata. The dashboard should render active queue panels from `queue_member`
and `selectable`, not from the mere presence of a document row in a pack. This
prevents states such as the same document being simultaneously shown in Bulk
Review and Escalated Repair.
Documents that are canonical pending but not actionable should be rendered as
submitted/processing in the Pack Map, inside their previous visible bucket.
Every document should be visible in exactly one of the three user-facing
buckets: Bulk Review, Escalated Repair, or Resolved.

Pack Map is the read-only overview for current actionable work only. It should
not retain finalized batches after publication has moved on to the next active
queue. Clicking a Pack Map document opens an in-place preview. If the browser
has an active, deferred, or submitted local draft for that document, the preview
may overlay that draft so the reviewer sees the latest local state, but only
while the draft's task stage still matches the document's current stage. This
never changes canonical queue membership; only imported pipeline state moves a
document between Bulk Review, Escalated Repair, and Resolved.
The preview is document-level, not review-item-level: it should show every unit
in source order. For a resolved document, lazily load the latest finalized
archive record and render its full unit list rather than showing only the units
that happened to remain in a review pack.

Finalized documents belong in a separate Archive Browser, not in the active
Pack Map. The archive is published as static, lazily loadable JSON shards under
`docs/review/archive/` plus a small `archive/index.json`. The archive UI is
read-mostly: choose a shard or search the source text, then open a finalized
document directly in the correction editor. Merely opening the editor does not
mutate canonical data. If a resolved document needs correction, the UI creates
an auditable correction Issue against finalized raw yomi data. This keeps daily
review uncluttered while still making completed corpus data inspectable and
correctable.

The first finalized-correction milestone is Issue export only. Corpus Map tiles,
document rows, and search results all open the same row-based rendered-yomi
editor directly; there is no separate read-only preview or correction-request
step.
Each finalized unit remains collapsed by default. The reviewer expands only the
unit(s) that need correction; expanding reveals only the current rendered-yomi
editor for that unit. The raw source text is not shown separately because the
ruby preview already provides source context and the editable object is the
rendered-yomi string. A row edit is not exported until the reviewer explicitly
saves it. Saved rows collapse, show a read-only `Rendered Yomi` line below the
ruby preview, and can be reopened for further editing. `Clear` removes a saved
pending correction and returns the row to its original unedited state. Multiple
saved units can be exported in one correction request.

The same editor permits finalized disposition changes. A normal row offers
`Skip` and `Exclude`; a skipped row offers `Restore` and `Exclude`. These
choices update row styling immediately and may be reversed while still a local
draft. Server application moves recoverable skips between final and skipped
artifacts atomically. Confirmed exclusions become content-free tombstones and
cannot be restored or edited through Corpus Map.

The normal correction unit is exactly one finalized unit. Changing sentence or
unit boundaries is intentionally out of scope for this first workflow because
it affects source identity, review history, archive replay, decoder corpus
export, and ruby dictionary harvesting. Boundary-changing corrections should
be a later workflow with a distinct payload type and stronger server-side
validation.

Browser validation must reject:

- missing, extra, reordered, or changed unit IDs
- rendered-yomi tokens without `/`
- empty token surfaces
- readings that violate the canonical `surface/reading` structural rule:
  kanji or Latin surfaces normally need a katakana reading, Arabic and Unicode
  Roman-numeral surfaces need an empty reading, and kana/symbol surfaces need
  their normalized literal reading. Numeric-only includes ASCII/fullwidth digits, Unicode Roman numeral
  symbols such as `Ⅲ`, and multi-character digit-style Japanese numeral runs such
  as `二五`, `二〇〇二`, and `二○二六`, but not ASCII Roman-looking strings such as
  `III`. Unlike Arabic digits and Roman numeral symbols, those multi-character
  Japanese digit runs accept either no reading or a katakana reading. Default
  generation blanks numeral uses identified by Sudachi but preserves proper-name
  readings such as `一二三/ヒフミ`; human review may add or remove the reading.
  Japanese numeral runs containing units such as `十`, `百`, `千`, `万`, or
  `億` use ordinary lexical readings, as do single lexical numerals such as `七`.
  Standalone notation symbols `〇` and `○` remain no-ruby. Thus `Ⅲ/` and
  `二〇〇二/` are canonical,
  `Ⅲ/サン` is invalid, `III/スリー` can be valid,
  and `III/` is invalid. Mixed lexical surfaces such as `聖飢魔Ⅱ` still require a
  normal katakana reading. The deterministic symbolic-kaomoji marker is another
  narrow exception: a multi-character, symbol-bearing surface may use
  `カオモジ`, including faces containing Japanese characters such as `ノ`.
  Semantic parentheticals such as `（笑）/カオモジ` remain invalid.
- source-surface changes relative to the original rendered-yomi token surfaces
  after removing whitespace. In practice the UI should ignore differences
  between ASCII spaces and NBSP here, because finalized archive data may contain
  NBSP-like source-space tokens that can be normalized during editing.
- submissions with no yomi change

The browser exports only saved changed units, not unsaved textarea drafts and
not the whole document.
Accepted UI payloads use:

```json
{
  "submission_type": "finalized_correction_patch",
  "schema_version": 2,
  "submission_id": "finalized_correction__client__dev_34__...",
  "track_name": "dev",
  "review_stage": "finalized_correction",
  "doc_id": "...",
  "track_doc_seq": 34,
  "batch_name": "dev_batch_0004",
  "base_archive_revision": "0123456789abcdef",
  "source": {
    "archive_index_path": "review/archive/index.json",
    "archive_shard": "review/archive/docs_000001_000035.json",
    "page_url": "..."
  },
  "units": [
    {
      "unit_id": "...",
      "unit_seq": 1,
      "text": "...",
      "original_yomi_tokens": [["表記", "ヨミ"]],
      "proposed_yomi_tokens": [["表記", "ヨミ"]]
    }
  ]
}
```

The server dual-reads legacy schema-v1 `original_rendered_yomi` and
`proposed_rendered_yomi` fields while historical submissions remain, but all
new browser submissions and finalized writes use compact token arrays.

The browser retains this stable `submission_id` while a local draft is edited
and after it is marked submitted. Archive units publish the correction IDs
that were actually applied. A `sent` marker is cleared only when every unit in
that submission acknowledges the same ID. Archive revision changes alone are
not acknowledgements. Legacy submitted browser records without IDs are dropped
when the local correction store migrates to schema version 2; draft records are
preserved.

Server-side import and replay are separate follow-up work. The importer should
repeat the same validation, apply accepted patches to canonical finalized
state, regenerate archive shards, and feed the usual downstream harvesters.

Long-term target: move from a batch-centered review page to a document-centered
workspace.

In that target, backend batches are only processing chunks. They are not the
main unit the reviewer sees. While a reviewer works on one slice, for example
documents 31-40, the orchestrator may prepare later slices such as 41-50 and
append newly reviewable documents to the same workspace. The UI may therefore
show documents that came from multiple backend batches, as long as every
document has stable IDs and canonical server-side state.

Future/unprocessed documents are out of scope for the review UI for now. They
are cheap to produce later but make the current state model harder: they add no
immediate review value, complicate numbering, and blur the difference between
current work and corpus browsing. If a full-corpus map is needed later, it
should be a distinct mode that combines static archive shards with lightweight
raw-text shards, not an extension of the active review queue.

This requires implementation discipline:

- keep server-side document state as the source of truth
- keep browser-local state as a temporary overlay for active, deferred, and
  submitted work only
- treat backend batches as append-only artifacts and logs, not as user-visible
  queue boundaries
- load archive data lazily or in shards; do not put every finalized review
  payload into the first page load
- use virtualization if archive browsing grows beyond simple shard pages
- store compact static index data separately from heavier finalized payloads

Resolved documents should eventually be correctable too. A correction to a
resolved document should create a new correction task or Issue, replay through
the same importer/audit path, update canonical document state, and feed the
same downstream harvesters for exact rewrite defaults and ruby dictionary
entries. Resolved correction should be possible, but it should be auditable and
should not silently mutate historical review submissions.

Display:

- the current best-effort yomi-annotated sentence
- three checkboxes

The three checkboxes are:

1. non-target status
2. yomi fully correct

Important intended behavior:

- the first checkbox may already be prefilled by mechanical or LLM output
- the yomi checkbox should start unchecked
- the yomi-annotated sentence already contains the original sentence content, so
  a separate raw-text field is unnecessary in this UI
- a sentence that is already known to have incorrect yomi should not be shown as
  a knowingly bad candidate; instead the pipeline should first attempt repair

Minor alphabetic review should be visible in this sentence-level UI only as
pre-checked `Skip` suggestions with reasons. The reviewer should not have to
visit a separate entity-level UI for routine cases.


## 12. Human Review: Pass 2

Units that still fail the yomi check after the best-effort pipeline should enter
a second, more expensive path.

## 12.1 Expensive repair

For these units:

- use `gpt-5.6-sol` as the default rescue model
- allow expensive tooling such as web search and stronger reasoning if needed
- generate a new best-effort yomi

Only if that still fails after human review should the pipeline consider a
`gpt-5.5-pro` escalation. That should be treated as a last resort for a very
small tail, not part of the normal path.

## 12.2 Bulk Review UI

The Bulk Review UI should look primarily like ruby-annotated text, not like a
table of records. Sentence-level metadata should be hidden except for compact
controls:

- skip checkbox
- range-marker menu
- tappable ruby spans

For each highlighted target, tapping cycles through the recorded reading
candidates and two explicit no-ruby states. `−` means that no ruby is an
intentional accepted result; `?` means that the reading is unresolved and must
enter Escalated Repair. A selected reading is applied directly. Consecutive
`?` targets are grouped for repair, while `−` targets never become repair
atoms. Multi-character Japanese numeral runs remain tappable even when `−` is
their deterministic default, so lexical exceptions such as `七五三` can select
a dictionary reading without weakening the general no-ruby default.

### 12.2.1 Interaction spans and ruby projection

Bulk Review must edit complete reading-bearing surface spans, not the individual
kanji nodes produced by furigana alignment. Define four distinct layers:

1. The **corpus token sequence** is canonical rendered yomi such as
   `後払い/アトバライ`.
2. An **interaction span** is a stable source range used by the browser and
   submission format. It normally covers one corpus token, but may cover
   adjacent tokens when candidate generation intentionally treats them as one
   reading or boundary decision.
3. A **candidate** belongs to the complete interaction span and contains a
   replacement token sequence whose surfaces concatenate to the span surface.
4. A **ruby projection** is display-only alignment from that candidate onto the
   source characters. It may cover less than the interaction span.

For `後払い/アトバライ`, the browser should render `後払(あとばら)い`, while
the hover background, pointer hit box, tap cycle, edited-state color, and
no-ruby action cover all of `後払い`. The okurigana remains visually outside the
`<rt>` annotation but inside the interactive wrapper. The same principle
applies to inflected forms such as `行って` and multi-character lexicalized
units such as `1日`.

Each review-pack interaction span should carry at least:

```json
{
  "span_id": "stable within the pack",
  "unit_id": "source unit",
  "start": 12,
  "end": 15,
  "surface": "後払い",
  "default_candidate_id": "hybrid",
  "candidates": [
    {
      "candidate_id": "hybrid",
      "tokens": [["後払い", "ゴバライ"]],
      "ruby_nodes": [{"surface": "後払", "reading": "ごばら"}]
    }
  ]
}
```

Offsets are measured against the preserved source text and are the primary
identity within a unit; `surface` is an integrity check. Interaction spans must
not overlap within one candidate view. Candidate token surfaces must concatenate
exactly to `surface`, and ruby nodes must preserve source order without claiming
characters outside the span. The browser may derive ruby nodes when they are
not stored, but must never derive the interaction boundary from those nodes.
Digit-plus-kana compounds retain one canonical token while leaving the kana
suffix outside the visual ruby base, so `1つ/ヒトツ` renders as `1（ひと）つ`.
All Han checks include supplementary CJK planes; `𠮟` and `𩸽` must not fall
through to the kana/symbol path merely because they are outside the BMP.

Submission overrides should identify `span_id` and the selected candidate, or
record `none` for rejection. Replay replaces the complete span token sequence.
Escalated Repair receives exactly the rejected interaction surface and may
change its internal segmentation, but its returned surfaces must still
concatenate to that surface. Adjacent rejected interaction spans may be merged
for one repair request; the merged source range, not adjacent ruby nodes,
defines the repair boundary.

Bulk Review also supports explicit repair atoms for common local segmentation
errors. After a kanji-bearing interaction span is changed to no ruby, its
immediately adjacent canonical kana or numeric token becomes tappable. The
selected text token is neutral rather than attached to one side. The browser
recomputes maximal contiguous components from selected text atoms and cancelled
interaction spans, then emits canonical `kana_merge_no_reading`,
`numeric_merge_no_reading`, or mixed repair span overrides. Thus selecting the
kana between two cancelled kanji joins all three regardless of whether the
kana or either kanji was selected first. This remains explicit and
one-token-at-a-time: the browser does not infer how far a neighboring run
belongs to the rejected word.

Repeated-cancellation suggestions are derived from the complete connected
component containing the most recently touched target, not merely from atoms
touched while one transient suggestion remains visible. Exact surface layout
and selected bridges must match within the same document, while prior readings
may differ. The suggestion is positioned near the triggering target and clamped
to the current visual viewport so it remains usable when an iPad page is
zoomed.

Implementation should proceed without migrating finalized corpus data:

1. Extend pack generation with interaction spans while preserving legacy target
   fields and current browser behavior.
2. Move candidate collection, default selection, and submission replay to the
   span schema; add invariants for offsets, surfaces, and replacement tokens.
3. Change the browser wrapper and event handling so a complete span is one
   tappable region while nested ruby nodes remain presentation-only.
4. Generate strong-repair groups from rejected spans and regenerate all active
   packs/queues. Existing local drafts should either be translated by exact
   unit/offset matching or invalidated visibly rather than guessed.
5. Remove kanji-target compatibility code after no active review artifact
   depends on it.

General segmentation editing belongs to the Escalated Repair confirmation UI.
That UI can toggle split points between characters and rebuild the corresponding
reading fields. Bulk Review only provides the constrained adjacent-token merge
described above; keeping arbitrary boundary editing out of Bulk Review avoids
two competing span editors and preserves its fast tap-to-review workflow.

## 12.3 Escalated Repair and finalization

Escalated Repair should operate on local target groups produced by human ruby
cancellation, not on whole sentences by default. The model receives the source
text, current yomi, rejected span, and rejected readings, and returns replacement
surface/reading items. The application step validates that replacement surfaces
concatenate to the rejected span and that readings are valid kana.

After Escalated Repairs are applied, the reviewed units can be finalized. Final
postprocessing should continue to validate:

- rendered-yomi format
- surface preservation
- reading normalization


## 13. Batch Execution Model

The pipeline should run in batches of documents.

Current initial preference:

- start with batches of about 100 documents
- later scale upward once the process is stable

Within a batch:

- import documents
- derive units
- run mechanical analysis for every unit
- query unresolved reading targets with the configured LLM
- run LLM yomi repair where needed
- build human review queues

### 13.1 Current post-review stages

The current yomi pipeline now continues after `final_review_prepared`.

Implemented stages:

- `final_review_applied`
- `yomi_strong_repair_queued`
- `yomi_finalized`

`final_review_applied` reads local review submission JSON files from:

```text
data/review_submissions/yomi_final/
```

The submission format is the JSON exported by the GitHub Pages review UI:

- `review_stage` must be `yomi_final_review`
- `pack_id` must match the generated review pack
- `reviewed_ranges` define which sentence items were actually reviewed
- sparse `overrides` carry `skip` and target-level reading choices
- no-ruby choices retain `choice_source: "none"` for compatibility, but their
  candidate ID and `no_ruby_state` distinguish unresolved `?` from intentional
  `−`; only unresolved choices enter Escalated Repair
- automatic accepted no-ruby defaults select `accepted_none`, retain
  `automatic_default: true`, and are not human rejections; legacy submissions
  without a candidate ID infer this state only for automatic accepted defaults
- `skip` dominates operational output: target choices on a skipped sentence are
  preserved as audit data, but they are not applied to rendered yomi and do not
  trigger Escalated Repair

When `./next` reaches `final_review_applied`, it first scans open GitHub Issues
for yomi Bulk Review submissions and stores matching payloads in that local
store. The apply step then replays the local store. If GitHub import fails or
finds no matching submission, the stage falls back to the existing local-store
check and blocks as a human review gate.

For debugging or manual recovery, a single Issue can still be imported
explicitly:

```bash
python scripts/import_yomi_final_review_issue.py --issue-number 1
```

`yomi_strong_repair_queued` should focus on canceled target groups. Target-level
`choice_source == "none"` choices are collected in token order, and consecutive
canceled targets in the same sentence become one `repair_scope:
"target_group"` queue entry. These groups need focused Escalated Repair rather
than serving as confirmed local constraints. Skipped sentences are excluded
from this queue. The Escalated Repair prompt should decide whether web
search is needed from the target context and rejected readings, and should
record whether search was actually used.

For a canceled ruby, the queue should preserve the rejected reading explicitly
as `rejected_readings`. This is useful for strong/web repair cases: for example,
if human review rejects `史輝/ふみてる` in the publisher-name context
`史輝出版`, the strong model should receive both the canceled span and the fact
that `ふみてる` is known wrong. The fixture
`data/evals/yomi_strong_repair/regression_v1.jsonl` records this kind of
target-level web-repair case.

The same open-Issue scan that `./next` runs can also be invoked manually:

```bash
python scripts/import_yomi_final_review_inbox.py
```

The older alphabetic Issue importer has been retired. Alphabetic entity
judgments are now treated as provisional pipeline evidence, while final
skip/keep corrections are handled in the Bulk Review UI.

Merge rule:

- replay submissions by `generated_at_epoch`, `submission_id`, and source path
- a reviewed range defaults every item in that range to accepted
- sparse overrides apply on top of that default
- later overlapping submissions overwrite earlier ones

If no matching submission exists, `final_review_applied` blocks with a human
review gate. This is intentional: even on `dev`, final yomi review should not be
silently skipped.

`yomi_strong_repair_queued` creates the local Escalated Repair input queue:

```text
data/units/<batch>/yomi_strong_repair_queue.jsonl
data/units/<batch>/yomi_strong_repair_queue_summary.json
```

Queue entries are generated for:

- consecutive target-level `choice_source: "none"` overrides as `repair_scope:
  "target_group"` and `repair_order: 1`

Older sentence-level escalation should be treated as legacy/fallback plumbing,
not the preferred review path. The current design expects reviewers to cancel
the local problematic targets instead.

`yomi_strong_repair_llm_completed` runs `config/llm/yomi_repair.toml` on that
queue, using the track's `yomi_repair` profile and execution mode. The current
dev profile is `economy` (`gpt-5.4-mini`) while working remains `standard`
(`gpt-5.5`) in the committed runtime profile until that mapping is migrated;
the current documented target for `standard` is `gpt-5.6-sol`. Web search is
available with medium context, but the model is told to use it only when its
own knowledge is insufficient. Every result still goes through human
confirmation.

The stage writes:

```text
data/units/<batch>/yomi_strong_repair_results.jsonl
data/units/<batch>/yomi_strong_repair_usage_summary.json
data/units/<batch>/units.yomi.strong_repaired.jsonl
data/units/<batch>/yomi_strong_repair_apply_summary.json
```

Application is intentionally conservative. For now, target-group repairs are
applied only when the parsed JSON array is valid, every reading is kana-only,
and the concatenated returned surfaces exactly match the rejected span. Missing,
parse-failed, sentence-scope, or surface-mismatched rows block the stage rather
than being silently accepted.

`yomi_finalized` reads `units.yomi.strong_repaired.jsonl` when it exists. If the
Escalated Repair queue is non-empty, finalization requires a later human
confirmation step even when every LLM repair was mechanically applied. This is
intentional: Escalated Repair results are candidates, not final truth. If no
successful apply summary exists, or if confirmation is missing, finalization
blocks. The confirmation is stored by applying submissions for the
`yomi_strong_repair_review` pack; when all repair items are reviewed and none is
rejected, `yomi_strong_repair_apply_summary.json` is marked `confirmed`.
Otherwise finalization remains blocked. When confirmed, finalization writes:

```text
data/units/<batch>/units.yomi.final.jsonl
data/units/<batch>/yomi_finalize_summary.json
data/units/<batch>/manual_yomi_rewrites.jsonl
data/units/<batch>/supplemental_furigana.tsv
data/units/<batch>/yomi_finalization_harvest_summary.json
```

The `yomi_strong_repair_review` UI may also submit `manual_segments` for a
repair item. This is the first manual correction path for local Escalated Repairs:
the rejected span is displayed inline, boundaries between characters can be
toggled, and the UI emits segment-level `surface/reading` pairs. During
confirmation these manual segments override the LLM repair only if their
surfaces concatenate exactly to the rejected span and all readings are valid
kana. Invalid manual segments keep confirmation incomplete rather than being
silently applied.

The short rationale returned with an Escalated Repair is durable evidence, not
temporary debug text. Number distinct rationales in document order and show a
compact marker such as `*1` immediately after the repaired span, with the
matching `*1 rationale` below the ruby text. No navigation links are required.
Persist the repaired surface, rationale, and web-search usage in the unit's
strong-repair metadata, carry them through finalization, and expose the same
markers and notes in finalized Corpus Map previews.

For each rejected Escalated Repair span, the review pack should include
dictionary-backed reading candidates for substrings found by prefix-style
lookup from every character position. For example, `池尻中学校` should carry
candidates for substrings such as `中学校` and `学校`, and ambiguous entries
such as `池尻` may carry multiple readings. The UI currently uses the first
candidate as the default reading when a boundary edit creates that segment, but
the full candidate list is preserved in JSON for later richer controls.

Skipped units are excluded from `units.yomi.final.jsonl`. Reviewed, non-skipped
units are retained.

At batch finalization, the pipeline also harvests three conservative reusable
artifacts:

- exact Escalated Repair rewrite rules, appended de-duplicated to
  `data/lexicon/manual_yomi_rewrites.jsonl`
- supplemental furigana allocations, appended de-duplicated to
  `data/lexicon/supplemental_furigana.tsv`
- accepted Escalated Repair readings, appended with provenance to
  `data/lexicon/learned_yomi_readings.tsv`

Manual yomi rewrites are reserved for repairs whose accepted token boundaries
differ from the rejected token boundaries. They affect future
tokenization/readings only by exact surface-span match. For example, if a reviewed repair establishes
`池尻中学校 -> 池尻/イケジリ 中学校/チュウガッコウ`, a later exact
`池尻中学校` occurrence can use that as a default. Do not generalize these
rules into regexes until there is explicit evidence. Compare replacement
boundaries separately from readings when historical evidence differs. If all
evidence agrees on boundaries but supports multiple readings, preserve the
learned segmentation while carrying forward the current mechanical reading;
expose every accepted reading as a review candidate. For example, conflicting
`貢船/コウセン` and `貢船/ミツギブネ` evidence still establishes the single
span `貢船`. Only genuinely different boundary sequences are omitted and
reported as exact-rewrite conflicts.

Reading-only repairs do not become unconditional defaults. Their accepted
`surface/reading` pairs are added to `learned_yomi_readings.tsv` and appear as
additional candidates in later Bulk Review and Escalated Repair interfaces.
Thus an accepted `一日/イチニチ` adds `いちにち` alongside legitimate readings
such as `ついたち`; it does not globally replace the mechanical default. A
boundary-changing repair contributes both its exact rewrite default and its
component readings as reusable candidates.

Human-edited Escalated Repair segments supersede the LLM proposal for harvesting.
Every learned row retains batch, track, unit, item, and method provenance. Rebuild
the canonical artifacts deterministically from finalized batches with
`scripts/rebuild_learned_yomi_lexicons.py`; do not treat append order as authority.

Supplemental furigana is display-only. It records accepted `surface/reading` to
annotated-form mappings not already present as exact Sudachi-derived dictionary
entries, so future ruby rendering can load it alongside
`sudachi_20251022.tsv`. It must not change the underlying corpus format, which
remains `surface/reading`.

Simple target reading choices are applied directly to exact rendered yomi
tokens when the reviewed target covers the whole token. Harder cases, such as
no-ruby targets or future token-boundary corrections, should go through the
Escalated Repair queue.

### 13.2 Decoder model refresh boundary

Updating the `yomi-decoder` language model should be an explicit maintenance
workflow, not a normal per-batch `./next` stage.

Reason:

- a decoder model refresh changes future batch behavior
- several finalized batches may be aggregated before rebuilding
- small `dev` batches are often experimental and should not automatically
  affect the decoder
- comparisons are easier when model refreshes are deliberate experiment
  boundaries

Boundary:

- `yomi-corpus` owns reviewed-corpus export and model-version selection
- `yomi-decoder` owns model building and decoder internals

Track policy:

- `dev` and `working` should have separate reviewed-corpus exports
- `dev` model refreshes may use dev-finalized batches, but should not
  automatically affect `working`
- `working` model refreshes should use only accepted working material, or
  explicitly promoted dev material
- a batch should pin the decoder model it used at batch creation time
- model changes should affect only later batches, never a batch already in
  progress

Recommended artifact layout:

```text
data/decoder_corpora/dev/<batch>.txt
data/decoder_corpora/working/<batch>.txt
data/decoder_models/dev/<model-id>/
data/decoder_models/working/<model-id>/
```

Implemented low-level interface:

```bash
python /path/yomi-corpus/scripts/export_decoder_corpus.py \
  --batch reviewed_batch_name

python /path/yomi-decoder/scripts/build_model.py \
  --base-corpus /path/core_SUW_yomi_final.txt \
  --extra-corpus /path/yomi-corpus/data/exports/decoder_corpus/reviewed_batch_name.txt \
  --output-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD
```

Then this project should call the decoder with the chosen model directory:

```bash
python /path/yomi-decoder/scripts/decode.py \
  --model-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD \
  ...
```

Operator-facing refresh command:

```bash
python scripts/refresh_decoder_model.py --track dev
python scripts/refresh_decoder_model.py --track working
```

That wrapper:

- find finalized batches for the selected track
- export missing decoder corpora
- build a new model under `data/decoder_models/<track>/...`
- update the track state with the latest model path

Retention policy:

- frequent refreshes are acceptable because rebuild cost is background compute,
  not LLM cost, but full model directories are not free to keep forever. Current
  dev model snapshots are roughly 210 MB each, mostly `model.arpa`,
  `ngram_corpus.txt`, `model.klm`, and `lexicon.jsonl`.
- keep the latest model and a small recent window of full model snapshots for
  debugging and rollback
- for older models, keep provenance rather than full runtime artifacts. The
  retained manifest should be enough to reconstruct the model if needed.
- old-model provenance should include model ID, build timestamp, track,
  yomi-decoder version or commit, base corpus identity, included finalized
  batch/document IDs, hashes or versions of exported yomi rows, build config,
  thresholds, aggregate corpus/model counts when available, and hashes of the
  generated runtime artifacts
- publish models atomically: build into a new versioned directory, validate it,
  then update the track's latest-model pointer. If cleanup removes old heavy
  artifacts, it must not touch the latest pointer target.

When `prepare` or `./next` starts a new batch, it copies the track's latest
decoder model path into the batch state. The batch then uses that pinned path
for all Sudachi/decoder hybrid generation.

Each batch manifest should eventually record:

- decoder executable/version
- base corpus path or version
- extra reviewed-corpus inputs
- model output directory or model ID
- build timestamp
- relevant build parameters

For now, the pipeline should produce stable finalized artifacts and support an
explicit refresh command. A periodic sync may later trigger that refresh
automatically whenever newly finalized output makes the decoder corpus dirty.

### 13.3 Auto advancement

`./next --auto` repeatedly advances the current batch until one of these happens:

- a human gate blocks progress
- a stage is still incomplete
- confirmation is required
- the final stage is reached
- an error/blocking reason appears

Plain `./next` remains single-step and should be preferred while debugging a
specific stage.


## 14. Unclear or Open Points

The following points are not settled and should be treated as explicit open
questions:

1. When should non-target feature extraction become a real
   mechanical classifier?
   The current idea is to rely partly on Sudachi and N-gram analysis quality,
   but no reviewed-data-backed scoring rule exists yet.

2. When should sentence-level `certain` gating be turned on for non-target and
   yomi tasks?
   The branching logic is clear, but it should remain disabled until there is
   enough reviewed data.

3. How much context should be shown in entity-level alphabetic review?
   The preferred unit is now the entity type, but some cases such as `OK` or
   `Lab` may still need representative sentence examples.

4. What exact format should represent unit-local nested analysis?
   The current example is only a draft.

5. How should the mechanical yomi confidence score eventually be computed from
   Sudachi and `yomi-decoder` outputs?

6. At what point should reusable rules be harvested automatically from reviewed
   cases?
   Current preference is after human review, not before.

7. How should the second-pass expensive LLM workflow be constrained so that cost
   does not grow too quickly?


## 15. Immediate Next Steps

The basic small-batch loop exists. The next work should be done in this order:

1. regenerate the active `dev` Bulk Review pack whenever candidate-generation
   logic changes
2. complete the current dev Bulk Review, import the GitHub-Issue submission,
   and apply it
3. finalize the batch if no Escalated Repair targets remain
4. implement real Escalated Repair for canceled ruby target groups, using optional
   sentence-level web search when the reviewer requests it
5. add conservative learned default repairs from accepted human/strong-LLM fixes
6. propagate human skip/unskip decisions back into alphabetic token decisions
   once the Bulk Review UI exposes that override cleanly
7. export finalized yomi into a supplemental decoder corpus and rebuild the
   track-local decoder model
8. run several more small dev batches before changing `working` defaults

The immediate blocker is no longer theory or initial infrastructure; it is
closing the review-to-finalization loop on concrete dev batches.
