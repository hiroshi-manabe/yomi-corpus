# Escalated Repair Segmentation

The active prompt is `config/prompts/yomi_repair.txt`. On 2026-09-08 we removed
the instruction to merge an entire recognized entity into one item. Entity
recognition determines readings, not necessarily corpus token boundaries.

Prefer natural word-level segments, specifically family name / given name when
identifiable, and distinctive name / institutional or descriptive suffix.
Preserve each name component and ordinary lexical compounds internally. Do not
force uncertain boundaries or split into individual kanji. Existing correct
readings, hard whitespace boundaries, kaomoji handling, optional web search,
and Japanese explanatory comments remain unchanged. No example-specific names
were added to the production prompt.

## Local Evaluation

`scripts/build_repair_segmentation_eval.py` extracts nine unique spans from
human boundary edits in batches 1050, 1052, and 1054, retaining submission paths.
It also prepares six older regression cases and additional personal-name
contexts. These are targeted development cases, not an unbiased accuracy sample.

Artifacts and prompt snapshots:
`data/experiments/repair_segmentation_20260908/`.

Both old and revised prompts were called with `gpt-5.4-mini`, medium reasoning,
medium verbosity, text output, 8192 maximum output tokens, and web search with
medium context size, using the existing repair task runner.

| Nine human-edited spans | Old prompt | Revised prompt |
| --- | ---: | ---: |
| Exact boundary agreement | 0/9 | 6/9 |
| Exact segments and readings | 0/9 | 5/9 |

The revised prompt returned 西尾 / 美恋, 嶮山 / 小学校, 市立 / 城北 / 中学校,
城北 / 中学校, 新潮 / 文庫, and 山喜房 / 仏書林. The latter differed from the
human reading despite matching boundaries; that discrepancy is not adjudicated
by this experiment. 初出演, 角大工, and 小水路 still differed from human edits.
Do not treat every failure as evidence for a word-specific prompt exception.

All six older controls matched: 池尻 / 中学校, two 視来 contexts, 一発, 真光元,
and 靏見. In particular, the ordinary-word and unsplittable-name controls did
not regress in this run. Single runs do not establish deterministic behavior.
Two additional human-edited 西尾美恋 contexts also matched 西尾 / 美恋, giving
3/3 matching personal-name contexts across the tests. Total estimated API cost
for all 26 requests, including web-search calls, was approximately $0.344.

This changes future requests only. It does not overwrite stored LLM responses,
human corrections, or currently published review items.
