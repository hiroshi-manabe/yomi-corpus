# Pipeline Design

## 1. Goal

Build a reproducible pipeline that turns filtered modern Japanese source text
into a reading-annotated corpus, while preserving enough metadata to:

- reject clearly non-modern material
- eventually auto-accept only very safe cases, but not before reviewed data
  exists
- auto-fix simple surface/reading boundary problems
- escalate harder cases to an LLM with the right amount of context
- send the final uncertain tail to human review
- promote reviewed data back into the core corpus used by `yomi-decoder`


## 2. Repo Boundary

This repository should own orchestration, staged artifacts, flags, review
queues, and promotion logic.

It should not reimplement the other projects.

External inputs and dependencies:

- Source corpus:
  `../llm-jp-corpus-v4/data/filtered/*_kept.jsonl.gz`
- Decoder:
  `../yomi-decoder/`

Recommended rule:

- `yomi-decoder` stays the place for decoding logic and decoder-model building.
- `yomi-corpus` stays the place for corpus ingestion, confidence decisions,
  repair pipelines, review, and export.
- OpenAI calling, prompt iteration, batch orchestration, and usage accounting
  should live in this repository's own LLM layer.


## 3. Key Design Decisions

### 3.1 Keep the canonical unit small and simple

The annotation unit should usually be a sentence or short line.

Each unit record must keep:

- `doc_id`
- `unit_id`
- `unit_seq`
- `char_start`
- `char_end`
- `text`
- `source_file`
- `source_line_no`

The source JSONL remains unchanged. Units are sequential spans over the source
document text, and the core invariant is that unit text must remain recoverable
from the original document by offsets.

### 3.2 Keep analysis inside each unit record

The current direction is to avoid separate `candidate`, `decision`, and
`review` record families.

Instead, each unit should contain nested analysis blocks such as:

- `analysis.mechanical`
- `analysis.llm`
- `analysis.human_review`

This is intentionally denormalized in favor of operational simplicity.

### 3.3 Features first, gated decisions later

The first pass should not ask the API to "solve" the whole problem.

Instead:

1. generate raw mechanical features and a mechanical yomi with Sudachi B-mode
   segmentation and
   `yomi-decoder`
2. keep non-target and sentence-level "certain" judgments disabled
   until enough reviewed data exists
3. ask the LLM to classify units that currently have no trusted mechanical
   decision
4. ask the LLM for yomi repair only when yomi correctness is not accepted
5. send best-effort output to human review

For yomi quality, reading correctness should take priority over coarse vs. fine
segmentation. If the readings are correct, extra splits introduced by the
morphological analyzer should not by themselves count as a failure.
This includes over-split katakana expressions such as `ディ/ディ プロ/プロ
マ/マ`: awkward segmentation is tolerable at this stage if the assigned
readings are correct. Cleaner token merging can be handled later if downstream
consumers need it.

Decoder-driven reading changes should be gated by support evidence, but they do
not need to be rare. If `yomi-decoder` differs from Sudachi on the same surface
span and the exact decoder entry has real N-gram support, the hybrid layer may
use the decoder reading as a tentative override. If the decoder appears to rely
only on unigram fallback, its output should not override Sudachi.

`piece_orders[0] >= 2` should be interpreted according to the decoder's current
count threshold: order-2 support means the boundary transition is attested at
least twice in the decoder corpus. A singleton 2-gram is not enough. This keeps
cases like a one-off `と-中(チュウ)` transition from becoming trusted evidence
even if the decoder still ranks that reading highly for other reasons.

For splitting one Sudachi token into multiple decoder entries, support must
cross the split boundary. Each decoder entry after the first must start with
`piece_orders[0] >= 2`; a later entry that starts at order 1 and only gains
support internally is not evidence for the boundary itself.

The configured hybrid strategy may also merge multiple contiguous Sudachi
tokens into one decoder entry. This is intentionally stricter than a same-span
reading override: every value in the merged entry's `piece_orders` must be at
least 2. Decoder-driven splitting remains disabled. Thus strongly supported
groupings such as `戦/セン 争/争` becoming `戦争/センソウ` can repair a bad
Sudachi boundary, while an entry such as `楽し/タノシ` with orders `[1, 2]`
cannot replace `楽/ラク し/シ`.

Stable two-kanji confidence experiments should not let decoder tokenization
become the decision surface. The hybrid rendered tokens are the units being
judged, and decoder evidence is projected onto those character spans. If the
decoder covers a hybrid token only by disagreeing subpieces, such as
`古/コ 本屋/ホンヤ` for `古本屋/フルホンヤ`, that token remains unsupported.
Likewise, a stable two-kanji token can forgive only itself; it does not make
the following boundary safe.

The stable-token inventory should come from raw SudachiDict CSV files, not from
compiled-tokenizer lookup. Component-only rows with `-1,-1` connection IDs
must count because they can expose readings used inside larger compounds. The
rule is reading uniqueness, not POS: unique proper nouns are allowed, while
surfaces with any additional raw dictionary reading are rejected.

The hybrid result may then pass through a post-hybrid repair memory. The first
version keeps this deliberately simple: every active rule is a regular
expression substitution over the rendered yomi string, with pair-bounded
literal repairs written as patterns such as
`(?<!\S)若しくは/モシクワ(?!\S)`. These repairs are best-effort systematic
fixes, not review-skip evidence. Each application should be logged with the
rule ID, matched text, replacement, count, and source so later review can audit
or revert the rule.

Numeric runs are not treated as ordinary yomi targets. Consecutive numeric
tokens should be grouped and emitted with an empty reading, for example
`2021/`, `2,035.28/`, or `二〇〇二/`, so a future number-reading module can handle them separately.
Thousands separators, decimal points, and an optional leading sign belong to
the numeric token only when the complete expression is structurally valid;
units and currency symbols remain separate. Japanese
numeral digits are numeric only when the whole surface consists of digit
characters; mixed lexical surfaces such as `二年` and `一人` retain their normal rules. Unit
kanji such as `万` and `京` are not intrinsically numeric. This
must also be explicit in any yomi-triage prompt: `2021/`, `30/ 分/フン`, and
`1/ 回/カイ` are intentional, not malformed yomi, and should not trigger
`Review` by themselves.

Canonical yomi storage is a versioned JSON token array, not a slash-delimited
string:

```json
{"token_schema_version": 1, "tokens": [["学校", "ガッコウ"], ["です", "デス"], ["。", "。"]]}
```

Each two-item array is `[surface, reading]`. This representation is lossless
when source text contains `/`, whitespace, or backslashes. Slash-delimited
`surface/reading` text remains a derived prompt/debug/editor view only and must
not be used as the persisted source of truth.

Canonical tokens should also satisfy a structural validity
rule before any `OK` decision is trusted:

- if `surface` contains kanji or Latin letters, `reading` must be non-empty and
  contain only katakana plus the long-vowel mark `ー`, except for the Japanese
  digit-run case below
- if `surface` is numeric only, including ASCII/fullwidth digits and Unicode
  Roman numeral symbols, `reading` must be empty, so `2021/` and `Ⅲ/` are valid
  while `2021/2021` and `Ⅲ/サン` are invalid. A multi-character digit-style
  Japanese numeral run may have either an empty reading or a katakana reading;
  `二〇〇二/` and `一二三/ヒフミ` are both valid. Generation defaults numeral
  uses to no reading but preserves a valid Sudachi proper-name reading, and
  human review may change either choice. ASCII Roman-looking strings such
  as `I`, `II`, and `III` remain alphabetic surfaces, so `III/スリー` can be
  valid while `III/` is invalid.
- otherwise, `reading` must equal the result of converting hiragana in
  `surface` to katakana while leaving non-kana characters unchanged, so
  `です/デス` and `。/。` are valid

Finalization normalizes this last category deterministically. In particular,
spoken interpretations of symbol-only surfaces are not canonical readings:
`～/カラ` becomes `～/～`, `%/パーセント` becomes `%/%`, and kana-plus-symbol
tokens preserve the symbol literally. If a kanji/Latin token is structurally
invalid, finalization may recover it only from one unique valid reading already
recorded by human final review; otherwise it must stop rather than guess.

This is a format guardrail, not a semantic yomi correctness rule. A unit with a
structurally invalid token can still be sent to the LLM for `Skip` detection,
but an LLM `OK` must be forced back to `Review`.

Original source whitespace should be preserved in the canonical yomi token
stream. Before Sudachi and decoder processing, convert source ASCII space
`U+0020` to NBSP `U+00A0`; keep full-width space `U+3000` unchanged. The JSON
array stores whitespace surfaces without delimiter ambiguity. In the editable
compatibility view, escape literal `/` as `\/`, backslash as `\\`, and ASCII
space as `\s`; these escapes are UI syntax and never alter canonical source
text.

N-gram support is currently an experimental confidence feature, not a committed
pipeline gate. The useful diagnostic variant is comma-span based: exclude units
with alphabetic letters; split spans only at `、`; treat empty-reading
kanji-like content as unsafe; exempt kana-only or symbol-only adjacent
boundaries; and require other adjacent entry boundaries to be supported by the
later entry starting at `piece_orders[0] >= 2`. Early measurements should be
reported both by span count and by character coverage, and kana-only spans
should not be counted as N-gram added value.

The stable two-kanji variant is also diagnostic for now. It can mark a hybrid
token as locally forgiven only when that same token is a two-kanji compound
with exactly one reading in the raw SudachiDict surface-to-reading inventory.
This is intended to handle common compounds whose absence from the N-gram path
is probably vocabulary sparsity, while avoiding safety claims based on
decoder-only over-splitting or compiled-dictionary candidate exposure.

Decoder overrides and mechanical safety are deliberately different judgments.
If a decoder reading differs from Sudachi and has repeated 2-gram support, it
can be used as a normal tentative correction signal even if many such changes
later turn out to be wrong. Review remains the default, and Sudachi/decoder
disagreement is not a safe-skip condition. Safe auto-acceptance should require
Sudachi and decoder to agree, with the whole relevant span supported from start
to end by repeated 2-gram evidence.

This keeps both cost and failure modes under control.

Current LLM yomi direction:

- use a compact raw-text scope triage (`Keep`/`Skip`/`Exclude`) before reading
  work: `Skip` is recoverable non-target material, while `Exclude` is a
  provisional recommendation for sensitive material that should disappear
  permanently after explicit human confirmation
- do not ask the first LLM pass to certify yomi correctness as
  `OK/Review/Skip`
- ask the LLM for independent readings of marked kanji/Latin targets in
  furigana-style context
- compare those readings with the stored Sudachi/hybrid readings
- route LLM/mechanical agreement to bulk audit or future auto-acceptance
  experiments, and route disagreement or parse failure to focused review

This makes the LLM a second reading source rather than a black-box correctness
classifier. It also preserves diagnostic metadata: N-gram support, stable
two-kanji safety, LLM agreement, and human approval remain separate signals.
The older `OK/Review/Skip` and `OK/Fix/Ambiguous/Skip` triage experiments remain
available through Git history, not as active prompts or implementation targets.

### 3.4 Use two judgment granularities

Sentence-level judgments should handle:

- non-target material
- whether the current mechanical yomi is correct with high confidence

The minor-alphabetic problem should instead be handled at the batch entity-type
level:

- run lightweight Sudachi analysis over the batch and extract alphabetic entity
  occurrences from its token boundaries; do not run the N-gram decoder here
- treat source whitespace as a hard boundary even for a space-spanning Sudachi
  dictionary token
- aggregate them into entity types
- resolve entity types through whitelist/blacklist lookup first
- send only unresolved entity types to the LLM once, then cache the judgment
  globally
- use the effective entity status immediately for provisional unit-level skip
- let final human review override provisional skip with the same `Skip`
  checkbox used for ordinary skip decisions
- keep the judgment source for audit/debug, but make behavior depend only on
  effective `in_scope`, `out_of_scope`, or `unknown`

This matters because the alphabetic long tail is primarily a repeated entity
problem, not a repeated sentence problem.

### 3.5 Use Python for the main pipeline, not shell wrappers

The existing wrapper at `/home/hmanabe/scripts/sudachi` currently resolves to:

- `sudachipy tokenize -r "$HOME/.config/sudachi/sudachi.json"`

The yomi pipeline currently invokes that wrapper with `-a -m B -s full`, so the
Sudachi baseline comes from Sudachi B-mode with `sudachidict_full`. The full
dictionary fixes useful cases such as `断捨離/ダンシャリ`, `主演/シュエン`, and
some named entities, but it is still only a candidate source. Readings that do
not pass our yomi-format validation, such as alphabetic lowercase readings, must
not be accepted merely because Sudachi full produced them. Tokens that span
spaces are split at source-space boundaries by project policy.

An internal Japanese middle dot (`・`, or half-width `･`) is likewise a hard
output boundary for every Sudachi POS, including proper names and kana-only
names such as `ラ・カンパネラ`. Preserve the separator as punctuation and look
up each lexical component independently with Sudachi. Parentheses follow the
same structural rule;
known abbreviations such as `（株）`, `（有）`, `（社）`, and `（財）` use their
short spoken readings. If independently looked-up component readings conflict
with the full token reading, leave the affected lexical components unresolved
for the normal LLM-reading and review path rather than inventing a reading.
Other punctuation inside proper-name tokens remains untouched because it can be
part of an established spelling and reading, as in `ZE:A`, `M&A`, or
`COVID-19`. Attached `〜` or `～` in a non-proper-name lexical token expresses
vowel lengthening, so forms such as
`な〜` and `う～ん` keep their surfaces but use `ナー` and `ウーン`. Standalone
wave marks and numeric ranges remain literal separators.

Alphabetic scope analysis uses the same configured Sudachi mode and dictionary
as its lightweight boundary source, but does not invoke `yomi-decoder`. It sends
all unit lines through one Sudachi subprocess per batch, persists the resulting
alphabetic occurrences, and reuses those occurrences when cached LLM decisions
are projected. This avoids both per-sentence process startup and later boundary
drift from re-tokenizing with another implementation.

The wrapper is useful as the source of truth for configuration, but the
production pipeline should eventually call SudachiPy from Python and point it at
the same config path and dictionary type. That avoids shelling out per sentence
and makes metadata capture easier.


## 4. Proposed Repository Layout

Large generated artifacts should be untracked.

Suggested layout:

```text
yomi-corpus/
  README.md
  docs/
    PIPELINE_DESIGN.md
    WORKING_PIPELINE.md
  config/
    datasets/
    pipelines/
    model_profiles/
    prompts/
    regex/
  scripts/
    import_corpus.py
    run_stage.py
    build_review_queue.py
    promote_core_corpus.py
  src/
    yomi_corpus/
      io/
      ingest/
      units/
      tokenizer/
      decoder/
      filters/
      scoring/
      repair/
      llm/
      review/
      export/
  data/
    imports/
    units/
    review_queues/
    exports/
  runs/
    20260402_example/
      manifest.json
      metrics.json
      logs/
  scratch/
```

Recommended meaning:

- `config/`: tracked small inputs and prompt templates
- `src/`: reusable code
- `scripts/`: thin CLI entry points
- `data/`: materialized stage outputs, with analysis stored inside unit records
- `runs/`: per-run manifests, metrics, logs, temporary API payloads
- `scratch/`: ad hoc inspection files

For the alphabetic subsystem specifically, distinguish:

- batch-local artifacts under `data/units/...`
- cross-batch state under `data/state/alphabetic/`


## 5. Stage Model

Each stage should write a new artifact set instead of mutating prior outputs.

Suggested stages:

### S00 Import

Input:

- `*_kept.jsonl.gz` from `llm-jp-corpus-v4`

Output:

- imported document JSONL with stable `doc_id`

Responsibilities:

- assign stable IDs
- preserve original JSON record
- normalize source metadata names

### S10 Unit Extraction

Output:

- sentence or line units with stable offsets

Responsibilities:

- split documents into units
- keep unit-to-document mapping
- preserve sequential order inside each document

### S20 Mechanical Analysis

Output:

- unit records enriched with mechanical analysis
- batch-level alphabetic entity inventory

Responsibilities:

- generate mechanical yomi
- add a conservative yomi auto-accept flag for units that do not need review
- extract alphabetic entity occurrences and aggregate entity types
- use persisted lightweight Sudachi-derived boundaries for those occurrences;
  never rerun an independent regex tokenizer during decision promotion
- resolve number-plus-recognized-measurement-unit entities deterministically,
  while retaining them in the occurrence artifacts for audit
- keep numeric values separate and readingless before `kg` and `km`; default
  those unit tokens to `キロ`, while retaining their formal expansions as
  review alternatives
- attach a `certain` flag for sentence-level tasks

Example signals:

- old kana or historical orthography
- heavy classical auxiliary patterns
- kanbun markers or citation style
- abnormal script mixture
- very high rare-character ratio
- Sudachi analysis quality
- `yomi-decoder` agreement or failure signals

The first yomi auto-accept rule is intentionally narrow. A unit is accepted
only when it has generated yomi, has no unresolved non-numeric readings,
Sudachi and the decoder agree on the rendered output, and the decoder top
candidate has full repeated N-gram support. The stable two-kanji relaxation is
an optional auto-accept profile, not a hardcoded `dev` behavior. That relaxation
is still conservative because Sudachi/decoder agreement remains mandatory and
ambiguous raw SudachiDict readings are rejected. Grouped numeric runs such as
`2021/` are allowed because number pronunciation is outside the current yomi
task.

### S30 LLM Scope Gate and Reading Signals

Output:

- unit records enriched with three-way scope judgments for target, recoverable
  non-target, and terminally excluded text
- per-target LLM reading results for yomi targets that were not suppressed by
  deterministic confidence rules

Responsibilities:

- run scope triage on raw text before mechanical yomi generation
- mechanically keep non-lexical units containing no letters or numbers (for
  example a standalone `！` or emoji) without sending them to the LLM; Latin or
  other alphabetic text still follows the normal alphabetic/scope checks
- return exactly one scope label: `Keep`, `Skip`, or `Exclude`
- treat `Skip` as non-target material such as foreign-language text, old
  Japanese prose, kanbun, Chinese, spam, obvious nonstandard OCR corruption, or
  flattened ruby interleaved with source characters; isolated ordinary typos
  remain in scope
- treat `Exclude` as a conservative privacy/reputational-risk recommendation
  when a unit
  identifies a private person together with sensitive negative information such
  as arrest, criminal suspicion, accusations, scandal, disciplinary action, or
  illness
- build a yomi-reading queue from unresolved kanji/Latin targets in the
  Sudachi/hybrid token stream
- ask the LLM for the reading of exactly one marked target per request
- compare the LLM reading with the stored mechanical reading

The default scope-triage output should be a single token, not JSON. Reasons
belong in debug/eval mode because ordinary production runs should minimize
expensive model output. Scope triage should normally use the `economy` profile
unless evals show that a stronger model materially reduces dangerous `Keep` or
`Skip` errors.

The default LLM reading output should be a one-key JSON object such as
`{"話":"はな"}`. The current default request context is plain source text with
one target marked by `**...**`, using the ultra-short pattern prompt
`目が**痛**い。->{"痛":"いた"}\n{marked_text}->`. The model should return only
the marked target's reading and must not rewrite the whole sentence.

The materialized LLM stage writes separate artifacts:

- raw LLM results, preserving raw text, parsed status, parse errors, usage, and
  metadata for audit and cost reporting
- scope-gated unit artifacts, where every unit has a parsed
  `Keep`/`Skip`/`Exclude`
  result or a safe fallback
- yomi-reading queue/result/apply artifacts, where each requested target has an
  LLM reading, comparison status, and usage metadata

Scope parse errors must be treated conservatively so malformed model output
cannot silently skip or accept a unit. Yomi-reading parse errors must not become
agreement. Because yomi-reading requests ask for a tiny one-key JSON object,
format/key parse errors should first be retried once with a stricter prompt:
`Return exactly one JSON object with key "{surface}"; no explanation.` Retry
results override first-pass results for the same item ID. Any remaining parse
error after retry routes the target or unit to focused review.

Scope triage is intentionally ordered before reading work because it only needs
raw unit text, but its result does not branch the reading pipeline. `Keep`,
`Skip`, and `Exclude` all continue through the same deterministic safety checks,
Sudachi/hybrid generation, and unresolved-target LLM reading calls. Scope status
changes review presentation and eventual corpus inclusion only. This keeps the
stored yomi schema identical, avoids skip-specific reconstruction bugs, and
preserves useful readings when a provisional decision is reversed. `Exclude`
remains provisional until Bulk Review; the model alone must never make text
disappear.

In this pipeline, agreement is operational rather than absolute. LLM/mechanical
agreement does not mean "guaranteed correct forever." It means the target or
unit may be low-risk enough for high-throughput audit or future auto-acceptance
experiments. The source of every low-risk signal must remain visible, for
example N-gram support, stable two-kanji support, LLM reading agreement, or
human approval, so later audits can measure false-accept rates separately by
source.

#### Per-target safety evidence

Whole-unit auto-accept is useful as a conservative experiment, but the more
scalable design is per-target evidence. Each yomi-bearing target in the
Sudachi/hybrid token stream should get a safety record. The LLM is queried only
for targets that deterministic evidence cannot make low-risk. Human review then
sees unresolved targets highlighted inside the unit instead of receiving only a
coarse sentence-level `OK` or `Review`.

Candidate safety signals:

- stable dictionary evidence: the target has one trusted dictionary reading,
  such as a stable two-kanji raw SudachiDict item, and the current reading
  matches it
- corpus-frequency evidence: a trusted decoder/training-corpus stats artifact
  shows that one reading dominates the exact full token surface, or the target
  surface as a fallback, with >=95% share and a minimum count threshold
- repeated N-gram evidence: the local reading is supported by repeated N-gram
  support, not a one-off transition
- LLM agreement: an independent per-target reading query returns the same
  reading as the mechanical reading
- no-ruby laughter marker: a standalone lower-case `w`/`ｗ` run such as `ｗ` or
  `ww` is treated as an internet laughter marker, not as the letter name; it is
  low-risk with `No ruby` as the preferred candidate
- unit auto-accept evidence: a legacy whole-unit auto-accept decision can mark
  every target in that unit low-risk, but it should remain a distinct signal so
  later audits can separate it from per-target evidence
- unresolved evidence: no signal applies, the LLM disagrees, or the LLM output
  is missing/malformed

The unit-level status is derived from target-level evidence. If scope triage
returns `Skip`, the entire unit is excluded. Otherwise, a unit with no
unresolved targets can enter bulk review or later auto-accept experiments. A
unit with unresolved targets stays reviewable, and the UI should highlight the
specific targets. Highlight intensity can reflect evidence strength: a
dictionary-plus-corpus target can be visually quiet, while LLM-only agreement
or unresolved targets can be more visible.

These safety records are risk labels, not truth labels. Store the full evidence
source and parameters: signal name, counts, threshold, N-gram order/count,
dictionary source, LLM model/profile, and artifact versions. This lets later
audits measure which evidence source caused false accepts and retune thresholds
without losing provenance.

The `w`/`ｗ` exception must stay narrow. It applies only to standalone
lower-case runs used as laughter markers. It should not apply to uppercase `W`,
to embedded alphabetic strings, or to lexicalized/product-name cases such as
`W主演`, `W杯`, `Wii`, `Web`, or `WiFi`; those remain normal alphabetic/yomi
targets.

Corpus-frequency evidence is probabilistic and corpus-dependent. A surface such
as `大麻` may be overwhelmingly observed as `タイマ` in the evidence corpus, while
a rare place-name reading such as `おおあさ` remains possible. That is an
accepted residual risk for bulk review/de-emphasis: the signal says "low-risk
under this corpus and policy," not "lexically impossible to read otherwise." If
audits show repeated misses of rare proper-noun readings, add targeted
exceptions or lower the confidence/highlight level for those surfaces.

Corpus-frequency lookup should use the exact full token before the extracted
ruby target. For example, the evidence pair `思っ/オモッ` can make the contained
target `思/オモ` low-risk. If exact full-token evidence does not qualify, use
a separate trailing-kana stem namespace before falling back to target-level
evidence. Remove the complete trailing hiragana run from the surface and its
exact katakana equivalent from the reading, so `思い知る/オモイシル` and
`思い知っ/オモイシッ` share `思い知/オモイシ`. Do not combine normalized
families with naturally kana-less surfaces. The safety signal records whether
`token`, `trailing_kana_stem`, or `target` evidence was used, together with the
evidence surface, reading, and normalization rule.

This is target-reading normalization, not linguistic lemmatization. It may
legitimately group derivational or lexical forms such as `赤かぶ/アカカブ` under
`赤/アカ`, because the same deterministic transform is applied to corpus
evidence and the current candidate. Competing reading stems still share the
family denominator and must pass the normal minimum-count and 95% dominance
requirements. Thus families such as `行`, `入`, `食`, and `勝` remain unresolved
when their observed reading stems are materially split.

Corpus-frequency statistics must be versioned with the decoder model rather
than remaining a global artifact built only from the original corpus. Every
decoder refresh should build its N-gram model, lexicon, and surface/reading
frequency table from the same ordered corpus set: the configured base corpus
plus all finalized batches selected for that track refresh. Store the frequency
TSV and its source manifest inside the immutable decoder model directory.

When a batch is prepared, it pins a decoder model directory. Mechanical yomi
generation and pre-LLM corpus-frequency safety must both use artifacts from that
same pinned directory. A later decoder refresh must not change the evidence for
an already prepared batch. The original global frequency artifact remains only
a compatibility fallback for batches that predate model-bundled statistics.
Safety records should retain the model-specific frequency artifact path and
corpus version so later audits can reproduce the decision.

Concrete per-target safety records should be versioned data, not just UI state.
A passing corpus-frequency record:

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

A failing corpus-frequency record should still keep the measured signal:

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

The `方` example is a useful negative control: the schema can record the corpus
counts, but policy must not mark it safe because the surface has genuinely split
readings and the dominant share is far below 95%.

Minimum fields:

- alignment: `target_id`, `surface`, `token_surface`, `target_start`,
  `target_end`, `token_index`, `chunk_index`
- mechanical reading: `mechanical_reading`,
  `mechanical_reading_hiragana`
- summary: `is_safe`, `review_status`, `highlight_level`,
  `accepted_signal_names`, `status_reason`
- evidence list: `signals[]`, with per-signal counts, thresholds, artifact
  versions, N-gram details, dictionary source, or LLM model/profile

Initial controlled values:

- `review_status`: `safe`, `unresolved`, `skipped`
- `highlight_level`: `none`, `weak`, `strong`

Unit-level fields such as `all_targets_safe`, `unresolved_target_count`,
`safe_signal_counts`, and `safety_policy_version` should be derived from the
target records. Do not collapse evidence into one irreversible boolean.
`status_reason` should stay a short derived explanation, for example
`accepted_by_corpus_frequency`, `accepted_by_llm_agreement`,
`no_accepted_safety_signal`, `llm_disagreement`, or `llm_parse_error`.

Implementation sequence:

1. Generate/load corpus-frequency stats from the exact corpus set used by each
   decoder model: the configured base corpus plus the finalized reviewed corpora
   included by that refresh. The generator writes both stats and a manifest in
   the decoder model directory; tests use small committed fixtures. The static
   stats configured for the original base corpus are a legacy fallback only.
2. Add a yomi safety module that builds per-target records using the same
   target IDs and alignment logic as LLM reading queue items.
3. Add deterministic signals first: stable dictionary and corpus frequency.
   Add N-gram target safety after the decoder-entry-to-target mapping is
   auditable.
4. Change yomi-reading queue construction to queue only unresolved, non-skipped
   targets from the pre-LLM safety artifact. The comparison baseline is the
   current hybrid rendered reading when the rendered token stream aligns with
   Sudachi tokens; raw Sudachi readings are only a fallback. This avoids false
   LLM mismatches such as Sudachi `方/ほう` versus hybrid `方/かた`.
5. Merge LLM results back into final safety records. LLM/hybrid agreement adds
   `safe_by_llm_match`; disagreement or malformed output leaves the target
   unresolved. For an unresolved disagreement with a valid LLM reading, final
   review should default to the LLM candidate while visibly keeping the target
   reviewable. Legacy whole-unit auto-accept decisions should be projected into
   target records as `safe_by_unit_auto_accept`, not left only as a unit-level
   flag.
6. Produce explicit artifacts and summaries:
   - `units.yomi.safety_pre_llm.jsonl`
   - `yomi_reading_input.jsonl`
   - `units.yomi.llm_readings.jsonl`, containing the merged post-LLM target
     safety records until a separate final safety artifact is needed
   - counts by safety signal, queued LLM targets, LLM agreement/disagreement,
     parse errors, and unresolved targets
7. Add review/debug export that highlights targets by `highlight_level`.
8. Keep the old whole-unit auto-accept path as legacy/debug until per-target
   safety has enough validation to replace it.

#### Corpus-frequency evidence interface

Corpus-frequency evidence should be consumed through a stable stats artifact,
not by reading decoder-internal runtime files directly during the main
pipeline. That artifact may be generated by the decoder, or generated inside
this project from a configured source/training corpus. For current
experimentation, generating the stats here is often preferable because the
normalization, filtering, and threshold policy can be aligned with this
pipeline and changed without requiring a decoder-side export.

A suitable stats artifact could be TSV or JSONL with one row per
`(surface, reading)` pair:

```text
surface  reading  count  surface_total_count  share  source_corpus_version
```

Optional fields such as `exported_at`, `source_corpus_path_or_id`,
`decoder_version`, and `normalization_version` are useful for reproducibility.
This project should load that artifact through a small evidence loader, cache
it for a pipeline run, and answer questions like: "Does this surface have a
dominant trusted reading above the configured threshold, and does the current
reading match it?"

If this project generates the stats, keep the source corpus path configurable
and allow the full corpus to live outside git. The source-to-stats command
should be deterministic and should write both the stats artifact and a manifest
recording the input corpus path or ID, corpus checksum when feasible,
normalization settings, filters, script version, and generation time.

Do not treat high share alone as sufficient. The rule should require both a
share threshold and a minimum count, because `1/1 = 100%` is weak evidence. The
normalization policy must also be explicit: old/new forms, Latin width, kana,
symbols, and `々` handling need to match or be recorded.

The initial default is `min_count = 5` and `min_share = 0.95`. At counts below
20 the share threshold still requires unanimity; at larger counts it permits a
small observed minority reading. Count-5 boundary
samples looked acceptable for this signal's intended role: de-emphasizing
low-risk targets while preserving auditability and bulk review visibility.

An experiment over 1,303,044 human-read source tokens produced 3,791
trailing-kana stem keys; 1,542 combined multiple surface forms. Of 1,297 keys
with at least five observations, 1,057 met the 95% threshold. Applied to the
finalized dev units, this evidence newly accepted 200 of 8,599 target
occurrences that exact-token statistics did not accept. Most gains were sparse
forms such as `学べる/マナベル -> 学/マナ` and `嬉しかっ/ウレシカッ ->
嬉/ウレ`; it also raised noisy exact forms such as `作り/ツクリ` from
93.2% exact-form share to 98.3% for `作/ツク`.

Changing the source/training corpus changes safety decisions. Every pipeline
output using corpus-frequency evidence must record the evidence artifact path
or ID, `source_corpus_version`, threshold, and minimum count. Full corpora and
large evidence files may live outside git if they are large; tests should use
small committed fixture corpora and fixture stats that exercise the same
generator and loader.

#### Resumable LLM execution

LLM execution should be handled by a generic resumable job layer rather than by
adding separate queued/submitted/completed pipeline stages for each LLM-using
task. The domain pipeline stage starts or resumes an LLM job; the LLM job owns
request files, result files, remote job IDs, progress, retry state, and logs.

The same job interface should support sync, OpenAI Responses background mode,
and OpenAI Batch mode:

- `sync`: process rows sequentially or with small concurrency, append each
  completed result, and skip already completed item IDs when resumed
- `background`: submit one Responses API background request per item, persist
  each response ID, poll response objects until completion or interruption by
  default, append completed results as they become available, and avoid
  resubmitting item IDs that already have a response ID or parsed result
- `batch`: submit the remaining rows as one or more remote batch jobs, persist
  remote batch IDs, poll at a slow interval until completion or interruption,
  fetch result files when complete, and resume from stored chunk state on later
  runs

All modes should report progress as completed items over total items. Sync mode
can update progress after each response. Background mode should poll stored
Responses API response IDs; completed responses can be parsed and appended
immediately. Batch mode should poll each remote OpenAI Batch object and use its
`request_counts` (`completed`, `failed`, `total`) as the progress source while
the server-side job is running. Batch output files are still available only
after completion, so final result parsing must continue to use downloaded output
files and each request's `custom_id`.

The domain stage should complete only after the job has produced a complete
result JSONL and those results have been applied to the domain artifact. For
scope triage or yomi reading generation, that means the job can be running
while the domain step is still active, and the stage advances only after the
scope artifact or yomi-reading comparison artifact is written.

Interruptions should be normal. The operator may stop sync mode partway through;
rerunning `./next` resumes from result rows already present. For background
mode, `./next` submits any missing Responses background requests, polls stored
response IDs roughly once per minute by default, and applies results once all
responses have completed. For batch mode, `./next` submits any missing remote
chunks, polls roughly once per minute by default, reports aggregate
`request_counts` when available, and applies results once all chunks have
completed and been fetched. If either remote mode is interrupted, rerunning
`./next` continues from stored local job state.

OpenAI API constraints that affect this design:

- Responses background mode stores response data only for a limited polling
  window, documented as roughly 10 minutes. It is useful for medium interactive
  runs such as about 150 requests, but local polling should not be postponed
  indefinitely.
- A single Batch API input file may contain up to 50,000 requests and may be up
  to 200 MB.
- Batch API also has a per-model enqueued prompt-token limit. Pending batch
  input tokens count against that queue limit until the batch completes.
- Batch creation is rate-limited, documented as up to 2,000 batches per hour.
- Batch jobs can expire if they do not complete within the completion window;
  completed requests remain available and unfinished requests appear as errors.

Current implementation: batch mode keeps one logical pipeline LLM job, but may
split requests into multiple remote OpenAI batch jobs. The task-config knob is
`batch_max_requests_per_batch`; the default preserves single-batch behavior up
to OpenAI's documented request limit, while tests and smoke runs can set a small
value to exercise multi-batch behavior. A future size-based guard such as
`max_input_file_mb` may still be useful before very large production runs.

#### Sentence vs comma-span operating modes

The canonical corpus unit remains the sentence-like unit produced from the
source document. The pipeline may nevertheless run yomi triage and repair on a
derived work-item layer. Two modes should be supported:

- `sentence`: the current sentence-like unit is the triage and repair work item
- `comma_span`: each sentence-like unit is split at Japanese commas (`、`), and
  each span becomes a triage/repair work item

The batch manifest or yomi config should record the selected mode explicitly in
`yomi_policy.unit_mode`. Track names should provide defaults, not hardcoded
behavior. `sentence` should remain the conservative default for `working` until
span-mode export and repair are well tested. `comma_span` is a useful `dev`
default candidate because it can increase automatic `OK` coverage and reduce
later review volume, at the cost of more LLM calls and more reconstruction
logic.

Yomi-specific policy should record only yomi structural decisions:

```json
{
  "unit_mode": "sentence",
  "auto_accept_profile": "strict"
}
```

LLM configuration is cross-cutting and should be stored separately as two
task-to-setting maps:

- `llm_policy`: which model profile each task uses
- `llm_execution_policy`: whether each task runs through sync calls, Responses
  background mode, or OpenAI Batch

```json
{
  "alphabetic_entity_judge": "standard",
  "scope_triage": "economy",
  "yomi_reading": "standard",
  "yomi_repair": "standard",
  "yomi_rescue": "strong"
}
```

```json
{
  "alphabetic_entity_judge": "background",
  "scope_triage": "background",
  "yomi_reading": "background",
  "yomi_repair": "background",
  "yomi_rescue": "background"
}
```

Initial allowed values:

- `unit_mode`: `sentence`, `comma_span`
- `auto_accept_profile`: `off`, `strict`, `stable_two_kanji`
- LLM profiles: `smoke`, `economy`, `standard`, `strong`
- LLM execution modes: `sync`, `background`, `batch`

Suggested yomi defaults are `working={unit_mode=sentence,
auto_accept_profile=strict}` and
`dev={unit_mode=sentence,auto_accept_profile=stable_two_kanji}` for now. Track
defaults should also choose an LLM profile per task. Operators should be able to
override these per batch, so dev can run with no auto-accept, working can later
run with stable two-kanji auto-accept, and either track can use cheaper or
stronger model profiles for specific tasks. `yomi_reading` should default to
`standard` even on dev, because mini-model reading errors create noisy false
problems and can distort prompt/pipeline design. Execution mode should be
equally configurable per task. `background` should be the normal default for both
`dev` and `working`, because it avoids slow sequential calls while still
allowing `./next` polling/resume. Prompt exploration, smoke tests, and tiny
repair batches are often easier in `sync`; very large low-urgency tasks with
acceptable latency are better in `batch`. The selected explicit values must be
stored with the batch for reproducibility.

These defaults should not remain hidden Python constants. They should be moved
to a small source-controlled project config, for example:

```toml
[tracks.working.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "strict"

[tracks.working.llm_policy]
alphabetic_entity_judge = "standard"
scope_triage = "economy"
yomi_reading = "standard"
yomi_repair = "standard"
yomi_rescue = "strong"

[tracks.working.llm_execution_policy]
alphabetic_entity_judge = "background"
scope_triage = "background"
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"

[tracks.dev.yomi_policy]
unit_mode = "sentence"
auto_accept_profile = "stable_two_kanji"

[tracks.dev.llm_policy]
alphabetic_entity_judge = "economy"
scope_triage = "economy"
yomi_reading = "standard"
yomi_repair = "economy"
yomi_rescue = "standard"

[tracks.dev.llm_execution_policy]
alphabetic_entity_judge = "background"
scope_triage = "background"
yomi_reading = "background"
yomi_repair = "background"
yomi_rescue = "background"
```

The prepare-time precedence should stay deliberately shallow:

- explicit CLI override when preparing a batch
- configured track default

After preparation, all later pipeline stages and reruns should use the stored
resolved batch policy rather than re-reading current track defaults.

There should not be a broad global override layer until repeated operational
pain justifies it.

In `comma_span` mode, span artifacts should preserve enough parent information
to reconstruct sentence-level output:

- `span_id`
- parent `unit_id`
- `span_seq`
- span text and rendered yomi
- character offsets within the parent sentence when practical
- rendered pair indexes or another stable replacement range when practical
- optional context fields such as previous span, next span, and full parent
  sentence

Triage aggregation from spans to the parent sentence is monotonic:

- if any span is `Skip`, the parent sentence is `Skip`
- else if any span is `Review`, the parent sentence is `Review`
- else the parent sentence is `OK`

`Skip` is sentence-destructive. If any span in a parent sentence is `Skip`, the
whole parent sentence is excluded from later yomi repair and final export. Other
spans in that same sentence, even if labeled `OK` or `Review`, are retained only
as audit metadata and are not repaired.

`Review` remains local in span mode. If no sibling span is `Skip`, only
`Review` spans proceed to repair. The repair prompt should target the span, but
may receive context such as neighboring spans, the full parent sentence,
previous/next sentences, or source metadata when needed. The final exported
artifact must still be sentence-level: repaired spans are merged back into the
parent sentence, `OK` spans are preserved, and span-level decisions remain
available under audit metadata.

### S40 Yomi Repair

Output:

- corrected yomi where the prior stage did not accept the mechanical yomi

Responsibilities:

- apply deterministic repair where possible
- use an LLM repair prompt only for units labeled `Review` by yomi triage
- never send knowingly bad yomi directly to the first human-review UI

Useful features:

- tokenization agreement
- reading agreement
- OOV count
- kanji token with empty reading
- punctuation-only differences
- decoder piece-crossing behavior
- suspicious token length
- regex-matchable patterns

LLM-facing yomi text may be more natural than the stored rendered annotation.
The stored `surface/reading` token sequence remains the source of truth for
storage, auditing, deterministic replacement, and N-gram feedback, but prompts
and human review UIs may use derived display forms.

The preferred display candidate is no-space furigana-style text. It should
render token readings as inline parenthesized kana and then concatenate tokens
without token spaces, for example:

```text
荷物（にもつ）を送（おく）って結果（けっか）を待（ま）ちます。
```

This is the most familiar Japanese reading-annotation form and should be easier
for both humans and LLMs to judge than whitespace-separated token pairs. It also
hides irrelevant segmentation artifacts such as katakana splits (`ラミン トン`)
when segmentation repair is out of scope for the current task.

Raw corpus text often contains both full-width and half-width parentheses, so
LLM-facing no-space furigana must reserve full-width `（...）` for yomi
annotations only. Before rendering yomi for an LLM prompt, escape original
source parentheses as a reversible display-layer transform:

- source `（` -> `-LRB-`
- source `）` -> `-RRB-`
- source `(` -> `-lrb-`
- source `)` -> `-rrb-`

For example, raw `荷物を送って、（明日）届く。` may be displayed as
`荷物（にもつ）を送（おく）って、-LRB-明日-RRB-届（とど）く。`.
The stored source text and canonical token sequence remain unescaped. Escapes
are applied only to human/LLM display text and should be restored or validated
before any model proposal is applied.

No-space display should also preserve the distinction between ordinary numeric
tokens and rare fused numeric yomi tokens. If a digit-containing surface is one
canonical yomi-bearing token, prefix the displayed token with `|`. For example,
canonical `1人/ヒトリ` displays as `|1人（ひとり）`, while ordinary separated
tokens `1/ 人/ニン` display as `1人（にん）`. The marker is not source text; it
only tells the LLM that the digit participates in the annotated token.

The furigana renderer should be dictionary-backed when possible. The Sudachi
derived CSV can map `(surface, reading)` to annotated surfaces such as
`読（よ）み仮名（がな）` or `送（おく）っ`; this avoids heuristic reconstruction
for many inflected forms. If no unique dictionary-backed form exists, the
renderer can fall back to a simple token-level representation or keep the
stored `surface/reading` pair for that token.

This projection is deliberately separate from the accepted corpus format. The
canonical data remains `surface/reading` tokens; furigana text is a derived UI,
LLM, or export view. After a document/unit is accepted, persist any
non-dictionary or inferred furigana projection that the reviewer effectively
accepted. The metadata should include the surface, normalized reading, accepted
annotated form, converter method/confidence, source such as
`human_accepted_review_ui`, dictionary version, and batch/unit IDs. Future UI
rendering can prefer this accepted projection cache before falling back to the
base dictionary/scored converter, and repeated accepted projections can later be
promoted into the annotated-form dictionary. The cache is evidence and display
support, not a replacement for canonical `surface/reading`.

Whitespace-separated views still have a role:

- full token view: `送っ/オクッ て/テ`
- compact token view: `送っ/オクッ て`
- furigana spaced debug view: `送（おく）っ て`
- furigana no-space prompt view: `送（おく）って`

Use plain marked source text for per-target LLM reading generation by default:
only the requested target is marked, and the rest of the sentence is unannotated
context. A 150-item mini-model comparison showed similar match counts to
no-space furigana context with materially fewer input tokens, so the simpler
view is the default until larger experiments say otherwise.

Use no-space furigana for LLM triage/proposal and human bulk review experiments
where the existing yomi annotation itself is what the model or reviewer must
judge. Use spaced token views for debugging, alignment inspection,
deterministic repair rules, and N-gram decoder training data, where token
boundaries are useful.

Because some corrections cross token boundaries, the repair/proposal prompt
should allow local spans that include neighboring kana or symbols. For example,
`外出/ガイシュツ て` may need to become `外/ソト 出/デ て`. Proposed `from` spans
must be aligned back to the full stored annotation before application. Display
strings, including no-space furigana, are views and should not be blindly
string-replaced as canonical data. Automatic application should require one
unique valid span in the stored token sequence; ambiguous or non-matching
proposals stay in human review.

Implementation plan:

- Implement yomi display renderers as shared prompt-rendering filters, not as
  task-specific prompt text. Supported display modes should include at least
  `full`, `compact`, and `furigana_no_space`; a spaced furigana debug mode is
  also useful.
- Keep full canonical yomi token arrays in all unit artifacts. Derived display
  text is computed at prompt-build time or stored as explicit debug metadata
  only.
- Enable plain marked source text first for `yomi_reading`.
- Enable no-space furigana first for LLM judgment/proposal tasks that inspect
  existing yomi annotations: yomi triage, review-resolution/local-fix proposal,
  and possibly yomi check.
- Keep full token arrays available for prompts or tools that need exact token
  boundaries until span alignment and expansion are implemented for furigana
  display.
- Add a task-level configuration flag so experiments can compare full and
  compact/furigana display.
- For per-target reading generation (`yomi_reading`), prefer an ultra-short
  pattern prompt unless later tests show a regression:
  `目が**痛**い。->{"痛":"いた"}\n{marked_text}->`. A GPT-5.5 test on 150
  current-format items produced no parse errors and materially lower input
  token use than the older explanatory prompt. Keep target extraction strict:
  iteration-mark words such as `日々` must be queued and evaluated as the whole
  readable unit, not as `日` alone.
- When a prompt receives a derived display, it must state the display policy.
  For compact display, bare kana, symbols, and numbers are intentional
  abbreviations. For furigana display, absence of token spaces is intentional
  and should not be treated as evidence that segmentation was fixed.
- For proposal tasks, parse the model's local `from`/`to` spans as proposals.
  Align `from` against the full stored token sequence. Apply only unique valid
  spans; otherwise escalate to human review.

Before repair, the pipeline may run a lightweight router over units that first
triage labeled `Review`. This router separates operational causes:

- `Fix`: concrete yomi repair is possible from the available unit/context
- `Ambiguous`: target Japanese, but no safe correction can be chosen from the
  available context
- `OK`: first-stage triage was a false positive; the unit can move to bulk
  audit
- `Skip`: first-stage triage missed non-target text

This router is an operational step, not the conceptual gold taxonomy. Gold data
should use `OK`, `Fix`, `Ambiguous`, and `Skip`; first-stage `Review` is the
collapsed production label for both `Fix` and `Ambiguous`, plus possible
first-stage false positives.

Experiment note, 2026-05-18: direct 4-way triage was tested against a
two-stage `OK/Review/Skip -> Fix/Ambiguous/OK/Skip` route on the small yomi
triage eval set. With `gpt-5.4-mini`, the end-to-end two-stage route scored
47/60 and direct 4-way scored 36/60. With `gpt-5.5`, the two-stage route scored
49/60 and direct 4-way scored 45/60. The `gpt-5.5` router could emit
`Ambiguous` when run only on gold Review rows, but the first 3-way prompt sent
all conceptual `Ambiguous` rows to `OK`, so end-to-end recall for ambiguity was
still zero. Production should therefore keep the first triage label set at
`OK/Review/Skip`; conceptual `Ambiguous` remains useful for gold data and
later repair/proposal-stage evaluation, not as a first-triage requirement.

### S50 Human Review Pass 1

Output:

- first-pass human review annotations

Responsibilities:

- show the yomi-annotated sentence
- show two checkboxes:
  - non-target status
  - yomi fully correct
- allow the first box to be prefilled
- keep the yomi-correct box initially unchecked

Important UI rule:

- do not show the raw sentence separately in this UI; the yomi-annotated
  sentence already contains the original text

Minor alphabetic review should not be a separate routine human surface. Entity
judgments are cached and projected to provisional unit skip; the final
sentence/unit review UI is where a human restores wrongly skipped units.

### S60 Rule Harvesting

Output:

- candidate reusable rules derived from reviewed cases
- candidate human overrides for alphabetic entity types

Responsibilities:

- propose non-target triggers
- propose minor-alphabetic entity status updates when human review contradicts
  provisional skip
- keep yomi repair rules separate from classification lists

This should remain conservative and is still an open design area.

### S62 Alphabetic Entity Judgment

Output:

- cached LLM judgments for unresolved alphabetic entity types
- unit-level provisional skip reasons derived from effective entity status

Responsibilities:

- read the unresolved entity-type report produced after alphabetic extraction
- ask the LLM once per unresolved entity type, using a few short batch examples
  as context
- judge whether the entity is naturally usable in modern Japanese context or is
  obscure/foreign/noisy enough to skip
- store the answer in the cross-batch alphabetic judgment cache
- do not ask again for the same effective entity key unless the cache is
  explicitly invalidated or superseded
- mark a unit as provisional skip when any effective entity status is
  `out_of_scope`
- preserve raw model output and usage metadata for later audit

The LLM answer is provisional policy, not a final corpus decision. It may cause
a unit to start with `Skip` checked, but that unit remains visible in final
review. If the human unchecks `Skip` for a provisional alphabetic skip, the
triggering `out_of_scope` entities become effective `in_scope` entries from then
on. If the human leaves the unit skipped, or manually skips a unit that was not
provisionally skipped, entity status does not change.

The cache should preserve source metadata such as `static_whitelist`,
`static_blacklist`, `llm`, or `human_unskip`, but ordinary behavior should not
depend on the source. The effective status is just `in_scope`, `out_of_scope`,
or `unknown`.

Human-facing review labels should avoid internal stage names. In the browser,
the normal high-throughput yomi review should be called `Bulk Review`, even
though the internal stage ID remains `yomi_final_review`. The stronger local
repair confirmation pass should be called `Escalated Repair`, even though the
internal stage ID remains `yomi_strong_repair_review`. Technical logs and file
paths may keep internal names; UI copy and operator-facing docs should prefer
the human labels.

### S65 Provisional Scope Review

Output:

- Bulk Review records containing the displayed three-way scope state
- optional human overrides for alphabetic entities when provisional skip is
  restored

Responsibilities:

- show provisional alphabetic skip units greyed out with `Skip` selected
- show concise skip reasons such as the triggering entity key and cached status
- let the human select `Keep` to restore the unit
- show LLM-proposed sensitive exclusions as provisional `Exclude`; require an
  explicit confirmation before submission accepts that terminal disposition
- write effective `in_scope` overrides for triggering `out_of_scope` entities
  only when the human restores a provisional alphabetic skip
- retain every machine-skipped unit through mechanical/hybrid yomi generation
  and pack generation; only paid LLM reading work is suppressed

The sentence-level control has two compact toggle buttons: archive box for
`Skip` and shield-with-X for `Exclude`. `Keep` is the implicit state when
neither button is active. Selecting either button clears the other; selecting
the active draft button again returns to `Keep`. Tooltips and accessible names
expose the text labels. The sentence immediately adopts subdued gray or warning
red styling so the reviewer sees the draft disposition before submission.
This replaces both the unused range-selection control and the visually heavier
three-segment selector.

Asymmetric update rule:

- human keeps a provisional skip checked: no entity-level change
- human checks `Skip` on a normal unit: no entity-level change
- human selects `Keep` for a provisional alphabetic skip: triggering entities become
  effective `in_scope`
- human confirms `Exclude`: replace the unit with a content-free structural
  tombstone in finalized browsing and remove its text from every downstream
  corpus/model/search input, while retaining only the minimum private audit
  record needed for idempotent processing

Rationale:

- Bulk Review is already required for yomi quality, so skip correction should
  piggyback on the same UI
- machine skip decisions are deliberately fallible and must always be visible
  to a human before becoming final
- provisional skips reduce downstream cost without requiring a separate
  promotion-candidate review loop
- source-aware audit records remain available if the LLM cache proves noisy

#### Confirmed skip lifecycle

Machine and LLM skip decisions remain provisional until Bulk Review confirms
the displayed `Skip` state. Before submission, the reviewer may restore the
unit by unchecking it. Human confirmation changes the unit into a durable
skipped record rather than deleting it.

A confirmed skipped record retains:

- document and unit identity, sequence, source location, and raw text
- the effective skip state and whether it originated from a deterministic
  rule, LLM judgment, or human action
- machine reasons and any later human override provenance
- enough version information to reproduce which review decision was applied
- canonical hybrid yomi and ruby-rendering data, even though the unit is not
  part of the finalized corpus

It is excluded from finalized reading corpus output, decoder training data,
yomi-reading LLM inference, and Escalated Repair. Ordinary Bulk Review controls
are no longer available after confirmation. Restoring it later uses the
finalized-correction path initialized from the preserved hybrid yomi; it does
not send the unit back through Bulk Review.

Resolved browsing shows confirmed skips as subdued ruby text marked `Skipped`.
Corpus Map offers `Restore and Edit`, which emits an explicit `skip: false`
finalized-correction patch. Applying that patch atomically moves the unit from
the skipped artifact to finalized corpus data while preserving skip and
restoration history.

Corpus Map also exposes disposition changes through the same correction patch:
an ordinary finalized unit can be moved to recoverable `Skip` or terminal
`Exclude`, and a skipped unit can be restored or excluded. These draft actions
remain reversible in the browser until Issue submission. A server-confirmed
exclusion is still an immutable, non-editable tombstone.

#### Confirmed exclusion lifecycle

`Exclude` is distinct from ordinary skip. It is intended for sensitive material
that must not remain browsable or restorable through Corpus Map. Before human
confirmation it behaves like a provisional skip and retains hybrid yomi for
inspection. After confirmation it is terminal: finalized browsing retains only
a content-free tombstone such as `Removed`, in the original unit position.
Published review packs, archive shards, and Corpus Map must not contain the
original text, yomi, or analysis. Search indexes, corpus output, evaluation
exports, and decoder training omit the unit entirely.

The tombstone schema retains only stable document/unit identity, source order,
an exclusion reason category, confirming submission identity, and timestamps.
It must not contain source text, source offsets that expose content, ruby,
mechanical/LLM analysis, or free-form notes copied from the source. Tombstones
are not searchable and expose no edit/restore action. Reversal is an explicit
administrative migration, not an ordinary browser edit.

Retrospective exclusion uses the same terminal representation. A dry run first
enumerates every copy in final/skipped unit files, review packs, archive/search
shards, evaluation datasets, and downstream corpus manifests. Applying the
migration removes only explicitly enumerated units atomically, writes
content-free tombstones plus an idempotent migration manifest, and marks
pre-exclusion decoder models as superseded rather than attempting to rewrite
immutable historical model files. Document-level exclusion must not be inferred
from a few excluded units. In document 13, only the previously reviewed units
about alleged violations, arrest, or related private-person reporting are
terminally excluded; ordinary finalized units and recoverable alphabetic skips
remain available.

### S70 Expensive Yomi Recovery

Output:

- best-effort high-cost yomi for units that still failed human pass 1 on yomi

Responsibilities:

- use a maximally capable LLM setup
- allow stronger reasoning or external search if needed
- prepare units for a second, narrower human review pass

### S80 Human Review Pass 2

Output:

- second-pass human comments on expensive-recovery outputs

Responsibilities:

- show only:
  - the yomi-annotated sentence
  - a free-text comment box
- leave the comment blank when the yomi is acceptable
- describe the remaining error in natural language when it is not

### S90 Final Editable Review

Output:

- human-edited final yomi for the hardest remaining cases

Responsibilities:

- show a fully editable text box containing the whole yomi-annotated sentence
- let the reviewer directly rewrite the full output into the correct form
- run validation and normalization after the edit
- reject or flag outputs that no longer match the required format

### S100 Export and Promotion

- corpus export
- core-corpus promotion candidates for `yomi-decoder`

Responsibilities:

- export only accepted records
- produce promotion files for decoder retraining
- keep promotion explicit and reversible


## 6. Canonical Record Types

The pipeline should use a small set of stable schemas.

### 6.1 Unit record

```json
{
  "doc_id": "ja_cc_level2:0000000123",
  "unit_id": "ja_cc_level2:0000000123:u0007",
  "unit_seq": 7,
  "char_start": 418,
  "char_end": 457,
  "text": "...",
  "source_file": "data/filtered/ja_cc_level2.surface_word_kept.jsonl.gz",
  "source_line_no": 123,
  "analysis": {
    "mechanical": {
      "non_target": {
        "value": false,
        "certain": false
      },
      "minor_alphabetic_sequence": {
        "value": false,
        "certain": true
      },
      "yomi": {
        "rendered": "...",
        "certain": false
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


## 7. What Should Count as "Almost Certainly Safe"

This should eventually become a derived rule bundle, not intuition.

Current implementation policy:

- auto-accept only when Sudachi and the decoder agree and the decoder candidate
  has full repeated N-gram support
- do not define a sentence-level `certain=true` rule for non-target judgment
  yet
- collect the raw mechanical features needed to learn those rules later

The point is to avoid inventing confidence rules before reviewed data exists.


## 8. Rule and Repair Strategy

The current design distinguishes between classification lists and yomi repair
rules.

For classification:

- prefer simple whitelist or blacklist entries for Latin/alphanumeric entity
  types that are either in scope for modern Japanese text or out of scope
- match those entity-list entries case-insensitively by default
- keep exact-case exceptions for short tokens and acronyms
- treat pure uppercase ASCII initialisms of one to three letters as
  deterministic in-scope exceptions rather than whitelist entries; normalize
  full-width forms first, extract them for auditability, and do not send them to
  the alphabetic LLM judge. Normal reading assignment and review still apply.
  Mixed forms such as `GI9`, `ZE:A`, and `2nd` do not receive this exemption.
- judge those entities primarily at the entity-type level, not the sentence
  level
- remain cautious about rule harvesting for non-target status

For yomi repair:

- regex-like deterministic transforms are still plausible
- LLM repair remains the fallback when deterministic repair is insufficient


## 9. OpenAI Layer

## 9.1 API choice

Use the Responses API as the canonical interface.

Keep two operating modes:

- synchronous mode for prompt iteration
- Batch API mode for production throughput

Both modes should use the same prompt builder and parser.

Prompt exploration should prefer synchronous Responses API calls even when the
eventual production path uses batches. The exploration loop needs immediate raw
outputs, parsed labels, usage, and failure reports so prompts can be revised
quickly. Batch mode should enter after candidate prompts are already stable
enough for larger regression checks or production throughput.

## 9.2 Model configuration

Do not hardcode model choice deep in the pipeline, but the project should still
have an explicit default policy.

Recommended default policy:

- use `gpt-5.6-sol` as the normal model for real annotation work
- use `gpt-5.5-pro` only as a last-resort rescue model for a very small tail
- use `gpt-5.4-nano` only for plumbing checks, transport tests, and cache/token
  instrumentation
- do not assume `gpt-5.4-mini` is the normal cost-saving path unless task-level
  evals show that the quality tradeoff is actually worth it

Model selection should be expressed through named capability/cost profiles, not
scattered model strings in pipeline branches. The batch should store
`llm_policy`, and the runner should resolve each task's profile into the task
config overrides used for the actual API call.

Initial profile meanings:

- `smoke`: plumbing-only API checks, normally `gpt-5.4-nano`
- `economy`: cheaper interactive/dev pipeline checks, normally `gpt-5.4-mini`
- `standard`: production-quality judgment/repair, normally `gpt-5.6-sol`
- `strong`: exceptional expensive rescue settings, normally `gpt-5.5-pro` or
  web-search-enabled repair/check tasks

Track defaults should choose profiles per LLM task, not one profile for the
whole batch. A dev batch may use `standard` for realistic dry runs, and a
working batch may use `smoke` only for explicit plumbing checks before real
annotation. The resolved profile should be recorded in batch artifacts together
with the actual model and reasoning effort so later cost and accuracy audits are
unambiguous.

Cost audits must also preserve actual built-in tool calls. Normalized result
rows record counts by Responses API output type, especially
`web_search_call`. Usage summaries price model tokens and tool calls separately
and expose both components as well as their combined total. Tool availability
alone must never be counted as tool use.

Scope triage is an intentional exception to "working means standard model".
Use the `economy` profile for scope triage on both `dev` and `working` by
default. Its job is to provide a recoverable scope/skip signal, not to certify
yomi quality, and `dev_batch_0002` showed that promoting it to `gpt-5.5` would
be a material cost increase without directly improving the final readings.

The mapping from track/task to default LLM profile should come from the same
source-controlled defaults config as `unit_mode` and `auto_accept_profile`.
Profile definitions live in the LLM profile config, while the prepared batch
stores the resolved task-to-profile map plus each artifact's resolved model
settings.

Stage-oriented defaults:

- `alphabetic_entity_judge`: `gpt-5.6-sol`
- `scope_triage`: `gpt-5.4-mini` through the `economy` profile unless evals
  justify a stronger model
- `yomi_reading`: `gpt-5.6-sol` for production-quality reading comparison,
  `gpt-5.4-mini` for dev flow checks
- `yomi_repair`: `gpt-5.6-sol`
- post-review rescue repair: `gpt-5.6-sol` with web search allowed
- final emergency escalation: `gpt-5.5-pro` with web search, only after
  cheaper paths and human review have already failed

This keeps the main path simple and high-quality while still reserving a clear
escape hatch for the hardest cases.

### GPT-5.6 evaluation (2026-07-15)

GPT-5.6 model IDs and pricing were initially evaluated without changing pipeline
defaults. All runs used the existing task prompts and parameters through
background Responses API calls.

Per-target yomi reading on the 155-item regression set:

| Model | Correct | Parse errors | Estimated standard cost |
| --- | ---: | ---: | ---: |
| GPT-5.4 mini | 144/155 | 2 | $0.0169 |
| GPT-5.5 | 154/155 | 0 | $0.1017 |
| GPT-5.6 Luna | 134/155 | 3 | $0.0229 |
| GPT-5.6 Terra | 150/155 | 0 | $0.0547 |
| GPT-5.6 Sol | 155/155 | 0 | $0.1094 |

The original fixture incorrectly expected `近々/きんきん`; human final review
had selected `近々/ちかぢか`, so the fixture and saved outputs were rescored to
match the finalized corpus. Sol reproduced the same 155/155 result on a second
complete run. Sol is therefore the documented standard recommendation replacing
GPT-5.5 for new production-quality work. Historical GPT-5.5 results and pinned
batch configurations remain unchanged. A Batch API run also completed with the
identical 155/155 result and no
parse errors; its estimated cost was $0.0547 instead of
$0.1094 with standard processing.

Scope triage did not justify a profile change. On the 90-item balanced set,
GPT-5.4 mini and Luna scored 86, Terra 88, and Sol 87. On the expanded 392-item
set, Terra scored 389 at $0.2538 versus GPT-5.4 mini's 388 at $0.0768. Terra's
single-case gain is not worth roughly 3.3 times the cost, while Luna is both
more expensive and no more accurate than GPT-5.4 mini.

Strong repair was scored separately against 21 human-reviewed historical
outcomes because old queue rows do not embed gold results. Terra matched 18/21
exact segment lists (19/21 if only concatenated reading is considered) at
$0.1875; Sol matched 20/21 exactly at $0.5250. Neither model reliably solved the
web-dependent `真光元/しんこうげん` case: five independent repetitions succeeded
2/5 for Terra and 1/5 for Sol. Keep strong repair under human confirmation, and
do not treat a newer model as a substitute for that gate.

Experiment scoring for `yomi_repair` must use explicit `expected_segments` (or
the legacy `expected_rendered`). Missing repair gold is an evaluation error, not
an implicit pass.

For prompt exploration, `gpt-5.4-mini` is a reasonable search model because the
goal is to test prompt shape, label semantics, and sample quality quickly. Its
results should not be treated as the final production quality estimate. The
search should sweep mini reasoning effort settings and score each run by both
quality and cost. Promote only the best few prompt candidates to `gpt-5.6-sol` for
the final production-quality comparison.

## 9.3 Cost controls

For ordinary judgment tasks:

- keep the static prompt prefix identical
- put variable item text at the end
- set low verbosity
- use the lowest reasoning effort that preserves accuracy
- batch production jobs

For production cost control, prefer:

- `gpt-5.6-sol` plus caching and batching
- strict structured outputs
- short outputs for judgment tasks

Do not assume that moving routine corpus judgments to `mini` or `nano` is the
best optimization by default. Verify that with task-level evals first.

Also do not assume that bundling multiple judgments into one prompt is the best
optimization by default. The default policy should be one prompt per judgment
task, because that makes parsing, prompt iteration, and regression diagnosis
much cleaner.

Merged prompts should be treated as an optimization step that needs evidence.
Only merge tasks after evals show that the combined prompt preserves accuracy
and parser stability, and only when the tasks share the same unit, context
requirements, model policy, and failure surface.

## 9.4 Prompt caching

Prompt caching is only useful for exact shared prefixes and only starts once the
prompt is long enough.

Practical rule:

- keep instructions and examples first
- append unit data last
- use a stable prompt template version
- log cached-token counts in run metrics

For naturally short judgment prompts, do not pad them just to reach the cache
threshold. In many cases, a shorter and clearer prompt is the better
optimization. Caching matters most when a task already needs a long stable
shared prefix for good reasons, such as context-heavy repair or tool-using
rescue prompts.

## 9.5 Batch constraints

Batch jobs should be organized by stage and model profile.

Practical rule:

- one batch input file per model profile
- stable `custom_id`
- full manifest of prompt version, model profile, and parser version

Tool-using rescue jobs should be separated from ordinary batch jobs because they
have a different cost profile and should only operate on the small residue that
survives the normal pipeline.

## 9.6 Local pipeline orchestration

The operator should not need to remember the exact next script for each batch.

Recommended design:

- keep durable local pipeline state for each batch
- keep a current-batch pointer per track
- use `working` as the implicit default track and `dev` as an explicit second
  track
- `working` is the strict protected track and should enforce required human
  review gates; `dev` is the relaxed experimental track
- expose read-only `status` and mutating `next` commands
- expose a `prepare` command that allocates the next batch name for a track and
  extracts the requested number of documents
- let `next` perform one legal automatic step and then stop cleanly

This project has three different kinds of stages:

- fully automatic local computation
- external waits such as OpenAI Batch jobs
- human-review waits

Those should all be represented as normal pipeline states.

### 9.6.1 Per-batch and per-track state

Recommended storage:

- one state file per batch, such as `data/pipeline/batches/<batch_id>.json`
- one track pointer file per track, such as `data/pipeline/tracks/working.json`

Batch state should record at least:

- `batch_id`
- `track_name`
- `current_stage`
- `artifacts`
- `blocking_reason`
- `updated_at`

The state enum can start small and grow with the actual implementation.

Track state should record at least:

- `track_name`
- `current_batch_name` as a legacy/manual-command convenience pointer, not as
  the authoritative scheduler target
- `updated_at`

### 9.6.2 Current command surface

Current intended operator commands:

- `./prepare 100`
- `./prepare dev 10`
- `./prepare --yomi-unit-mode comma_span --yomi-auto-accept-profile off --llm-profile yomi_reading=smoke dev 10`
- `./next`
- `./next dev`
- `./next --force-stage yomi_generated`
- `./next --status`
- `./next dev --status`
- `./next dev --status --stages`
- `./status`
- `./status dev`

The implicit no-argument track should be `working`.

`./next --status` is the canonical read-only inspection command. `./status` is
kept as a compatibility wrapper around it.

### 9.6.3 Current one-step behavior

Current recommended behavior for the manual main orchestration command:

- load the current batch state for the requested track
- inspect the current stage and prerequisites
- run the next automatic step if legal
- stop after that one step and report the updated state

This describes `./next` and related debugging commands. It must not define the
unattended scheduler model. As refill and review import mature, `review-sync`
should sweep actionable batch/document state across the track rather than
touching only `track.current_batch_name`.

Examples:

- if a batch is freshly prepared, `./next` should build the alphabetic
  artifacts
- the following `./next` should build the unresolved alphabetic report
- the following `./next` should judge unresolved alphabetic entity types with
  the configured LLM mode and update the cached effective entity status
- the following `./next` should project cached alphabetic status back to units
  as provisional skip reasons before general scope/yomi processing
- the following `./next` should queue raw-text scope triage
- the following `./next` should run or resume scope triage and write
  `units.scope_triaged.jsonl`
- the following `./next` should build the mechanical yomi JSONL
- the following `./next` should add the yomi auto-accept artifact
- the following `./next` should build per-target safety evidence and queue LLM
  readings for unresolved kanji/Latin targets
- the following `./next` should run or resume the yomi-reading LLM task
- `./next --force-stage <stage>` should rerun the current completed stage
- on `working`, a confirmation prompt should appear only when rerunning would
  overwrite existing artifacts


## 10. Human Review

Human review is not a cleanup afterthought. It is one of the outputs of the
system.

Recommended policy:

- every exported record should know whether a human has looked at it
- human-reviewed records should remain distinguishable from auto-accepted ones
- corrected human decisions should be harvestable as future evaluation data
- the first review UI should show only the yomi-annotated sentence and three
  checkboxes
- the second review UI should show only the yomi-annotated sentence and a
  free-text comment box

### 10.1 Review transport

The review UI should not assume writable hosting on the cluster.

Current preferred transport design:

- keep the review UI in this same repository
- isolate it from the Python pipeline code as a small static web app
- host the static review UI on GitHub Pages
- export immutable review-pack JSON from the cluster
- use GitHub as the return mailbox
- for now, use one Issue per review pack and one comment per submission

The cluster should later poll GitHub, extract valid submission payloads, and
reconstruct the latest merged review state.

Recommended repo layout:

- `src/yomi_corpus/`: Python pipeline code
- `scripts/`: operator scripts and local orchestration
- `web/review/`: source for the static review app
- `docs/`: built static assets served by GitHub Pages

The important point is not the exact directory names but the separation:

- Python pipeline code should stay independent of frontend tooling
- the review UI should remain versioned with this project
- GitHub Pages output should be a normal repo artifact, not a separate system

Migration target: implement the intended `working` review workflow in `dev`
first, then move UI hosting and GitHub Issues into dedicated review
repositories. The current in-repo Pages UI remains acceptable while schemas
churn, but the next target should be a separate dev review repository, followed
later by a separate working review repository with stricter, more stable
behavior. The split should be limited to UI hosting and review-return
conventions. Pack generation, submission ingestion, replay semantics, and
corpus state should remain in this repository because they are pipeline-coupled.
The prerequisite is a stable contract:

- immutable review-pack schema with versioned stage-specific extensions
- exported review-submission schema with replay/overwrite semantics
- publishing contract from this repo to the UI repo or Pages branch
- importer contract for fetching matching Issue/comment payloads back into this
  repo

Until those contracts stabilize, keeping the UI source and Pages artifacts in
this repository is the lower-friction choice. The dev workflow should still be
designed as if the split already existed: review repositories own Pages and
Issues only; this repository remains the source of truth after importing and
replaying submissions.

Use `config/review_transport/default.toml` as the compatibility seam for that
future split. Each track should declare:

- `repo`: GitHub Issue mailbox for returned review submissions
- `pages_url`: expected browser entry point for the static review UI
- `publish_mode`: sync-time artifact behavior, one of `none`, `local`, or
  `gh-pages`

CLI overrides such as `./review-sync dev --repo ... --publish ...` are for
one-off operations. Stable transport choices should live in config so moving
`dev` and `working` to independent review repositories is mostly a configuration
and publishing-adapter change.

For yomi review, use the same transport model as alphabetic review: the cluster
writes a review-pack JSON file, GitHub Pages displays it, the browser keeps
local draft state, and the reviewer returns exported JSON through GitHub
Issues/comments/attachments. The page should default to the latest review pack
but keep older packs readable as immutable history.

The review pack should be a flat, continuous list grouped by visible document
separators rather than a hard document-switching UI. Each item still carries
`doc_id`, `unit_id`, and stable local/global indexes. This keeps review and
range export simple while preserving enough metadata to add document tabs or a
sidebar later. Document boundaries should be visually clear, for example:

```text
Document 3 / ja_cc_level2:0000000004
```

The first final-yomi review UI should be ruby-first and look like normal text,
not like a table of pipeline metadata. For each sentence/unit, show only:

- ruby-rendered text
- compact `Skip` and `Exclude` toggle buttons; `Keep` is implicit when neither
  is active
- only genuinely useful low-frequency actions; the unused range-selection
  control is removed

Do not show document IDs, unit IDs, target IDs, or signal details in the normal
view. Keep them in the review pack for audit/debugging, but hide them from the
main workflow.

Yomi targets are edited inline by tapping/clicking the ruby span, not by using
dropdowns. Unresolved targets should have a visible highlight. Safe targets
should not be visually noisy, but they may still be tappable if the pack has
useful candidate readings. A tap cycles through candidates, for example:

```text
近々(きんきん) -> 近々(ちかぢか) -> 近々 -> 近々(きんきん)
```

The candidate list should be derived from recorded evidence:

- current mechanical/hybrid reading
- LLM reading when it differs
- corpus-frequency dominant reading when available
- stable dictionary reading when available
- no-ruby / remove reading

Ruby placement and review interaction boundaries are separate concepts. Bulk
Review must not use an individual kanji or an individual `<ruby>` node as its
editable identity. Its normal editable unit is an **interaction span**: a
surface span with one complete reading, normally one rendered-yomi token and,
when necessary, a deliberately merged sequence of adjacent tokens. The span
must include attached okurigana and other characters that participate in that
reading. For example:

```text
corpus token:       後払い/アトバライ
interaction span:  後払い
ruby display:       後払(あとばら)い
```

Hovering, tapping, highlighting, candidate cycling, and no-ruby rejection all
apply to the complete `後払い` interaction span, even though the ruby renderer
places `<rt>あとばら</rt>` over only `後払`. Ruby nodes are therefore a derived
display projection and must not be reused as target IDs or repair boundaries.

Keep these layers explicit in review-pack and submission processing:

- **corpus tokens** are the canonical `surface/reading` sequence stored and
  exported by the pipeline
- **interaction spans** are stable source-offset ranges used for review actions
  and may cover one or more corpus tokens
- **reading candidates** belong to the complete interaction span and carry a
  replacement token sequence, not a mutation of one displayed kanji
- **ruby ranges** are dictionary-derived display nodes inside an interaction
  span and may cover only part of its surface

An accepted candidate replaces the complete interaction span with its recorded
token sequence. `No ruby` rejects that complete span and sends the same exact
surface to Escalated Repair. Strong-repair proposals must concatenate to that
surface, while being free to return different word boundaries. This prevents
truncated repairs such as sending `後払` when the reviewed unit was `後払い`.

Bulk Review may expose one deliberately narrow segmentation action after a
kanji-bearing interaction span is changed to `No ruby`: the immediately
adjacent canonical kana token on either side becomes tappable, just as an
adjacent numeric token does. Tapping it creates one merged Escalated Repair
region without guessing across additional token boundaries. For example,
`はる/ハル 夏/ナツ` may be submitted as the rejected region `はる夏`; the model
can then return `はる夏/ハルカ`. Punctuation, spaces, non-kana tokens, and kana
outside that single adjacent canonical token are not absorbed automatically.

Migration should be incremental:

1. Add interaction-span IDs, source offsets, complete surfaces, candidates,
   and ruby-display projections to review packs while retaining the current
   target fields as compatibility data.
2. Make candidate generation and submission replay operate on interaction
   spans, then render the existing UI from those spans.
3. Switch hit testing, highlighting, candidate cycling, and cancellation to the
   complete interaction spans.
4. Regenerate active review packs and strong-repair queues; finalized canonical
   yomi does not require migration.
5. Remove kanji-target compatibility fields only after active drafts and Issue
   submissions using the old schema have been drained or explicitly retired.

Changed spans should use a separate color from unresolved highlights. Removing
the ruby or choosing an available alternate reading is a span-level override,
not a whole-sentence rejection.

No-ruby is the normal way to request Escalated Repair. If consecutive
targets are canceled in the same sentence, group them into one repair span. Do
not ask the human reviewer to decide whether web search is needed. The
Escalated Repair prompt/model should make that decision from the local target
context, rejected readings, and entity-like cues, and should record whether web
search was actually used.

Whole-sentence escalation should be reserved for a future advanced fallback if
real examples require it. Strong-model handling must be a separate later stage,
and its output should return to an Escalated Repair confirmation UI.

The Escalated Repair confirmation UI for strong-model outputs should be
different from the Bulk Review UI. It should show ruby-rendered text and expose
raw editable structured data, so a human can directly correct the result before
it enters the final corpus.

Review entry point migration:

- replace the main stage dropdown with one task dashboard
- show at least three sections: `Bulk Review`, `Escalated Repair`, and
  `Deferred local tasks`
- let the reviewer start a task from either active queue; task start should
  move into the existing focused task screen for that queue type
- use the same task shell for both queue types: selected documents, queue ID,
  local draft key, copy/open-Issue controls, defer, and complete
- `Defer` should save the local task draft and return it to the dashboard
- `Complete` should clear only the local draft after the reviewer has copied or
  submitted JSON; imported pipeline state, not local storage, decides whether a
  document is actually complete
- `Copy JSON and Open Issue` should copy the review JSON, open a GitHub Issue
  page with the title filled in, and then show a return/focus modal asking
  whether the Issue was created
- if the user confirms submission, the task should move to `Submitted local
  tasks`; submitted tasks are greyed out in the Pack Map and disabled in the
  active queues only while their documents remain in the submitted task's stage.
  If regenerated server-side state moves a document to another stage or to
  resolved, remove that document from the local task. If no documents remain,
  delete the task. Local tasks are resumeable work, not history
- Unified Yomi Review is now the only normal human-facing review entrypoint.
  Legacy stage packs may remain as internal/history payloads while cleanup is
  incremental, but the public page should not expose a Classic mode or a stage
  selector when Unified review sources are available.

This depends on review packs carrying document-level queue state. The UI should
render from that explicit metadata rather than inferring pending/completed
documents from old batch-level stages.

### 10.2 Review state model

Separate three things:

- immutable review pack
- device-local draft state in the browser
- append-only review submissions returned through GitHub

The browser should persist local drafts by `review_stage` and `pack_id` so a
reviewer can leave the page and return later.

### 10.3 Reviewed ranges and overrides

For review export, a reviewed range matters more than explicit marks on every
approved item.

Recommended behavior:

- by default, the whole pack is in scope for export
- optional `from` / `to` markers narrow the reviewed range
- items outside that range remain visible but faded
- within the reviewed range, no explicit mark means "accept the proposed
  action"
- the submission payload should therefore contain reviewed range metadata plus
  sparse item-level overrides

### 10.4 Yomi Audit Queues

Yomi review should distinguish queue semantics from UI widgets. The same
checkbox-style UI can serve both high-throughput audit and focused review if
the queue context is explicit.

Recommended yomi queues:

- `bulk_ok_audit`: units with automatic `OK`, including mechanical and LLM
  `OK`; the reviewer scans quickly and marks only suspicious/problematic
  sentences
- `focused_review`: units that remained `Review` after triage and repair; the
  reviewer inspects carefully and accepts only after resolving the issue
- `skip_audit`: optional sampling of `Skip` units to check non-target decisions

The checkbox can be identical in both main queues: "problem found" or "needs
attention." The meaning of an unchecked item depends on queue context:

- in `bulk_ok_audit`, unchecked means accepted by default after fast scanning
- in `focused_review`, unchecked means accepted after focused inspection
- checked means the sentence should go to correction or remain unresolved

This keeps the UI simple while still changing the reviewer's posture. `OK`
queues are designed for anomaly detection over many items; `Review` queues are
designed for deliberate sentence-by-sentence validation.

### 10.4.1 Migration target: document-level work queues

Target: use larger durable pipeline batches, such as 100 documents, while
letting the browser expose smaller review work slices selected by document,
range, or checkbox. The batch should remain the pipeline data unit; a browser
work slice should be only a claim/submission unit.

The next dev implementation should prototype the intended `working` workflow.
That means the pipeline should stop treating Bulk Review and Escalated Repair
as one global batch stage. Different documents in the same batch may be in
different states:

- waiting for Bulk Review
- Bulk Review applied, waiting for Escalated Repair
- Escalated Repair complete
- skipped
- complete

The intended model has three layers:

- batch state: the durable set of documents owned by the pipeline
- per-document review state inside the batch, especially Bulk Review and
  Escalated Repair pending states
- review submissions returned through GitHub Issues/comments, keyed by stable
  document IDs, item IDs, queue/stage IDs, and pack IDs

Workflow target:

1. A large batch is prepared and documents enter the Bulk Review pending list.
2. The UI lets a reviewer choose a document range or checked subset and keeps
   the in-progress slice in browser storage.
3. Starting or submitting a browser task uses one GitHub Issue for that selected
   work slice.
4. A periodic importer polls Issues/comments, validates matching payloads, and
   applies them idempotently.
5. Successfully applied Issues are closed as `completed`; invalid or mismatched
   Issues remain open with a clear reason or are ignored until a later sync.
6. Applied Bulk Review submissions grey out or remove completed documents from
   the Bulk Review list.
7. Documents with canceled/local unresolved readings move to
   `strong_repair_pending`.
8. Strong-repair submissions are imported the same way. Accepted documents
   disappear from the second list.
9. Documents with no Escalated Repair need can move directly from Bulk Review to
   `complete`.
10. When every document is `complete` or `skipped`, the polling/orchestration
    process finalizes the batch and prepares the next large batch.

Design constraints:

- local state should be per-document, not only one batch-level stage. A minimal
  state vocabulary is `final_pending`, `final_in_review`, `final_reviewed`,
  `strong_pending`, `strong_in_review`, `strong_reviewed`, `complete`, and
  `skipped`
- the concrete state meanings are:
  - `final_pending`: actionable Bulk Review has not been submitted
  - `final_in_review`: part of the document has Bulk Review applied, but more
    units remain
  - `final_reviewed`: Bulk Review is submitted/applied and backend processing
    is deciding whether the document is complete or needs Escalated Repair
  - `strong_pending`: Escalated Repair is needed, but it is actionable only
    after repair proposals/results exist
  - `strong_in_review`: part of Escalated Repair has been reviewed, but more
    repair items remain
  - `strong_reviewed`: Escalated Repair review is submitted/applied and awaits
    final backend resolution
  - `complete`: accepted for corpus output
  - `skipped`: intentionally excluded from corpus output
- this per-document state is the canonical source of active queue membership.
  Bulk Review is a view over `final_*` states, and Escalated Repair is a view
  over `strong_*` states. A document may remain present in older/generated
  packs for preview or audit, but it must be active in at most one queue.
- review packs should expose queue-view metadata such as `queue_member` and
  `selectable`; UI queue panels should use those fields instead of inferring
  membership from whether a document row exists in a pack.
- the human-facing dashboard should present non-actionable post-submission
  states as submitted/processing. For example, `final_reviewed` or
  `strong_pending` without completed repair results should not appear as
  `Resolved`; it should stay in the previous visible bucket with a submitted
  overlay until backend processing either creates an actionable Escalated Repair
  task or marks the document complete. Submitted is not a fourth bucket; every
  document should be visible in exactly one of Bulk Review, Escalated Repair,
  or Resolved.
- Pack Map is a read-only overview over current actionable work only. Finalized
  batches should leave the active Pack Map after publication advances to later
  queues.
- Pack Map previews should show the latest available local view when possible:
  active, deferred, or submitted browser drafts can be overlaid for display only
  while the draft's task stage still matches the document's current stage. This
  overlay is presentation-only; imported pipeline document state remains
  the canonical source for Bulk Review, Escalated Repair, and Resolved buckets.
- Completed documents should be visible through a separate Corpus Map. It uses
  Pack Map visual language but reads static finalized archive shards:
  `docs/review/archive/index.json` lists finalized-document shards, and each
  shard contains finalized text, raw yomi, and ruby display data for a bounded
  document range. The first Corpus Map is read-only; later finalized-document
  corrections should be submitted as auditable correction Issues rather than
  mutating archive data locally.
- Future/unprocessed documents are not part of the review UI for now. If a
  full-corpus map is needed later, implement it as a separate static browsing
  mode rather than mixing raw future documents into active queues.
- browser local storage tracks in-progress work by `pack_id`, `queue_id`, and
  selected document IDs or range
- greyed-out/completed state comes from imported pipeline queue state, not from
  browser-only state
- imports must be idempotent; re-reading the same Issue/comment must not
  duplicate work
- later valid submissions still use the existing replay rule for overlapping
  ranges/items
- Escalated Repair should be a derived queue from Bulk Review output, not a
  separately hand-managed batch
- periodic sync should be explicit operator behavior, for example a future
  `./review-sync dev` command that imports Issues, updates document states,
  republishes review packs when needed, and starts the next batch only when the
  current batch is complete

This should be implemented in small steps: document-level state, issue closing,
periodic sync, and then separate review repositories. The goal is to let human
work scale to large batches without making each browser session large or
fragile.

Long-term workspace target:

- the reviewer should eventually work in a document-centered workspace, not a
  batch-centered page
- backend batches become processing chunks that can be prepared ahead of human
  work
- while the reviewer works on one slice, such as documents 31-40, the
  orchestrator may process and append later slices, such as 41-50
- one UI workspace may contain documents produced by multiple backend batches
- batch IDs remain important for provenance, artifact lookup, and replay, but
  they should not define the user-visible queue boundary

The workspace map should be able to cover a large corpus range, for example
10,000 documents:

- processed documents render from canonical server-side yomi data
- active documents render from current queue payloads
- submitted documents render with a temporary local overlay until importer
  state catches up
- unprocessed documents render raw text only

Implementation requirements:

- server-side per-document state is authoritative
- browser-local state is only an overlay for active, deferred, and submitted
  tasks
- heavy review payloads should be sharded or lazy-loaded
- compact map/index data should be separated from full per-document payloads
- large maps should use virtualization rather than rendering every document
  body eagerly
- periodic preparation should maintain a buffer of reviewable documents without
  requiring the previous human slice to be fully finalized first

Rolling Bulk Review refill should be implemented as a queue-maintenance policy
over the document-state ledger, not as a new kind of batch:

- keep a configured target number of actionable Bulk Review documents, for
  example `bulk_review_target_ready_docs = 50`
- each `./review-sync <track>` pass should count documents whose canonical
  state makes them selectable in Bulk Review
- for refill accounting and corpus-map display, derive coarse pool labels from
  canonical document state:
  - `unprocessed`: source document has not entered the track
    ledger/preparation flow
  - `prepared`: deterministic/LLM preprocessing exists, but the document is not
    yet actionable in Bulk Review
  - `bulk-ready`: actionable `final_pending` or `final_in_review`
  - `bulk-submitted`: `final_reviewed` or non-actionable `strong_pending` while
    backend processing decides the next visible bucket
  - `escalated-ready`: actionable `strong_pending` with repair proposals, or
    `strong_in_review`
  - `escalated-submitted`: `strong_reviewed` while backend finalization is
    pending
  - `resolved`: `complete` or `skipped`
- these labels are derived summaries for selection and display only. They must
  not become a second mutable state machine; the concrete `final_*`,
  `strong_*`, `complete`, and `skipped` values remain authoritative.
- the initial implementation exposes those counts as `queue_counts` in the
  document-state summary and the review-sync summary.
- the implemented refill primitive is:
  `./review-sync <track> --bulk-review-target-ready-docs N --refill-pass-limit M`
  reports current `bulk-ready` count, target, deficit, and capped planned
  prepare count. `--dry-run` additionally reports the exact source documents
  that would be selected and deliberately keeps `will_prepare: false`.
- if the count is below target and the pass is not a dry run, the runner
  prepares more source documents, runs the automatic/LLM stages needed to make
  them reviewable, appends their document states to the ledger, and republishes
  review artifacts according to `--publish`
- if any prepared/refill batch is already before `final_review_prepared`,
  refill should resume that batch rather than preparing another duplicate
  source slice. The current implementation approximates this with
  `track.current_batch_name`, but the target scheduler should discover such
  batches from batch/document state.
- refill should be bounded by a per-pass limit so a sync does not unexpectedly
  launch an unbounded amount of LLM work
- the UI may show documents from multiple backend preparation batches in one
  Bulk Review queue; batch IDs remain provenance, not user-facing queue
  boundaries
- refill must be idempotent: a source document selected once must not be
  selected again, and partially prepared documents should resume rather than
  duplicate

The durable ledger should be the source of truth for this refill behavior. A
minimal ledger row should include:

- stable source document ID and source ordering key
- stable track-level document sequence number
- current document state
- preparation batch/artifact IDs that produced the current review payload
- current queue membership flags for Bulk Review and Escalated Repair
- submission/import provenance, including Issue/comment IDs when applicable
- pointers to the latest canonical yomi payload, repair payload, and finalized
  output when available

Document numbering must not be batch-local once rolling refill is enabled. The
UI, Issues, correction payloads, and corpus-wide map should use a stable
`track_doc_seq` assigned once when a source document first enters a track's
ledger. Batch-local `doc_seq` may still exist inside preparation artifacts, but
it is not the reviewer-facing identifier. Rules:

- `doc_id` remains the authoritative stable source identifier
- `track_doc_seq` is the human-facing stable order number within one track
- the ledger assigns `track_doc_seq` monotonically and never renumbers existing
  documents
- regenerating or resuming a preparation batch must reuse the existing
  `track_doc_seq` for the same `doc_id`
- published packs may include batch-local sequence numbers for debugging, but
  browser queues, Pack Map tiles, and Issue payloads should include both
  `track_doc_seq` and `doc_id`
- cross-track numbering is intentionally separate; `dev` and `working` may have
  different `track_doc_seq` values for the same source document

Corpus-wide map viewing should be a separate read-mostly artifact family, not a
large editable review pack. The map should be generated from the ledger plus
source/final data:

- `docs/review/map/index.json` should contain corpus range metadata, shard
  names, counts by document state, and current queue sizes
- `docs/review/map/shard_XXXX.json` should contain compact rows for a bounded
  contiguous source range: document ID, source order, state, queue membership,
  short plain-text preview, and pointers to detail payloads
- the first implementation should show resolved/finalized documents only, as
  resolved-style tiles that open finalized yomi/ruby previews
- active/submitted documents already have the Active Work view and should not
  be mixed into Corpus Map until a concrete need appears
- unprocessed/future documents are out of scope for now

The first Corpus Map milestone is read-mostly overview and preview. Editing
resolved documents starts as an auditable correction Issue export, not an
in-place archive mutation. The browser can prepare a
`finalized_correction_patch` payload from a finalized document preview:

- row-based editing where only explicitly saved, changed finalized units are
  exported
- unchanged unit IDs and order
- compact original and proposed `[surface, reading]` arrays
- readings must satisfy the canonical token structural rule: kanji or Latin
  surfaces normally need katakana readings, Arabic and Roman-numeral surfaces
  need empty readings, multi-character Japanese digit runs permit either empty
  or katakana readings, and kana/symbol surfaces need normalized literal readings
- source surfaces preserved relative to the original rendered-yomi tokens after
  removing whitespace. ASCII-space/NBSP differences should not invalidate an
  otherwise unit-scoped yomi correction. The editable text view uses reversible
  escapes for literal slashes, backslashes, and ASCII spaces.
- at least one changed unit
- alternatively, a disposition-only change to `Keep`, `Skip`, or `Exclude`

This correction payload is unit-scoped. Sentence or unit boundary changes are a
separate future workflow, probably with a distinct payload type, because they
affect document identity, audit replay, decoder export, and ruby dictionary
harvesting.

The initial payload is copied to a GitHub Issue with
`submission_type: finalized_correction_patch`,
`review_stage: finalized_correction`, document identity, archive source
metadata, and changed units containing `original_yomi_tokens` and
`proposed_yomi_tokens`, plus `disposition` when corpus membership changes.
Applying `Skip` atomically moves the unit to the recoverable skipped artifact;
applying `Exclude` writes a content-free terminal tombstone. New payloads use
schema version 2; the importer may
dual-read legacy schema-v1 rendered strings during migration.
Server-side import/replay is a follow-up step and must repeat validation before
updating canonical finalized state.

Each browser-created correction has a stable `submission_id` and records the
document's `base_archive_revision`. Published archive documents expose their
current revision and each unit's applied finalized-correction submission IDs.
The browser keeps a submitted correction marked `sent` until every affected
unit acknowledges that exact ID; an unrelated archive publication must not
clear it. The local-store schema-v2 migration removes legacy submitted records
that have no submission ID, while preserving unsent drafts.

Resolved-document correction is a later extension of the same model. A resolved
document may be reopened through a correction task, but the change should be
recorded as a new auditable submission, replayed by the importer, and harvested
for exact rewrite defaults or ruby dictionary additions. The canonical document
state changes only after that replay succeeds.

Review sync command:

- `./review-sync <track>` is the explicit polling entry point
- the command must acquire a per-track lock and be safe to rerun
- one pass imports matching Bulk Review and Escalated Repair submissions from
  open GitHub Issues/comments, runs the local apply stages that are currently
  reachable, regenerates local review artifacts if state changed, and writes a
  JSON summary
- Issues are closed only after their matching submissions were imported and the
  corresponding local apply step succeeded
- invalid or not-yet-applicable Issues stay open; a later sync may apply them
- `--loop --interval <seconds>` may be added for unattended polling, but the
  default mode should remain one bounded pass rather than a resident daemon
- review artifact handling should be a single mode, not two booleans:
  `--publish none` applies state only, `--publish local` regenerates local
  `docs/review`, and `--publish gh-pages` regenerates and runs
  `./publish-review`. The default is `local`.
- non-transport automation belongs in `config/review_sync/default.toml`. The
  current decoder-refresh policy is configurable per track:
  `never`, `on-finalize`, or `always`, plus minimum new finalized batches and
  minimum interval.
- decoder refresh should be a post-finalization hook. It must not roll back or
  invalidate successful review finalization if the decoder build fails; report
  the failure in the sync summary and retry in a later pass.

LLM polling policy:

- background and batch LLM execution should use durable per-stage job state so
  the caller can stop and resume without duplicating completed results
- one command invocation must be bounded; the shared LLM runner stops after the
  configured maximum wait or after a stale-progress timeout with no increase in
  completed results
- the default source-level guard is one hour total wait and ten minutes of no
  completed-result progress
- a timeout is not a failed stage by itself; the stage remains incomplete with a
  `running` job summary and a `status_reason`, and the next `./next` or
  `./review-sync` invocation resumes polling/submission

### 10.5 Multiple partial submissions

One pack may produce multiple submissions.

That supports:

- interrupted review
- work split by range
- accidental multi-device use

Merge rule:

- replay submissions in order
- later submissions overwrite earlier results for overlapping items
- for an overlapping reviewed range, reset that range to default proposal
  acceptance first, then apply that submission's sparse overrides

This is intentionally simple and leaves conflict responsibility to the user.

### 10.6 Current yomi Bulk Review application path

After `final_review_prepared`, the concrete pipeline stages are:

- `final_review_applied`
- `yomi_strong_repair_queued`
- `yomi_finalized`

`final_review_applied` consumes local JSON submissions under
`data/review_submissions/yomi_final/`. These are the files exported by the
GitHub Pages review UI or imported from GitHub Issues by a separate ingestion
step. The replay semantics match the review UI:

- reviewed ranges define coverage
- no explicit override inside a reviewed range means accept
- sparse overrides can set sentence skip or target-level reading choices
- target-level `No ruby` means the current reading was rejected and should be
  grouped with adjacent canceled targets for focused Escalated Repair
- sentence skip dominates operational processing; target choices on skipped
  rows are retained as audit data but do not update rendered yomi or trigger
  Escalated Repair
- later overlapping submissions overwrite earlier ones

At `final_review_applied`, `./next` first runs the open-Issue ingestion path and
then applies the local submission store. The importer is non-fatal: network
errors are recorded in the import summary, after which the stage behaves as if
no new submissions were imported.

Manual ingestion commands remain available for debugging:

```bash
python scripts/import_yomi_final_review_issue.py --issue-number 1
python scripts/import_yomi_final_review_inbox.py
```

The old alphabetic Issue importer is intentionally retired. Alphabetic
classification remains cached as provisional evidence, but final human
overrides are collected through the Bulk Review path.

If no matching submission exists, the stage blocks as a human review gate.

`yomi_strong_repair_queued` writes a target-group queue for cases that need the
stronger repair model:

- consecutive target-level `No ruby` overrides as `repair_scope:
  "target_group"` and `repair_order: 1`

The queue row should carry the grouped targets, any human-selected local
constraints, and enough sentence context for the strong model to decide whether
web search is needed. Sentence-level escalation is legacy/fallback plumbing, not
the preferred path.

Canceled target readings should carry `rejected_readings` in the strong queue.
That gives the strong/web repair prompt negative evidence such as
`史輝` is not `ふみてる` in a publisher-name context, or `元` is not `もと`
inside `真光元`.

`yomi_strong_repair_llm_completed` runs `config/llm/yomi_repair.toml` on the
queue and applies valid target-group repairs to:

```text
data/units/<batch>/units.yomi.strong_repaired.jsonl
```

The applier is deliberately narrow. It accepts only valid parsed JSON arrays
whose concatenated surfaces exactly match the rejected span, and it converts
readings to the pipeline's katakana `surface/READING` representation. Missing
results, parse errors, sentence-scope repairs, invalid readings, or surface
mismatches remain blocking.

Future work: after an Escalated Repair is accepted in human confirmation, promote
the repaired surface span into an exact learned default for later batches. This
is useful for multi-token or boundary-crossing repairs such as `一発` becoming
`いっぱつ` or `池尻中学校` becoming `いけじり ちゅうがっこう`. Promotion should
use the whole confirmed surface span by default, not infer a broader regex or
subspan rule unless separately approved.

The Escalated Repair confirmation UI can also accept a human-edited segmentation
for the rejected local span. The intended first UI is a boundary-toggle editor:
characters inside the rejected span are joined with `=` and split with `/`, and
one reading input is shown for each resulting segment. The submitted
`manual_segments` remain canonical `surface/reading` data. They override the
LLM repair only after validation confirms that segment surfaces concatenate
exactly to the rejected span and readings are valid kana.

The pack builder should provide reading candidates for possible segments before
the reviewer edits boundaries. Use the annotated-form dictionary as a
prefix-style source: from every character position in the rejected span, collect
dictionary readings for substrings up to the configured maximum length. This
lets splits such as `池尻/中学校` or `池尻中/学校` prefill known readings while
preserving all dictionary candidates for ambiguous substrings.

`yomi_finalized` consumes `units.yomi.strong_repaired.jsonl` when it exists, but
Escalated Repair results are still candidates. If the repair queue is non-empty,
finalization blocks until a later human confirmation stage marks the repair
apply summary as confirmed. Missing or incomplete repair apply
summaries also block finalization.

`yomi_finalized` writes the no-escalation final output when the repair queue is
empty. If the queue is non-empty, it blocks rather than pretending the batch is
done.

Successful finalization also harvests conservative reusable artifacts:

```text
data/units/<batch>/manual_yomi_rewrites.jsonl
data/units/<batch>/supplemental_furigana.tsv
data/units/<batch>/yomi_finalization_harvest_summary.json
data/lexicon/manual_yomi_rewrites.jsonl
data/lexicon/supplemental_furigana.tsv
```

Manual yomi rewrite rows come from accepted Escalated Repair results or human
`manual_segments`. They are exact surface-span defaults only. For example,
`池尻中学校 -> 池尻/イケジリ 中学校/チュウガッコウ` may be reused
when the exact same surface span appears later. These rows should not be
generalized automatically.

Supplemental furigana rows are display/allocation knowledge only. They record
final accepted `surface`, `reading`, and `annotated_surface` triples not already
available as exact Sudachi-derived annotated-form entries. Future ruby
rendering should load this TSV together with the base annotated-form TSV, but
the underlying yomi corpus remains `surface/reading`.

`./next --auto` may be used to advance repeatedly through non-human stages. It
stops on human gates, incomplete stages, confirmation requirements, blocking
errors, or final completion. Single-step `./next` remains the debugging default.

### 10.7 Decoder model refresh as maintenance

Refreshing the `yomi-decoder` language model should not be a standard per-batch
stage. It should be an explicit maintenance workflow run after one or more
batches have been finalized.

Division of responsibility:

- `yomi-corpus` exports reviewed additions in a stable decoder-training format
- `yomi-decoder` accepts base corpus plus one or more extra reviewed corpora
- `yomi-decoder` writes model artifacts to a caller-specified output directory
- `yomi-corpus` records and selects the decoder model used for later batches

Track-scoped model policy:

- keep `dev` and `working` decoder additions separate by default
- `dev` refreshes may be frequent and experimental
- `working` refreshes should use only reviewed working material, or dev material
  that has been explicitly promoted
- each track records its latest decoder model path
- each new batch copies the track's latest model path into its own batch state
- a running batch never changes decoder model implicitly

Recommended artifact layout:

```text
data/decoder_corpora/dev/<batch>.txt
data/decoder_corpora/working/<batch>.txt
data/decoder_models/dev/<model-id>/
data/decoder_models/working/<model-id>/
```

The low-level shape is:

```bash
python /path/yomi-corpus/scripts/export_decoder_corpus.py \
  --batch reviewed_batch_name

python /path/yomi-decoder/scripts/build_model.py \
  --base-corpus /path/core_SUW_yomi_final.txt \
  --extra-corpus /path/yomi-corpus/data/exports/decoder_corpus/reviewed_batch_name.txt \
  --output-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD
```

and later:

```bash
python /path/yomi-decoder/scripts/decode.py \
  --model-dir /path/yomi-corpus/data/decoder_models/model_YYYYMMDD \
  ...
```

The operator-facing refresh command is:

```bash
python scripts/refresh_decoder_model.py --track dev
python scripts/refresh_decoder_model.py --track working
```

It exports finalized corpora for that track, builds a new decoder model, and
updates that track's latest decoder model pointer. New batches copy that
pointer into their batch manifest/state for reproducibility.

Automated refreshes use a separate maintenance worker. `review-sync` only
writes a durable request after the configured finalization thresholds are met;
`./decoder-refresh-worker <track>` performs the expensive export and KenLM
build under an independent lock. Failed requests remain pending for retry, and
successful workers clear only the request ID they consumed. This keeps Issue
application and review publication responsive while model construction runs.

Model retention should favor reproducibility without retaining every heavy
artifact forever:

- keep the latest runtime model and a small recent window of full model
  directories
- for older models, retain manifest/provenance data rather than all generated
  files
- provenance should include model ID, build timestamp, track, yomi-decoder
  version or commit, base corpus identity, finalized batch/document IDs,
  exported yomi row versions or hashes, build config, thresholds, aggregate
  corpus/model counts when available, and hashes of generated runtime artifacts
- atomically publish refreshed models by building into a versioned directory,
  validating it, and then updating the latest-model pointer
- cleanup may delete old `model.arpa`, `ngram_corpus.txt`, `model.klm`, and
  `lexicon.jsonl` files once their manifest is retained, but it must never
  delete the current latest-model target

Batch manifests should record enough information to reproduce decoder behavior:

- decoder executable/version
- base corpus version
- extra corpus input manifest
- model directory or model ID
- build timestamp and build parameters

This keeps model refreshes as deliberate experiment boundaries rather than
implicit side effects of finishing a batch.


## 11. Recommended First Iterations

### Iteration 0: skeleton and schemas

Build:

- repo layout
- schema definitions
- import and unit extraction
- run manifest format

Do not call the API yet.

### Iteration 1: mechanical feature baseline

Build:

- Python Sudachi adapter using the same config as the shell wrapper
- `yomi-decoder` adapter
- first feature capture for later scoring
- no sentence-level gating rules yet

Measure:

- auto-accept rate
- obvious failure buckets

### Iteration 2: cheap LLM triage

Before production use, run a concentrated prompt-optimization phase that is
separate from corpus batch progression. Build a fixed gold eval set, test many
prompt candidates, and freeze a prompt version before large-scale processing.
This has an upfront cost, but it avoids paying corpus-scale costs for weak early
prompts and keeps batch outputs comparable across time.

Use real-time API calls for this search phase. Do not use Batch API for the
first exploratory loop, because waiting for batch completion slows prompt
iteration and makes it harder to inspect failures while the prompt is still
fluid.

Build:

- synchronous prompt-testing command
- one compact triage prompt
- batch submission path reusing the same prompt format
- gold eval data under the active task's directory in `data/evals/`
- prompt candidates under `config/prompts/experiments/`

Measure:

- accuracy against the fixed gold set
- dangerous confusion types, especially `Keep` when scope triage expects
  `Skip`
- parse-error rate
- input/output token counts
- cached input token counts when reported
- estimated cost
- cost per 10k units
- distribution of class codes
- model and reasoning effort
- exact API input-token counts for cache-sensitive candidate prompts

Search strategy:

- start with `gpt-5.4-mini` and synchronous calls
- test broad prompt families before small wording edits
- include zero-shot prompts, but expect few-shot boundary examples to be needed
- compare `low` and `medium` reasoning effort first; add higher effort only if
  accuracy failures look reasoning-bound
- rank candidates by dangerous-error avoidance, parse stability, accuracy, then
  token/cost efficiency
- rerun only the strongest candidates on `gpt-5.6-sol` before freezing production

Cache strategy:

- keep instructions and examples before variable unit text so cacheable content
  is a stable prefix
- use local `tiktoken` only as an estimate for GPT-5-family prompts
- use the Responses `input_tokens` endpoint for exact counts when tuning around
  the 1024-token cache threshold
- treat token-count endpoint cost, if any, as negligible relative to prompt eval
  and corpus annotation costs
- if examples are accuracy-useful and the prompt is already near 1k tokens,
  tune the static prefix to about 1050-1150 exact API-counted tokens rather than
  leaving it just under the cache threshold
- do not add meaningless filler only to force caching

### Iteration 3: regex repairs and context repair

Build:

- deterministic repair rules
- local-context repair prompt
- document-context repair prompt

Measure:

- repair precision
- reduction in manual-review load

### Iteration 4: human review loop and promotion

Build:

- review queue export/import
- reviewed-status tracking
- promotion pipeline into decoder training data


## 12. Immediate Next Steps

The skeleton, unit extraction, mechanical yomi generation, LLM execution modes,
review-pack generation, and GitHub-Issue review import are now implemented.
Current work should focus on closing the real review loop and then making later
batches benefit from reviewed data:

1. regenerate any active review packs after candidate-selection changes
2. import and apply Bulk Review submissions for the active dev batch
3. finalize batches that have no Escalated Repair queue
4. implement the real `yomi_strong_repair` stage for canceled ruby target groups,
   with model-side web-search judgment when context is insufficient
5. harvest accepted repairs into conservative learned default rules
6. feed human skip/unskip decisions back into alphabetic token decisions where
   appropriate
7. export finalized yomi into the decoder supplemental corpus and refresh the
   track-local decoder model
8. repeat small dev batches until the process is stable enough to set strict
   `working` defaults
