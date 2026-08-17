from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.yomi.final_review import (
    FINALIZED_CORRECTION_STAGE,
    FINALIZED_CORRECTION_SUBMISSION_TYPE,
    apply_finalized_correction_submissions_file,
    apply_manual_strong_repair_segments,
    apply_target_group_strong_repair,
    apply_manual_strong_repair_review_segments_file,
    apply_manual_correction_flags_file,
    apply_exact_rendered_target_overrides,
    apply_final_review_file,
    apply_strong_repair_review_file,
    apply_yomi_strong_repair_results_file,
    build_review_target,
    build_target_override,
    build_review_item,
    build_ruby_segments,
    build_strong_repair_queue_file,
    build_yomi_strong_repair_review_pack_file,
    canonicalize_finalized_unit_yomi,
    default_target_rows,
    build_yomi_final_review_pack_file,
    finalize_reviewed_yomi_file,
    group_consecutive_target_overrides,
    harvest_yomi_finalization_artifacts_file,
    rendered_yomi_ruby_tokens,
    rendered_yomi_with_review_defaults,
    replay_review_submissions,
    manual_correction_required,
    materialize_yomi_review_units_file,
    normalize_correction_yomi_tokens,
    parse_rendered_pairs,
    store_review_submission,
    target_group_rejected_span,
    validate_finalized_correction_reading,
    validate_finalized_correction_rendered_yomi,
)


class YomiFinalReviewTests(unittest.TestCase):
    def test_parse_rendered_pairs_preserves_unicode_source_whitespace(self) -> None:
        self.assertEqual(
            parse_rendered_pairs("著/チョ \u2009/\u2009 『/『"),
            [("著", "チョ"), ("\u2009", "\u2009"), ("『", "『")],
        )

    def test_review_item_derives_alternate_inflected_dictionary_reading(self) -> None:
        unit = {
            "unit_id": "u-draw",
            "doc_id": "d-draw",
            "text": "で描いて",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "で/デ 描い/エガイ て/テ",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "で",
                                    "pos": "助詞,格助詞,*,*,*,*",
                                    "dictionary_form": "で",
                                    "reading": "デ",
                                },
                                {
                                    "surface": "描い",
                                    "pos": "動詞,一般,*,*,五段-カ行,連用形-イ音便",
                                    "dictionary_form": "描く",
                                    "reading": "エガイ",
                                },
                                {
                                    "surface": "て",
                                    "pos": "助詞,接続助詞,*,*,*,*",
                                    "dictionary_form": "て",
                                    "reading": "テ",
                                },
                            ]
                        },
                    }
                },
                "safety": {
                    "yomi": {
                        "targets": [
                            {
                                "item_id": "u-draw:r0001c01",
                                "surface": "描",
                                "token_surface": "描い",
                                "target_start": 1,
                                "target_end": 2,
                                # Numeric regrouping can leave this index stale.
                                "token_index": 0,
                                "chunk_index": 0,
                                "current_reading": "エガ",
                                "current_reading_hiragana": "えが",
                                "is_safe": True,
                                "review_status": "safe",
                                "highlight_level": "none",
                                "accepted_signal_names": [],
                                "signals": [],
                            }
                        ]
                    }
                },
            },
        }

        with patch(
            "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
            return_value={
                "描い": ("えがい",),
                "描く": ("えがく", "かく"),
            },
        ):
            item = build_review_item(unit, seq=1, doc_seq=1, track_doc_seq=1)

        span = item["interaction_spans"][0]
        self.assertEqual(span["surface"], "描い")
        self.assertEqual(
            [candidate["reading"] for candidate in span["candidates"]],
            ["えがい", "かい", None],
        )
        alternate = span["candidates"][1]
        self.assertEqual(
            alternate["ruby_nodes"],
            [
                {"type": "ruby", "text": "描", "reading": "か"},
                {"type": "text", "text": "い"},
            ],
        )

    def test_review_item_appends_okurigana_to_stem_reading_candidate(self) -> None:
        payload = {
            "unit_id": "u-good",
            "doc_id": "d-good",
            "text": "良い",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "良い/ヨイ",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "良い",
                                    "pos": "形容詞,一般,*,*,形容詞,連体形-一般",
                                    "dictionary_form": "良い",
                                    "reading": "ヨイ",
                                }
                            ]
                        },
                    }
                },
                "safety": {
                    "yomi": {
                        "targets": [
                            {
                                "item_id": "u-good:r0001c01",
                                "surface": "良",
                                "token_surface": "良い",
                                "target_start": 0,
                                "target_end": 1,
                                "token_index": 0,
                                "chunk_index": 0,
                                "current_reading": "ヨ",
                                "current_reading_hiragana": "よ",
                                "is_safe": False,
                                "review_status": "unresolved",
                                "highlight_level": "target",
                                "accepted_signal_names": [],
                                "signals": [],
                            }
                        ]
                    }
                },
            },
        }

        with patch(
            "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
            return_value={"良": ("い",), "良い": ("いい", "よい")},
        ):
            item = build_review_item(payload, seq=1, doc_seq=1, track_doc_seq=1)

        span = item["interaction_spans"][0]
        self.assertEqual(
            [(candidate["reading"], candidate["tokens"]) for candidate in span["candidates"]],
            [
                ("よい", [["良い", "ヨイ"]]),
                ("いい", [["良い", "イイ"]]),
                (None, []),
            ],
        )
        self.assertEqual(
            span["candidates"][1]["ruby_nodes"],
            [
                {"type": "ruby", "text": "良", "reading": "い"},
                {"type": "text", "text": "い"},
            ],
        )

    def test_review_item_uses_canonical_bounds_over_stale_token_surface(self) -> None:
        unit = {
            "unit_id": "u-stale-token",
            "doc_id": "d-stale-token",
            "text": "使い方",
            "analysis": {
                "mechanical": {"yomi": {"rendered": "使い/ツカイ 方/カタ"}},
                "safety": {
                    "yomi": {
                        "targets": [
                            {
                                "item_id": "u-stale-token:r0001c01",
                                "surface": "使",
                                "token_surface": "使い方",
                                "target_start": 0,
                                "target_end": 1,
                                "token_index": 0,
                                "current_reading": "ツカ",
                                "current_reading_hiragana": "つか",
                                "is_safe": True,
                            },
                            {
                                "item_id": "u-stale-token:r0001c02",
                                "surface": "方",
                                "token_surface": "方",
                                "target_start": 2,
                                "target_end": 3,
                                "token_index": 1,
                                "current_reading": "カタ",
                                "current_reading_hiragana": "かた",
                                "is_safe": True,
                            },
                        ]
                    }
                },
            },
        }

        item = build_review_item(unit, seq=1, doc_seq=1, track_doc_seq=1)

        self.assertEqual(
            [
                (span["surface"], span["target_start"], span["target_end"])
                for span in item["interaction_spans"]
            ],
            [("使い", 0, 2), ("方", 2, 3)],
        )

    def test_finalization_writes_confirmed_skips_only_to_tombstone_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "reviewed.jsonl"
            final = root / "final.jsonl"
            skipped = root / "skipped.jsonl"
            summary_path = root / "summary.json"
            strong_summary = root / "strong.json"
            strong_summary.write_text('{"queued_items":0}\n', encoding="utf-8")
            units.write_text(
                json.dumps(
                    {
                        "unit_id": "u-skip",
                        "text": "MeguruQuruwa",
                        "analysis": {
                            "human_review": {
                                "yomi_final": {"reviewed": True, "skip": True}
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = finalize_reviewed_yomi_file(
                units_jsonl=units,
                strong_queue_summary_json=strong_summary,
                output_jsonl=final,
                skipped_output_jsonl=skipped,
                summary_json=summary_path,
            )

            self.assertEqual(final.read_text(encoding="utf-8"), "")
            self.assertIn("u-skip", skipped.read_text(encoding="utf-8"))
            self.assertEqual(summary["skipped_units"], 1)

    def test_finalization_writes_content_free_exclusion_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = root / "reviewed.jsonl"
            final = root / "final.jsonl"
            skipped = root / "skipped.jsonl"
            excluded = root / "excluded.jsonl"
            summary_path = root / "summary.json"
            strong_summary = root / "strong.json"
            strong_summary.write_text('{"queued_items":0}\n', encoding="utf-8")
            units.write_text(
                json.dumps(
                    {
                        "doc_id": "doc-sensitive",
                        "track_doc_seq": 13,
                        "unit_id": "doc-sensitive:u0001",
                        "unit_seq": 1,
                        "text": "sensitive source text",
                        "source_file": "private.jsonl",
                        "analysis": {
                            "mechanical": {"yomi": {"rendered": "機密/キミツ"}},
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "disposition": "Exclude",
                                    "submission_id": "review-13",
                                    "generated_at_epoch": 123,
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = finalize_reviewed_yomi_file(
                units_jsonl=units,
                strong_queue_summary_json=strong_summary,
                output_jsonl=final,
                skipped_output_jsonl=skipped,
                excluded_output_jsonl=excluded,
                summary_json=summary_path,
            )

            tombstone = json.loads(excluded.read_text(encoding="utf-8"))
            self.assertEqual(final.read_text(encoding="utf-8"), "")
            self.assertEqual(skipped.read_text(encoding="utf-8"), "")
            self.assertEqual(summary["excluded_units"], 1)
            self.assertEqual(tombstone["tombstone_label"], "Removed")
            self.assertEqual(tombstone["confirmation_submission_id"], "review-13")
            for forbidden in ("text", "source_file", "analysis", "rendered_yomi"):
                self.assertNotIn(forbidden, tombstone)

    def test_replay_supports_exclude_and_legacy_skip(self) -> None:
        pack = {
            "items": [
                {"item_id": "u1", "seq": 1, "scope_default": "Keep", "targets": []},
                {"item_id": "u2", "seq": 2, "scope_default": "Keep", "targets": []},
            ]
        }
        submissions = [
            {
                "submission_id": "s1",
                "terminal_exclusion_confirmation": {
                    "confirmed": True,
                    "item_ids": ["u1"],
                },
                "reviewed_ranges": [{"from_seq": 1, "to_seq": 2}],
                "overrides": [
                    {"item_id": "u1", "disposition": "Exclude"},
                    {"item_id": "u2", "skip": True},
                ],
            }
        ]

        effective = replay_review_submissions(pack, submissions)

        self.assertEqual(effective["u1"]["disposition"], "Exclude")
        self.assertTrue(effective["u1"]["skip"])
        self.assertTrue(effective["u1"]["terminal_exclusion_confirmed"])
        self.assertEqual(effective["u2"]["disposition"], "Skip")
        self.assertTrue(effective["u2"]["skip"])

    def test_replay_does_not_confirm_exclusion_from_range_alone(self) -> None:
        pack = {
            "items": [
                {
                    "item_id": "u1",
                    "seq": 1,
                    "scope_default": "Exclude",
                    "targets": [],
                }
            ]
        }

        effective = replay_review_submissions(
            pack,
            [{"submission_id": "s1", "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}]}],
        )

        self.assertEqual(effective["u1"]["disposition"], "Exclude")
        self.assertFalse(effective["u1"]["terminal_exclusion_confirmed"])

    def test_automatic_no_ruby_default_is_not_a_human_rejection(self) -> None:
        target = {
            "item_id": "u1:r1",
            "surface": "二〇〇二",
            "token_surface": "二〇〇二",
            "current_reading_hiragana": "にれいれいに",
            "default_choice_source": "none",
            "default_reading": None,
        }

        rows = default_target_rows({"targets": [target]})
        override = build_target_override(rows[0], {"u1:r1": target})

        self.assertTrue(override["automatic_default"])
        self.assertFalse(override["accepted_no_ruby"])
        self.assertNotIn("rejected_readings", override)

    def test_rule_accepted_no_ruby_default_is_recorded_separately(self) -> None:
        target = {
            "item_id": "u1:r1",
            "surface": "二〇〇二",
            "token_surface": "二〇〇二",
            "current_reading_hiragana": "にれいれいに",
            "default_choice_source": "none",
            "default_reading": None,
            "signals": [
                {
                    "name": "safe_numeric_no_ruby",
                    "accepted": True,
                    "preferred_choice_source": "none",
                }
            ],
        }

        rows = default_target_rows({"targets": [target]})
        override = build_target_override(rows[0], {"u1:r1": target})

        self.assertTrue(override["automatic_default"])
        self.assertTrue(override["accepted_no_ruby"])
        self.assertNotIn("rejected_readings", override)

    def test_explicit_unresolved_choice_overrides_accepted_no_ruby_default(self) -> None:
        target = build_review_target(
            {
                "item_id": "u1:r1",
                "surface": "七五三",
                "token_surface": "七五三",
                "current_reading_hiragana": None,
                "default_choice_source": "none",
                "default_reading": None,
                "allows_intentional_no_ruby": True,
                "signals": [
                    {
                        "name": "safe_by_no_ruby_numeric_surface",
                        "accepted": True,
                        "preferred_choice_source": "none",
                    }
                ],
            }
        )

        override = build_target_override(
            {
                "item_id": "u1:r1",
                "choice_id": "none",
                "choice_source": "none",
                "selected_reading": None,
            },
            {"u1:r1": target},
        )

        self.assertFalse(override["accepted_no_ruby"])
        self.assertEqual(override["no_ruby_state"], "unresolved")

    def test_fallback_no_ruby_default_queues_strong_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "INOSHIRUです。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "INOSHIRU/INOSHIRU です/デス 。/。"}
                            },
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": False,
                                    "target_overrides": [
                                        {
                                            "item_id": "u1:s0001-0008",
                                            "choice_source": "none",
                                            "selected_reading": None,
                                            "surface": "INOSHIRU",
                                            "token_surface": "INOSHIRU",
                                            "token_index": 0,
                                            "target_start": 0,
                                            "target_end": 8,
                                            "automatic_default": True,
                                            "accepted_no_ruby": False,
                                        }
                                    ],
                                    "span_overrides": [],
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_strong_repair_queue_file(
                units_jsonl=units_path,
                output_jsonl=queue_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary["queued_items"], 1)
            self.assertEqual(summary["target_escalations"], 1)
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["rejected_span"], "INOSHIRU")

    def test_finalization_normalizes_stale_parenthesized_laughter_token(self) -> None:
        unit = {
            "unit_id": "u-laughter",
            "text": "面白い（笑）。",
            "analysis": {
                "mechanical": {
                    "yomi": {"rendered": "面白い/オモシロイ （笑）/（笑） 。/。"}
                },
                "human_review": {"yomi_final": {"reviewed": True}},
            },
        }

        canonicalize_finalized_unit_yomi(unit)

        self.assertEqual(
            unit["analysis"]["mechanical"]["yomi"]["tokens"],
            [["面白い", "オモシロイ"], ["（", "（"], ["笑", "ワライ"], ["）", "）"], ["。", "。"]],
        )

    def test_finalization_restores_legacy_whitespace_kaomoji_token(self) -> None:
        unit = {
            "unit_id": "u-kaomoji-space",
            "text": "参加(^ ^)方法",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "参加/サンカ (/( ^/ ^/カオモジ )/) 方法/ホウホウ",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "^\u00a0^",
                                    "pos": "補助記号,ＡＡ,顔文字,*,*,*",
                                    "reading": "キゴウ",
                                }
                            ]
                        },
                    }
                },
                "human_review": {"yomi_final": {"reviewed": True}},
            },
        }

        canonicalize_finalized_unit_yomi(unit)

        self.assertEqual(
            unit["analysis"]["mechanical"]["yomi"]["tokens"],
            [["参加", "サンカ"], ["(", "("], ["^ ^", "カオモジ"], [")", ")"], ["方法", "ホウホウ"]],
        )

    def test_finalization_restores_empty_surface_compatibility_expansion(self) -> None:
        unit = {
            "unit_id": "u-parenthesized-number",
            "text": "⑴環境",
            "analysis": {
                "mechanical": {
                    "yomi": {"rendered": "⑴/⑴ /イチ / 環境/カンキョウ"}
                },
                "human_review": {"yomi_final": {"reviewed": True}},
            },
        }

        canonicalize_finalized_unit_yomi(unit)

        self.assertEqual(
            unit["analysis"]["mechanical"]["yomi"]["tokens"],
            [["⑴", "⑴"], ["環境", "カンキョウ"]],
        )

    def test_review_item_keeps_japanese_numeral_run_without_ruby(self) -> None:
        targets = []
        for index, start in enumerate((0, 3), start=1):
            targets.append(
                {
                    "item_id": f"u1:r{index}",
                    "surface": "二",
                    "token_surface": "二",
                    "target_start": start,
                    "target_end": start + 1,
                    "token_index": index - 1,
                    "chunk_index": 0,
                    "current_reading": "ニ",
                    "current_reading_hiragana": "に",
                    "is_safe": True,
                    "review_status": "safe",
                    "highlight_level": "none",
                    "accepted_signal_names": ["safe_by_no_ruby_numeric_surface"],
                    "signals": [
                        {
                            "name": "safe_by_no_ruby_numeric_surface",
                            "accepted": True,
                            "preferred_choice_source": "none",
                        }
                    ],
                }
            )
        item = build_review_item(
            {
                "unit_id": "u1",
                "doc_id": "d1",
                "text": "二〇〇二年",
                "analysis": {
                    "mechanical": {"yomi": {"rendered": "二〇〇二/ 年/ネン"}},
                    "safety": {"yomi": {"targets": targets}},
                },
            },
            seq=1,
            doc_seq=1,
            track_doc_seq=1,
        )

        self.assertEqual(item["rendered_yomi"], "二〇〇二/ 年/ネン")
        numeral_segments = [
            segment
            for segment in item["ruby_segments"]
            if segment.get("text") in {"二", "〇〇", "二〇〇二"}
        ]
        self.assertTrue(numeral_segments)
        self.assertTrue(all(not segment.get("reading") for segment in numeral_segments))
        span = next(span for span in item["interaction_spans"] if span["surface"] == "二〇〇二")
        self.assertEqual(span["default_candidate_id"], "accepted_none")
        self.assertEqual(
            [candidate["id"] for candidate in span["candidates"] if candidate["source"] == "none"],
            ["none", "accepted_none"],
        )

    def test_unknown_japanese_numeral_run_is_clickable_with_dictionary_candidates(self) -> None:
        with patch(
            "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
            return_value={"七五三": ("しちごさん", "しめ")},
        ):
            item = build_review_item(
                {
                    "unit_id": "u1",
                    "doc_id": "d1",
                    "text": "七五三",
                    "analysis": {
                        "mechanical": {"yomi": {"rendered": "七五三/"}},
                        "safety": {"yomi": {"targets": []}},
                    },
                },
                seq=1,
                doc_seq=1,
                track_doc_seq=1,
            )

        self.assertEqual(len(item["interaction_spans"]), 1)
        span = item["interaction_spans"][0]
        self.assertEqual(span["surface"], "七五三")
        self.assertEqual(span["default_candidate_id"], "accepted_none")
        self.assertEqual(
            [(candidate["id"], candidate["reading"]) for candidate in span["candidates"]],
            [
                ("dictionary:0", "しちごさん"),
                ("dictionary:1", "しめ"),
                ("none", None),
                ("accepted_none", None),
            ],
        )

    def test_build_review_item_reads_only_laugh_inside_parentheses(self) -> None:
        for surface, start, end in (("（笑）", 3, 6), ("笑", 4, 5)):
            with self.subTest(surface=surface):
                unit = {
                    "unit_id": "u1",
                    "doc_id": "d1",
                    "text": "面白い（笑）。",
                    "analysis": {
                        "mechanical": {
                            "yomi": {"rendered": "面白い/オモシロイ （笑）/（笑） 。/。"}
                        },
                        "safety": {
                            "yomi": {
                                "targets": [
                                    {
                                        "item_id": "u1:r1",
                                        "surface": surface,
                                        "token_surface": "（笑）",
                                        "target_start": start,
                                        "target_end": end,
                                        "token_index": 1,
                                        "current_reading": "（笑）",
                                        "current_reading_hiragana": "（笑）",
                                        "is_safe": False,
                                        "signals": [
                                            {
                                                "name": "safe_by_llm_match",
                                                "accepted": False,
                                                "status": "mismatched",
                                                "llm_reading": "かっこわらい",
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    },
                }

                item = build_review_item(unit, seq=1, doc_seq=1, track_doc_seq=1)

                self.assertEqual(
                    item["rendered_yomi"],
                    "面白い/オモシロイ （/（ 笑/ワライ ）/） 。/。",
                )
                laughter_span = next(
                    span for span in item["interaction_spans"] if span["surface"] == "笑"
                )
                self.assertEqual(
                    (laughter_span["target_start"], laughter_span["target_end"]),
                    (4, 5),
                )
                self.assertEqual(laughter_span["default_reading"], "わらい")
                self.assertTrue(laughter_span["is_safe"])

    def test_groups_adjacent_mixed_script_targets_as_exact_source_span(self) -> None:
        targets = [
            {
                "item_id": "u1:r0001c01",
                "surface": "八",
                "token_index": 0,
                "chunk_index": 0,
                "target_start": 0,
                "target_end": 1,
            },
            {
                "item_id": "u1:r0002c01",
                "surface": "島",
                "token_index": 1,
                "chunk_index": 0,
                "target_start": 1,
                "target_end": 2,
            },
            {
                "item_id": "u1:r0002c02",
                "surface": "原",
                "token_index": 1,
                "chunk_index": 1,
                "target_start": 3,
                "target_end": 4,
            },
        ]

        groups = group_consecutive_target_overrides(targets)

        self.assertEqual(len(groups), 1)
        for text, expected in (
            ("八島ヶ原湿原", "八島ヶ原"),
            ("八島ケ原湿原", "八島ケ原"),
        ):
            with self.subTest(text=text):
                self.assertEqual(target_group_rejected_span(text, groups[0]), expected)

    def test_groups_targets_across_variation_selector(self) -> None:
        text = "禰󠄀豆子"
        targets = [
            {
                "item_id": "u1:s0001-0001",
                "surface": "禰",
                "token_index": 0,
                "chunk_index": 0,
            },
            {
                "item_id": "u1:s0003-0004",
                "surface": "豆子",
                "token_index": 2,
                "chunk_index": 0,
            },
        ]

        groups = group_consecutive_target_overrides(targets, text=text)

        self.assertEqual(len(groups), 1)
        self.assertEqual(target_group_rejected_span(text, groups[0]), text)

    def test_reconstructs_mixed_script_span_without_absolute_offsets(self) -> None:
        yashima_targets = [
            {
                "surface": "八",
                "token_surface": "八",
                "token_index": 20,
                "chunk_index": 0,
            },
            {
                "surface": "島",
                "token_surface": "島ヶ原",
                "token_index": 21,
                "chunk_index": 0,
            },
            {
                "surface": "原",
                "token_surface": "島ヶ原",
                "token_index": 21,
                "chunk_index": 1,
            },
        ]
        utsukushigahara_targets = [
            {
                "surface": "美",
                "token_surface": "美ヶ原",
                "token_index": 4,
                "chunk_index": 0,
            },
            {
                "surface": "原",
                "token_surface": "美ヶ原",
                "token_index": 4,
                "chunk_index": 1,
            },
        ]

        self.assertEqual(
            target_group_rejected_span("八島ヶ原湿原", yashima_targets),
            "八島ヶ原",
        )
        self.assertEqual(
            target_group_rejected_span("美ヶ原高原", utsukushigahara_targets),
            "美ヶ原",
        )

    def test_reconstructs_trailing_kana_as_part_of_repair_span(self) -> None:
        targets = [
            {
                "surface": "後払",
                "token_surface": "後払い",
                "token_index": 30,
                "chunk_index": 0,
            }
        ]

        self.assertEqual(
            target_group_rejected_span("後払い・抱え車", targets),
            "後払い",
        )

    def test_applies_whole_mixed_script_span_over_multiple_tokens(self) -> None:
        targets = [
            {
                "surface": "八",
                "token_index": 0,
                "chunk_index": 0,
                "target_start": 0,
                "target_end": 1,
                "rejected_readings": [{"surface": "八", "reading": "や"}],
            },
            {
                "surface": "島",
                "token_index": 1,
                "chunk_index": 0,
                "target_start": 1,
                "target_end": 2,
                "rejected_readings": [{"surface": "島", "reading": "しま"}],
            },
            {
                "surface": "原",
                "token_index": 1,
                "chunk_index": 1,
                "target_start": 3,
                "target_end": 4,
                "rejected_readings": [{"surface": "原", "reading": "はら"}],
            },
        ]
        payload = {
            "text": "八島ヶ原湿原です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "八/ヤ 島ヶ原/シマガハラ 湿原/シツゲン です/デス 。/。"
                    }
                }
            },
        }

        result = apply_target_group_strong_repair(
            payload,
            {
                "item_id": "u1::target_group:1",
                "rejected_span": "八島ヶ原",
                "rendered_yomi": "八/ヤ 島ヶ原/シマガハラ 湿原/シツゲン です/デス 。/。",
                "target_escalations": targets,
            },
            {
                "parsed": [
                    {"surface": "八島ヶ原", "reading": "やしまがはら"},
                ]
            },
        )

        self.assertEqual(result["status"], "applied")
        self.assertIn(
            "八島ヶ原/ヤシマガハラ 湿原/シツゲン",
            payload["analysis"]["mechanical"]["yomi"]["rendered"],
        )

    def test_strong_repair_preserves_source_whitespace_as_token_boundaries(self) -> None:
        payload = {
            "text": "The last of USです。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "token_schema_version": 1,
                        "tokens": [
                            ["The", "ザ"],
                            [" ", " "],
                            ["last", "ラスト"],
                            [" ", " "],
                            ["of", "オブ"],
                            [" ", " "],
                            ["US", "ユーエス"],
                            ["です", "デス"],
                            ["。", "。"],
                        ],
                    }
                }
            },
        }
        result = apply_target_group_strong_repair(
            payload,
            {
                "item_id": "u1::target_group:1",
                "rejected_span": "The last of US",
                "target_escalations": [
                    {"surface": "The", "token_index": 0, "chunk_index": 0},
                    {"surface": "last", "token_index": 2, "chunk_index": 0},
                    {"surface": "of", "token_index": 4, "chunk_index": 0},
                    {"surface": "US", "token_index": 6, "chunk_index": 0},
                ],
            },
            {
                "parsed": [
                    {"surface": "The", "reading": "ざ"},
                    {"surface": "last", "reading": "らすと"},
                    {"surface": "of", "reading": "おぶ"},
                    {"surface": "US", "reading": "あす"},
                ]
            },
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            payload["analysis"]["mechanical"]["yomi"]["tokens"][:7],
            [
                ["The", "ザ"],
                [" ", ""],
                ["last", "ラスト"],
                [" ", ""],
                ["of", "オブ"],
                [" ", ""],
                ["US", "アス"],
            ],
        )

    def test_strong_repair_clears_reading_from_new_numeric_only_segment(self) -> None:
        payload = {
            "text": "BGM8選です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "token_schema_version": 1,
                        "tokens": [
                            ["BGM8", "ビージーエムハチ"],
                            ["選", "セン"],
                            ["です", "デス"],
                            ["。", "。"],
                        ],
                    }
                }
            },
        }

        result = apply_target_group_strong_repair(
            payload,
            {
                "item_id": "u1::target_group:1",
                "rejected_span": "BGM8",
                "target_escalations": [
                    {
                        "surface": "BGM8",
                        "token_index": 0,
                        "chunk_index": 0,
                        "rejected_readings": [
                            {"surface": "BGM8", "reading": "びーじーえむはち"}
                        ],
                    }
                ],
            },
            {
                "parsed": [
                    {"surface": "BGM", "reading": "びーじーえむ"},
                    {"surface": "8", "reading": "はち"},
                ]
            },
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            payload["analysis"]["mechanical"]["yomi"]["tokens"][:2],
            [["BGM", "ビージーエム"], ["8", ""]],
        )

    def test_manual_strong_repair_failure_identifies_document_and_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            units = Path(tmp) / "units.jsonl"
            units.write_text(
                json.dumps(
                    {
                        "doc_id": "doc1",
                        "unit_id": "u1",
                        "analysis": {
                            "mechanical": {"yomi": {"rendered": "(笑)/ワライ"}}
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            pack = {
                "items": [
                    {
                        "item_id": "u1::strong_repair",
                        "doc_id": "doc1",
                        "regions": [
                            {
                                "region_id": "u1::region:1",
                                "unit_id": "u1",
                                "rejected_span": "(笑)",
                            }
                        ],
                    }
                ]
            }
            effective = {
                "u1::strong_repair": {
                    "submission_id": "submission-1",
                    "regions": [
                        {
                            "region_id": "u1::region:1",
                            "manual_segments": [
                                {"surface": "笑", "reading": "わらい"},
                            ],
                        }
                    ],
                }
            }

            result = apply_manual_strong_repair_review_segments_file(
                pack=pack,
                effective=effective,
                units_jsonl=units,
            )

            self.assertEqual(result["invalid_items"], 1)
            self.assertEqual(result["invalid"][0]["doc_id"], "doc1")
            self.assertEqual(result["invalid"][0]["unit_id"], "u1")
            self.assertEqual(result["invalid"][0]["submission_id"], "submission-1")

    def test_manual_strong_repair_disambiguates_repeated_stale_token_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            units = Path(tmp) / "units.jsonl"
            units.write_text(
                json.dumps(
                    {
                        "doc_id": "doc1",
                        "unit_id": "u1",
                        "text": "1kgと2kg。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "tokens": [
                                        ["1", ""],
                                        ["kg", "キログラム"],
                                        ["と", "ト"],
                                        ["2", ""],
                                        ["kg", "キログラム"],
                                        ["。", "。"],
                                    ]
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            reference = "1/ / kg/キログラム と/ト / 2/ / kg/キログラム 。/。"
            regions = [
                {
                    "region_id": "r1",
                    "unit_id": "u1",
                    "rejected_span": "kg",
                    "rendered_yomi_before": reference,
                    "target_escalations": [
                        {"surface": "kg", "token_surface": "kg", "token_index": 2}
                    ],
                },
                {
                    "region_id": "r2",
                    "unit_id": "u1",
                    "rejected_span": "kg",
                    "rendered_yomi_before": reference,
                    "target_escalations": [
                        {"surface": "kg", "token_surface": "kg", "token_index": 7}
                    ],
                },
            ]
            result = apply_manual_strong_repair_review_segments_file(
                pack={
                    "items": [
                        {
                            "item_id": "u1::strong_repair",
                            "doc_id": "doc1",
                            "regions": regions,
                        }
                    ]
                },
                effective={
                    "u1::strong_repair": {
                        "submission_id": "s1",
                        "regions": [
                            {
                                "region_id": region["region_id"],
                                "manual_segments": [{"surface": "kg", "reading": "きろ"}],
                            }
                            for region in regions
                        ],
                    }
                },
                units_jsonl=units,
            )

            self.assertEqual(result["applied_items"], 2)
            repaired = json.loads(units.read_text(encoding="utf-8"))
            self.assertEqual(
                [token for token in repaired["analysis"]["mechanical"]["yomi"]["tokens"] if token[0] == "kg"],
                [["kg", "キロ"], ["kg", "キロ"]],
            )

    def test_manual_strong_repair_accepts_non_whitelisted_numeric_compound_reading(self) -> None:
        unit = {
            "unit_id": "u1",
            "text": "1日現在",
            "analysis": {
                "mechanical": {
                    "yomi": {"tokens": [["1日", "ツイタチ"], ["現在", "ゲンザイ"]]}
                }
            },
        }

        result = apply_manual_strong_repair_segments(
            unit,
            {"item_id": "r1", "rejected_span": "1日"},
            {"manual_segments": [{"surface": "1日", "reading": "かずひ"}]},
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            unit["analysis"]["mechanical"]["yomi"]["tokens"][0],
            ["1日", "カズヒ"],
        )

    def test_manual_strong_repair_may_replace_full_token_around_rejected_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            units = Path(tmp) / "units.jsonl"
            units.write_text(
                json.dumps(
                    {
                        "doc_id": "doc1",
                        "unit_id": "u1",
                        "text": "予防疲れ。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "tokens": [
                                        ["予防", "ヨボウ"],
                                        ["疲れ", "ツカレ"],
                                        ["。", "。"],
                                    ]
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            region = {
                "region_id": "r1",
                "unit_id": "u1",
                "rejected_span": "疲",
                "rendered_yomi_before": "予防/ヨボウ 疲れ/ツカレ 。/。",
                "target_escalations": [
                    {"surface": "疲", "token_surface": "疲れ", "token_index": 1}
                ],
            }
            result = apply_manual_strong_repair_review_segments_file(
                pack={
                    "items": [
                        {
                            "item_id": "u1::strong_repair",
                            "doc_id": "doc1",
                            "regions": [region],
                        }
                    ]
                },
                effective={
                    "u1::strong_repair": {
                        "submission_id": "s1",
                        "regions": [
                            {
                                "region_id": "r1",
                                "manual_segments": [
                                    {"surface": "疲れ", "reading": "づかれ"}
                                ],
                            }
                        ],
                    }
                },
                units_jsonl=units,
            )

            self.assertEqual(result["applied_items"], 1)
            repaired = json.loads(units.read_text(encoding="utf-8"))
            self.assertIn(
                ["疲れ", "ヅカレ"],
                repaired["analysis"]["mechanical"]["yomi"]["tokens"],
            )

    def test_finalized_correction_accepts_kana_reading_for_fullwidth_latin(self) -> None:
        self.assertEqual(
            validate_finalized_correction_reading("ＵＦＯ", "ユーフォー"),
            {"ok": True},
        )

    def test_finalized_correction_accepts_symbolic_kaomoji_marker(self) -> None:
        self.assertEqual(
            validate_finalized_correction_reading("（●＾o＾●）", "カオモジ"),
            {"ok": True},
        )
        self.assertEqual(
            validate_finalized_correction_reading("（ノ∀｀）", "カオモジ"),
            {"ok": True},
        )
        self.assertEqual(
            validate_finalized_correction_reading("★彡", "カオモジ"),
            {"ok": True},
        )
        self.assertFalse(
            validate_finalized_correction_reading("（笑）", "カオモジ")["ok"],
        )

    def test_finalized_correction_preserves_numeric_compound_reading(self) -> None:
        self.assertEqual(
            validate_finalized_correction_reading("2つ", "フタツ"),
            {"ok": True},
        )
        self.assertEqual(
            validate_finalized_correction_reading("1日", "カズヒ"),
            {"ok": True},
        )
        self.assertFalse(
            validate_finalized_correction_reading("2つ", "2ツ")["ok"],
        )
        self.assertFalse(
            validate_finalized_correction_reading("1日", "")["ok"],
        )

    def test_finalized_correction_accepts_formatted_number_without_reading(self) -> None:
        self.assertTrue(
            validate_finalized_correction_reading("2,035.28", "")["ok"]
        )
        self.assertFalse(
            validate_finalized_correction_reading("2,035.28", "ニセン")["ok"]
        )

    def test_finalized_correction_allows_optional_japanese_numeral_reading(self) -> None:
        self.assertEqual(
            validate_finalized_correction_reading("二〇〇二", ""),
            {"ok": True},
        )
        self.assertEqual(
            validate_finalized_correction_reading("二〇〇二", "ニセンニ"),
            {"ok": True},
        )
        self.assertFalse(validate_finalized_correction_reading("一三", "13")["ok"])
        self.assertFalse(validate_finalized_correction_reading("Ⅲ", "サン")["ok"])

    def test_finalized_correction_treats_white_circle_placeholders_as_symbols(self) -> None:
        self.assertEqual(
            validate_finalized_correction_reading("○○", "○○"),
            {"ok": True},
        )
        self.assertEqual(
            validate_finalized_correction_reading("〇〇", "〇〇"),
            {"ok": True},
        )
        self.assertFalse(validate_finalized_correction_reading("○○", "")["ok"])
        self.assertEqual(
            validate_finalized_correction_reading("一○", ""),
            {"ok": True},
        )

    @patch("yomi_corpus.yomi.final_review.load_final_review_surface_readings")
    def test_finalization_repairs_invalid_reading_from_unique_trusted_lexicon_entry(
        self,
        load_readings,
    ) -> None:
        load_readings.return_value = {"𠮟": ("しか",)}
        unit = {
            "unit_id": "u1",
            "text": "𠮟られる。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "token_schema_version": 1,
                        "tokens": [["𠮟", "𠮟"], ["られる", "ラレル"], ["。", "。"]],
                    }
                }
            },
        }

        canonicalize_finalized_unit_yomi(unit)

        self.assertEqual(
            unit["analysis"]["mechanical"]["yomi"]["tokens"][0],
            ["𠮟", "シカ"],
        )

    def test_finalized_correction_normalizes_optional_numeral_reading_to_katakana(self) -> None:
        self.assertEqual(
            normalize_correction_yomi_tokens([["一二三", "ひふみ"]]),
            [["一二三", "ヒフミ"]],
        )

    def test_finalized_correction_requires_reading_for_single_lexical_numeral(self) -> None:
        self.assertEqual(
            validate_finalized_correction_reading("七", "ナナ"),
            {"ok": True},
        )
        self.assertFalse(validate_finalized_correction_reading("七", "")["ok"])

    def test_finalized_correction_validation_matches_numeric_space_and_laughter_rules(self) -> None:
        self.assertTrue(validate_finalized_correction_reading("五", "ゴ")["ok"])
        self.assertTrue(validate_finalized_correction_reading("　", "")["ok"])
        self.assertTrue(validate_finalized_correction_reading("ｗ", "")["ok"])
        self.assertTrue(validate_finalized_correction_reading("ww", "")["ok"])

    def test_finalized_correction_grandfathers_unchanged_legacy_token_pairs(self) -> None:
        unit = {
            "text": "SULです。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "tokens": [["SUL", "SUL"], ["です", "デス"], ["。", "。"]]
                    }
                }
            },
        }
        unchanged = validate_finalized_correction_rendered_yomi(
            unit=unit,
            proposed="SUL/SUL です/デス 。/。",
        )
        changed = validate_finalized_correction_rendered_yomi(
            unit=unit,
            proposed="SUL/english です/デス 。/。",
        )

        self.assertTrue(unchanged["ok"])
        self.assertFalse(changed["ok"])

        canonicalize_finalized_unit_yomi(
            unit,
            grandfathered_tokens=[["SUL", "SUL"], ["です", "デス"], ["。", "。"]],
        )
        self.assertEqual(
            unit["analysis"]["mechanical"]["yomi"]["tokens"][0],
            ["SUL", "SUL"],
        )

    def test_apply_finalized_correction_preserves_numeric_compound_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            final_jsonl = batch_dir / "units.yomi.final.jsonl"
            final_jsonl.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "doc_id": "doc1",
                        "text": "2つです。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "token_schema_version": 1,
                                    "tokens": [["2", ""], ["つ", "ツ"], ["です", "デス"], ["。", "。"]],
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "numeric_correction",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 1,
                    "units": [
                        {
                            "unit_id": "u1",
                            "text": "2つです。",
                            "original_yomi_tokens": [["2", ""], ["つ", "ツ"], ["です", "デス"], ["。", "。"]],
                            "proposed_yomi_tokens": [["2つ", "フタツ"], ["です", "デス"], ["。", "。"]],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary.json",
            )

            self.assertEqual(summary["applied_count"], 1)
            row = json.loads(final_jsonl.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["tokens"][0],
                ["2つ", "フタツ"],
            )

    def test_apply_finalized_correction_updates_final_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            final_jsonl = batch_dir / "units.yomi.final.jsonl"
            final_jsonl.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "doc_id": "doc1",
                        "text": "今日です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "今日/キョウ です/デス 。/。"}
                            },
                            "human_review": {
                                "manual_correction": {
                                    "required": True,
                                    "events": [{"required": True, "source_stage": "yomi_final_review"}],
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "correction_1",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 1,
                    "units": [
                        {
                            "unit_id": "u1",
                            "text": "今日です。",
                            "original_yomi_tokens": [["今日", "キョウ"], ["です", "デス"], ["。", "。"]],
                            "proposed_yomi_tokens": [["今日", "こんにち"], ["です", "デス"], ["。", "。"]],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary.json",
            )

            self.assertEqual(summary["applied_count"], 1)
            row = json.loads(final_jsonl.read_text(encoding="utf-8").strip())
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"],
                {
                    "token_schema_version": 1,
                    "tokens": [["今日", "コンニチ"], ["です", "デス"], ["。", "。"]],
                },
            )
            self.assertEqual(
                row["analysis"]["human_review"]["finalized_corrections"][0]["submission_id"],
                "correction_1",
            )
            self.assertFalse(manual_correction_required(row))

            second_summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary2.json",
            )

            self.assertEqual(second_summary["applied_count"], 0)
            self.assertEqual(second_summary["batches"][0]["accepted_count"], 1)
            self.assertEqual(second_summary["skipped_count"], 0)

            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "correction_2",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 2,
                    "units": [
                        {
                            "unit_id": "u1",
                            "text": "今日です。",
                            "original_rendered_yomi": "今日/コンニチ です/デス 。/。",
                            "proposed_rendered_yomi": "今日/コンニチ です/デス 。/。",
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )
            acknowledgement_summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary3.json",
            )

            self.assertEqual(acknowledgement_summary["applied_count"], 1)
            acknowledged_row = json.loads(final_jsonl.read_text(encoding="utf-8").strip())
            self.assertEqual(
                [
                    correction["submission_id"]
                    for correction in acknowledged_row["analysis"]["human_review"]["finalized_corrections"]
                ],
                ["correction_1", "correction_2"],
            )

    def test_no_edit_finalized_correction_acknowledges_manual_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            final_jsonl = batch_dir / "units.yomi.final.jsonl"
            final_jsonl.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "doc_id": "doc1",
                        "text": "一三です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "tokens": [["一三", ""], ["です", "デス"], ["。", "。"]]
                                }
                            },
                            "human_review": {
                                "manual_correction": {
                                    "required": True,
                                    "events": [{"required": True, "source_stage": "yomi_final_review"}],
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
            unchanged_tokens = [["一三", ""], ["です", "デス"], ["。", "。"]]
            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "acknowledge_1",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 1,
                    "units": [
                        {
                            "unit_id": "u1",
                            "text": "一三です。",
                            "original_yomi_tokens": unchanged_tokens,
                            "proposed_yomi_tokens": unchanged_tokens,
                            "acknowledgement_only": True,
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary.json",
            )

            self.assertEqual(summary["applied_count"], 1)
            row = json.loads(final_jsonl.read_text(encoding="utf-8").strip())
            self.assertFalse(manual_correction_required(row))
            self.assertEqual(
                row["analysis"]["human_review"]["finalized_corrections"][0]["submission_id"],
                "acknowledge_1",
            )
            self.assertEqual(row["analysis"]["mechanical"]["yomi"]["tokens"], unchanged_tokens)

    def test_finalized_correction_restores_skipped_unit_from_hybrid_yomi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            final_jsonl = batch_dir / "units.yomi.final.jsonl"
            skipped_jsonl = batch_dir / "units.yomi.skipped.jsonl"
            final_jsonl.write_text("", encoding="utf-8")
            skipped_jsonl.write_text(
                json.dumps(
                    {
                        "unit_id": "u-skip",
                        "doc_id": "doc1",
                        "text": "DVです。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "token_schema_version": 1,
                                    "tokens": [["DV", "ディーブイ"], ["です", "デス"], ["。", "。"]],
                                }
                            },
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": True,
                                    "submission_id": "original-skip",
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "restore-1",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 2,
                    "units": [
                        {
                            "unit_id": "u-skip",
                            "text": "DVです。",
                            "skip": False,
                            "original_yomi_tokens": [
                                ["DV", "ディーブイ"],
                                ["です", "デス"],
                                ["。", "。"],
                            ],
                            "proposed_yomi_tokens": [
                                ["DV", "ディーブイ"],
                                ["です", "デス"],
                                ["。", "。"],
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary.json",
            )

            self.assertEqual(summary["applied_count"], 1)
            self.assertEqual(summary["batches"][0]["restored_count"], 1)
            self.assertEqual(skipped_jsonl.read_text(encoding="utf-8"), "")
            restored = json.loads(final_jsonl.read_text(encoding="utf-8"))
            review = restored["analysis"]["human_review"]["yomi_final"]
            self.assertFalse(review["skip"])
            self.assertTrue(review["restored"])
            self.assertEqual(review["restoration_submission_id"], "restore-1")
            self.assertEqual(
                restored["analysis"]["human_review"]["skip_history"][0]["event"],
                "restored",
            )

            repeated = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary2.json",
            )
            self.assertEqual(repeated["applied_count"], 0)
            self.assertEqual(repeated["skipped_count"], 0)

    def test_finalized_correction_moves_final_unit_to_recoverable_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            final_jsonl = batch_dir / "units.yomi.final.jsonl"
            skipped_jsonl = batch_dir / "units.yomi.skipped.jsonl"
            final_jsonl.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "doc_id": "doc1",
                        "text": "本文です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "token_schema_version": 1,
                                    "tokens": [["本文", "ホンブン"], ["です", "デス"], ["。", "。"]],
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "skip-1",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 3,
                    "units": [
                        {
                            "unit_id": "u1",
                            "text": "本文です。",
                            "disposition": "Skip",
                            "skip": True,
                            "original_yomi_tokens": [["本文", "ホンブン"], ["です", "デス"], ["。", "。"]],
                            "proposed_yomi_tokens": [["本文", "ホンブン"], ["です", "デス"], ["。", "。"]],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary.json",
            )

            self.assertEqual(final_jsonl.read_text(encoding="utf-8"), "")
            skipped_row = json.loads(skipped_jsonl.read_text(encoding="utf-8"))
            self.assertEqual(
                skipped_row["analysis"]["human_review"]["yomi_final"]["disposition"],
                "Skip",
            )
            self.assertEqual(summary["batches"][0]["newly_skipped_count"], 1)

            repeated = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary2.json",
            )
            self.assertEqual(repeated["applied_count"], 0)
            self.assertEqual(repeated["skipped_count"], 0)

    def test_finalized_correction_excludes_final_unit_with_content_free_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            final_jsonl = batch_dir / "units.yomi.final.jsonl"
            final_jsonl.write_text(
                json.dumps(
                    {
                        "unit_id": "u-sensitive",
                        "unit_seq": 2,
                        "doc_id": "doc1",
                        "track_doc_seq": 13,
                        "text": "sensitive",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "token_schema_version": 1,
                                    "tokens": [["sensitive", "センシティブ"]],
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store_dir = root / "data" / "review_submissions" / FINALIZED_CORRECTION_STAGE
            store_review_submission(
                {
                    "submission_type": FINALIZED_CORRECTION_SUBMISSION_TYPE,
                    "review_stage": FINALIZED_CORRECTION_STAGE,
                    "submission_id": "exclude-1",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "generated_at_epoch": 4,
                    "units": [
                        {
                            "unit_id": "u-sensitive",
                            "text": "sensitive",
                            "disposition": "Exclude",
                            "skip": True,
                            "original_yomi_tokens": [["sensitive", "センシティブ"]],
                            "proposed_yomi_tokens": [["sensitive", "センシティブ"]],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary.json",
            )

            self.assertEqual(final_jsonl.read_text(encoding="utf-8"), "")
            tombstone = json.loads(
                (batch_dir / "units.yomi.excluded.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(tombstone["confirmation_submission_id"], "exclude-1")
            self.assertEqual(tombstone["tombstone_label"], "Removed")
            self.assertNotIn("text", tombstone)
            self.assertNotIn("analysis", tombstone)
            self.assertEqual(summary["batches"][0]["newly_excluded_count"], 1)

            repeated = apply_finalized_correction_submissions_file(
                root=root,
                submission_store_dir=store_dir,
                track_name="dev",
                summary_json=root / "summary2.json",
            )
            self.assertEqual(repeated["applied_count"], 0)
            self.assertEqual(repeated["skipped_count"], 0)

    def test_rendered_yomi_ruby_tokens_use_python_furigana_alignment(self) -> None:
        tokens = rendered_yomi_ruby_tokens("決め/キメ お金/オカネ")

        self.assertEqual(
            tokens[0]["nodes"],
            [
                {"type": "ruby", "text": "決", "reading": "き"},
                {"type": "text", "text": "め"},
            ],
        )
        self.assertEqual(
            tokens[1]["nodes"],
            [
                {"type": "text", "text": "お"},
                {"type": "ruby", "text": "金", "reading": "かね"},
            ],
        )

    def test_review_item_canonicalizes_literal_slashes_before_rendering(self) -> None:
        item = build_review_item(
            {
                "unit_id": "u-date",
                "doc_id": "d-date",
                "text": "2017/11",
                "analysis": {
                    "mechanical": {"yomi": {"rendered": "2017/ /// 11/"}},
                    "safety": {"yomi": {"targets": []}},
                },
            },
            seq=1,
            doc_seq=1,
            track_doc_seq=1,
        )

        self.assertEqual(item["rendered_yomi"], r"2017/ \//\/ 11/")
        self.assertEqual(
            [token["surface"] for token in item["rendered_yomi_ruby_tokens"]],
            ["2017", "/", "11"],
        )

    def test_rendered_yomi_ruby_tokens_keep_ke_place_name_ruby_on_full_surface(self) -> None:
        tokens = rendered_yomi_ruby_tokens("新鎌ケ谷/シンカマガヤ 鎌ヶ谷駅/カマガヤエキ")

        self.assertEqual(
            tokens[0]["nodes"],
            [{"type": "ruby", "text": "新鎌ケ谷", "reading": "しんかまがや"}],
        )
        self.assertEqual(
            tokens[1]["nodes"],
            [{"type": "ruby", "text": "鎌ヶ谷駅", "reading": "かまがやえき"}],
        )

    def test_rendered_yomi_ruby_tokens_keep_small_ka_counter_in_reading(self) -> None:
        tokens = rendered_yomi_ruby_tokens("２/ ヵ月/カゲツ")

        self.assertEqual(tokens[0]["nodes"], [{"type": "text", "text": "２"}])
        self.assertEqual(
            tokens[1]["nodes"],
            [{"type": "ruby", "text": "ヵ月", "reading": "かげつ"}],
        )

    def test_review_ruby_segments_include_numeric_compounds(self) -> None:
        segments = build_ruby_segments(
            "予約開始は12月2日からです。",
            [],
            rendered_yomi=(
                "予約/ヨヤク 開始/カイシ は/ハ 12/ 月/ガツ "
                "2日/フツカ から/カラ です/デス 。/。"
            ),
        )

        self.assertIn(
            {
                "type": "ruby",
                "text": "2日",
                "reading": "ふつか",
                "display_only": True,
            },
            segments,
        )

    def test_review_ruby_segments_keep_numeric_compounds_before_escaped_whitespace(self) -> None:
        segments = build_ruby_segments(
            "9月5日(火)\u3000展示します。",
            [],
            rendered_yomi=(
                r"9/ 月/ガツ 5日/イツカ (/( 火/カ )/) \u3000/ "
                "展示/テンジ し/シ ます/マス 。/。"
            ),
        )

        self.assertIn(
            {
                "type": "ruby",
                "text": "5日",
                "reading": "いつか",
                "display_only": True,
            },
            segments,
        )

    def test_review_ruby_segments_keep_noninteractive_duration_ruby(self) -> None:
        segments = build_ruby_segments(
            "最高40度、10日間下がる。",
            [
                {
                    "item_id": "u1:highest",
                    "target_start": 0,
                    "target_end": 2,
                    "default_reading": "さいこう",
                    "is_safe": True,
                    "highlight_level": "none",
                },
                {
                    "item_id": "u1:degree",
                    "target_start": 4,
                    "target_end": 5,
                    "default_reading": "ど",
                    "is_safe": True,
                    "highlight_level": "none",
                },
                {
                    "item_id": "u1:fall",
                    "target_start": 10,
                    "target_end": 13,
                    "default_reading": "さがる",
                    "is_safe": True,
                    "highlight_level": "none",
                },
            ],
            rendered_yomi=(
                "最高/サイコウ 40/ 度/ド 、/、 10日/トオカ "
                "間/カン 下がる/サガル 。/。"
            ),
        )

        self.assertIn(
            {
                "type": "ruby",
                "text": "10日",
                "reading": "とおか",
                "display_only": True,
            },
            segments,
        )
        self.assertIn(
            {
                "type": "ruby",
                "text": "間",
                "reading": "かん",
                "display_only": True,
            },
            segments,
        )

    def test_review_item_exposes_numeric_compound_as_editable_safe_target(self) -> None:
        payload = unit("doc1", "u1", "予約開始は12月6日からです。", safe=True)
        payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
            "予約/ヨヤク 開始/カイシ は/ハ 12/ 月/ガツ "
            "6日/ムイカ から/カラ です/デス 。/。"
        )
        payload["analysis"]["safety"]["yomi"]["targets"] = []

        item = build_review_item(payload, seq=1, doc_seq=1, track_doc_seq=1)

        self.assertEqual(len(item["targets"]), 1)
        target = item["targets"][0]
        self.assertEqual(target["surface"], "6日")
        self.assertTrue(target["is_safe"])
        self.assertEqual(target["default_reading"], "むいか")
        self.assertEqual(
            [(candidate["source"], candidate["reading"]) for candidate in target["candidates"]],
            [("current", "むいか"), ("none", None)],
        )
        self.assertEqual(
            target["candidates"][0]["ruby_nodes"],
            [{"type": "ruby", "text": "6日", "reading": "むいか"}],
        )
        numeric_ruby_token = next(
            row for row in item["rendered_yomi_ruby_tokens"] if row["surface"] == "6日"
        )
        self.assertEqual(
            numeric_ruby_token["nodes"],
            [{"type": "ruby", "text": "6日", "reading": "むいか"}],
        )
        self.assertIn(
            {
                "type": "ruby",
                "text": "6日",
                "target_item_id": "u1:s0009-0010",
                "reading": "むいか",
                "is_safe": True,
                "highlight_level": "none",
            },
            item["ruby_segments"],
        )

    def test_review_item_aligns_legacy_target_with_canonical_mixed_numeric_split(self) -> None:
        payload = {
            "doc_id": "doc1",
            "unit_id": "u1",
            "unit_seq": 1,
            "text": "中３生",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "中/チュウ ３/ 生/セイ",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "中３",
                                    "reading": "チュウサン",
                                    "dictionary_form": "中三",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                },
                                {
                                    "surface": "生",
                                    "reading": "セイ",
                                    "dictionary_form": "生",
                                    "pos": "接尾辞,名詞的,一般,*,*,*",
                                },
                            ]
                        },
                    }
                },
                "safety": {
                    "yomi": {
                        "targets": [
                            {
                                "item_id": "u1:r0001c01",
                                "surface": "中",
                                "token_surface": "中３",
                                "target_start": 0,
                                "target_end": 1,
                                "token_index": 0,
                                "chunk_index": 0,
                                "current_reading": "チュウサン",
                                "current_reading_hiragana": "ちゅうさん",
                                "is_safe": False,
                                "review_status": "unresolved",
                                "highlight_level": "target",
                                "accepted_signal_names": [],
                                "signals": [],
                            }
                        ]
                    }
                },
            },
        }

        item = build_review_item(payload, seq=1, doc_seq=1, track_doc_seq=1)

        span = item["interaction_spans"][0]
        self.assertEqual(span["surface"], "中")
        self.assertEqual(span["current_reading_hiragana"], "ちゅう")
        self.assertEqual(span["default_reading"], "ちゅう")
        self.assertEqual(
            item["ruby_segments"][0],
            {
                "type": "ruby",
                "text": "中",
                "target_item_id": "u1:s0001-0001",
                "reading": "ちゅう",
                "is_safe": False,
                "highlight_level": "target",
            },
        )

    def test_interaction_span_includes_okurigana_and_applies_full_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            store_dir = root / "submissions"
            payload = {
                "doc_id": "doc1",
                "unit_id": "u1",
                "unit_seq": 1,
                "text": "後払いです。",
                "analysis": {
                    "mechanical": {"yomi": {"rendered": "後払い/ゴバライ です/デス 。/。"}},
                    "safety": {
                        "yomi": {
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "token_index": 0,
                                    "chunk_index": 0,
                                    "surface": "後払",
                                    "token_surface": "後払い",
                                    "current_reading": "ゴバラ",
                                    "current_reading_hiragana": "ごばら",
                                    "target_start": 0,
                                    "target_end": 2,
                                    "is_safe": False,
                                    "review_status": "unresolved",
                                    "highlight_level": "target",
                                    "accepted_signal_names": [],
                                    "signals": [],
                                }
                            ]
                        }
                    },
                },
            }
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch(
                "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
                return_value={"後払い": ("ゴバライ", "アトバライ")},
            ):
                build_yomi_final_review_pack_file(
                    units_jsonl=units_path,
                    output_json=pack_path,
                    pack_id="pack_1",
                    track_name="dev",
                    batch_name="dev_batch_0001",
                    created_at_epoch=123,
                )

            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            item = pack["items"][0]
            span = item["interaction_spans"][0]
            self.assertEqual(span["surface"], "後払い")
            self.assertEqual(span["legacy_target_item_ids"], ["u1:r0001c01"])
            self.assertEqual(
                [(row["source"], row["reading"]) for row in span["candidates"]],
                [("current", "ごばらい"), ("dictionary", "あとばらい"), ("none", None)],
            )
            self.assertEqual(
                span["candidates"][0]["ruby_nodes"],
                [
                    {"type": "ruby", "text": "後払", "reading": "ごばら"},
                    {"type": "text", "text": "い"},
                ],
            )
            self.assertEqual(item["ruby_segments"][0]["text"], "後払い")
            self.assertEqual(item["ruby_segments"][0]["target_item_id"], span["span_id"])

            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 1,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "targets": [
                                {
                                    "item_id": span["span_id"],
                                    "choice_id": "dictionary:1",
                                    "choice_source": "dictionary",
                                    "selected_reading": "あとばらい",
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )
            result = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=output_path,
                summary_json=summary_path,
            )
            self.assertTrue(result["stage_complete"])
            reviewed = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                reviewed["analysis"]["mechanical"]["yomi"]["rendered"],
                "後払い/アトバライ です/デス 。/。",
            )

    def test_review_target_always_offers_common_kg_readings(self) -> None:
        target = {
            "item_id": "u1:r0001c01",
            "surface": "kg",
            "token_surface": "kg",
            "current_reading": "キログラム",
            "current_reading_hiragana": "きろぐらむ",
            "is_safe": True,
            "signals": [],
        }

        review_target = build_review_target(target)

        self.assertEqual(review_target["default_reading"], "きろぐらむ")
        self.assertEqual(
            [(candidate["source"], candidate["reading"]) for candidate in review_target["candidates"]],
            [
                ("current", "きろぐらむ"),
                ("usage_alternative", "きろ"),
                ("none", None),
            ],
        )

    def test_review_target_offers_kg_readings_for_compatibility_spelling(self) -> None:
        target = {
            "item_id": "u1:r0001c01",
            "surface": "ＫＧ",
            "token_surface": "ＫＧ",
            "current_reading": "キロ",
            "current_reading_hiragana": "きろ",
            "is_safe": True,
            "signals": [],
        }

        review_target = build_review_target(target)

        self.assertEqual(
            [(candidate["source"], candidate["reading"]) for candidate in review_target["candidates"]],
            [
                ("current", "きろ"),
                ("usage_alternative", "きろぐらむ"),
                ("none", None),
            ],
        )

    def test_review_target_offers_formal_km_reading_after_kilo_default(self) -> None:
        target = {
            "item_id": "u1:r0001c01",
            "surface": "km",
            "token_surface": "km",
            "current_reading": "キロ",
            "current_reading_hiragana": "きろ",
            "is_safe": True,
            "signals": [],
        }

        review_target = build_review_target(target)

        self.assertEqual(review_target["default_reading"], "きろ")
        self.assertEqual(
            [
                (candidate["source"], candidate["reading"])
                for candidate in review_target["candidates"]
            ],
            [
                ("current", "きろ"),
                ("dictionary", "きろめーとる"),
                ("none", None),
            ],
        )

    def test_review_target_projects_inflected_token_readings_onto_kanji_target(self) -> None:
        target = {
            "item_id": "u1:r0001c01",
            "surface": "描",
            "token_surface": "描け",
            "current_reading": "エガ",
            "current_reading_hiragana": "えが",
            "is_safe": True,
            "accepted_signal_names": ["safe_by_corpus_frequency"],
            "signals": [
                {
                    "name": "safe_by_corpus_frequency",
                    "accepted": True,
                    "evidence_scope": "token",
                    "dominant": {"reading": "エガケ"},
                }
            ],
        }

        with patch(
            "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
            return_value={"描け": ("えがけ", "かけ")},
        ):
            review_target = build_review_target(target)

        self.assertEqual(
            [(candidate["source"], candidate["reading"]) for candidate in review_target["candidates"]],
            [
                ("current", "えが"),
                ("dictionary", "か"),
                ("none", None),
            ],
        )

    def test_rendered_yomi_ruby_tokens_keep_kana_outside_latin_ruby(self) -> None:
        tokens = rendered_yomi_ruby_tokens("Tシャツ/ティーシャツ ロンT/ロンティー")

        self.assertEqual(
            tokens[0]["nodes"],
            [
                {"type": "ruby", "text": "T", "reading": "てぃー"},
                {"type": "text", "text": "シャツ"},
            ],
        )
        self.assertEqual(
            tokens[1]["nodes"],
            [
                {"type": "text", "text": "ロン"},
                {"type": "ruby", "text": "T", "reading": "てぃー"},
            ],
        )

    def test_rendered_yomi_ruby_tokens_keep_latin_and_han_token_together(self) -> None:
        tokens = rendered_yomi_ruby_tokens("AB型/エービーガタ")

        self.assertEqual(
            tokens[0]["nodes"],
            [
                {"type": "ruby", "text": "AB型", "reading": "えーびーがた"},
            ],
        )

    def test_rendered_yomi_ruby_tokens_project_numeric_kana_suffix_separately(self) -> None:
        tokens = rendered_yomi_ruby_tokens(
            "2日/フツカ ３日/ミッカ 1つ/ヒトツ ２つ/フタツ 1番/イチバン 30/ 分/プン"
        )
        self.assertEqual(
            [token["nodes"] for token in tokens],
            [
                [{"type": "ruby", "text": "2日", "reading": "ふつか"}],
                [{"type": "ruby", "text": "３日", "reading": "みっか"}],
                [
                    {"type": "ruby", "text": "1", "reading": "ひと"},
                    {"type": "text", "text": "つ"},
                ],
                [
                    {"type": "ruby", "text": "２", "reading": "ふた"},
                    {"type": "text", "text": "つ"},
                ],
                [{"type": "ruby", "text": "1番", "reading": "いちばん"}],
                [{"type": "text", "text": "30"}],
                [{"type": "ruby", "text": "分", "reading": "ぷん"}],
            ],
        )

    def test_rendered_yomi_ruby_tokens_support_supplementary_cjk_characters(self) -> None:
        tokens = rendered_yomi_ruby_tokens("𠮟られる/シカラレル 𩸽/ホッケ")

        self.assertEqual(
            [token["nodes"] for token in tokens],
            [
                [
                    {"type": "ruby", "text": "𠮟", "reading": "しか"},
                    {"type": "text", "text": "られる"},
                ],
                [{"type": "ruby", "text": "𩸽", "reading": "ほっけ"}],
            ],
        )

    def test_pack_candidate_nodes_keep_kana_outside_latin_ruby(self) -> None:
        target = {
            "item_id": "u1:r0001c01",
            "unit_id": "u1",
            "token_index": 0,
            "chunk_index": 0,
            "surface": "Tシャツ",
            "token_surface": "Tシャツ",
            "current_reading": "ティーシャツ",
            "current_reading_hiragana": "てぃーしゃつ",
            "target_start": 0,
            "target_end": 4,
            "is_safe": True,
            "review_status": "safe",
            "highlight_level": "none",
            "accepted_signal_names": ["safe_by_llm_match"],
            "signals": [
                {
                    "name": "safe_by_llm_match",
                    "accepted": True,
                    "status": "matched",
                    "llm_reading": "てぃーしゃつ",
                    "current_reading_hiragana": "てぃーしゃつ",
                }
            ],
            "status_reason": "accepted_pre_llm_signal",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            payload = unit("doc1", "u1", "Tシャツです。", safe=True)
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "Tシャツ/ティーシャツ です/デス 。/。"
            payload["analysis"]["safety"]["yomi"]["targets"] = [target]
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            candidate = pack["items"][0]["targets"][0]["candidates"][0]
            self.assertEqual(
                candidate["ruby_nodes"],
                [
                    {"type": "ruby", "text": "T", "reading": "てぃー"},
                    {"type": "text", "text": "シャツ"},
                ],
            )

    def test_review_target_exposes_multiple_dictionary_readings_with_stable_ids(self) -> None:
        target = {
            "item_id": "u1:r0001c01",
            "surface": "打ち壊す",
            "current_reading": "ウチコワス",
            "current_reading_hiragana": "うちこわす",
            "is_safe": False,
            "signals": [],
        }

        with patch(
            "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
            return_value={"打ち壊す": ("うちこわす", "ぶちこわす")},
        ):
            review_target = build_review_target(target)

        self.assertEqual(review_target["default_candidate_id"], "current")
        self.assertEqual(
            [
                (candidate["id"], candidate["source"], candidate["reading"])
                for candidate in review_target["candidates"]
            ],
            [
                ("current", "current", "うちこわす"),
                ("dictionary:1", "dictionary", "ぶちこわす"),
                ("none", "none", None),
            ],
        )

    def test_build_pack_groups_units_and_exposes_tappable_ruby_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            summary_path = root / "summary.json"
            unresolved = unit("doc1", "u1", "近々です。")
            unresolved["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "近々/キンキン です/デス 。/。"
            )
            accepted = unit("doc2", "u2", "学校です。", safe=True)
            accepted["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "学校/ガッコウ です/デス 。/。"
            )
            units_path.write_text(
                "\n".join(
                    [
                        json.dumps(unresolved, ensure_ascii=False),
                        json.dumps(accepted, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "yomi_corpus.yomi.final_review.load_final_review_surface_readings",
                return_value={},
            ):
                summary = build_yomi_final_review_pack_file(
                    units_jsonl=units_path,
                    output_json=output_path,
                    pack_id="yomi_final_dev_batch_0001_v1",
                    track_name="dev",
                    batch_name="dev_batch_0001",
                    created_at_epoch=123,
                )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary.item_count, 2)
            self.assertEqual(summary.unresolved_item_count, 1)
            self.assertEqual(summary.unresolved_target_count, 1)
            self.assertEqual(pack["review_stage"], "yomi_final_review")
            self.assertEqual(pack["queue_id"], "final_review")
            self.assertEqual([doc["doc_id"] for doc in pack["documents"]], ["doc1", "doc2"])
            self.assertEqual(pack["documents"][0]["state"], "final_pending")
            self.assertEqual(pack["documents"][0]["workflow_state"], "bulk_review")
            self.assertEqual(pack["documents"][0]["workflow_queue_stage"], "yomi_final_review")
            self.assertEqual(pack["documents"][0]["track_doc_seq"], 1)
            self.assertTrue(pack["documents"][0]["selectable"])
            self.assertEqual(pack["documents"][0]["item_count"], 1)
            self.assertEqual(pack["documents"][0]["unresolved_count"], 1)
            self.assertEqual(pack["summary"]["selectable_document_count"], 2)
            self.assertEqual(pack["items"][0]["doc_seq"], 1)
            self.assertEqual(pack["items"][0]["track_doc_seq"], 1)
            self.assertEqual(pack["items"][1]["doc_seq"], 2)
            self.assertEqual(pack["items"][1]["track_doc_seq"], 2)
            target = pack["items"][0]["targets"][0]
            self.assertFalse(target["is_safe"])
            self.assertEqual(target["default_choice_source"], "llm")
            self.assertEqual(target["default_reading"], "ちかぢか")
            self.assertEqual(
                [(candidate["source"], candidate["reading"]) for candidate in target["candidates"]],
                [
                    ("current", "きんきん"),
                    ("llm", "ちかぢか"),
                    ("none", None),
                ],
            )
            self.assertEqual(
                pack["items"][0]["ruby_segments"],
                [
                    {
                        "type": "ruby",
                        "text": "近々",
                        "target_item_id": "u1:s0001-0002",
                        "reading": "ちかぢか",
                        "is_safe": False,
                        "highlight_level": "target",
                    },
                    {"type": "text", "text": "です。"},
                ],
            )
            self.assertFalse(pack["items"][0]["all_targets_safe"])
            self.assertTrue(pack["items"][1]["all_targets_safe"])

            summary_path.write_text(json.dumps(summary.__dict__), encoding="utf-8")

    def test_pack_rendered_yomi_uses_same_default_as_accepted_review_range(self) -> None:
        payload = unit("doc1", "u1", "「断」断つこと。")
        payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
            "「/「 断/ダン 」/」 断つ/タツ こと/コト 。/。"
        )
        target = payload["analysis"]["safety"]["yomi"]["targets"][0]
        target.update(
            {
                "item_id": "u1:r0002c01",
                "surface": "断",
                "token_surface": "断",
                "current_reading": "ダン",
                "current_reading_hiragana": "だん",
                "target_start": 1,
                "target_end": 2,
                "token_index": 1,
                "signals": [
                    {
                        "name": "safe_by_llm_match",
                        "accepted": False,
                        "status": "mismatched",
                        "llm_reading": "た",
                        "current_reading_hiragana": "だん",
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            item = pack["items"][0]
            self.assertEqual(item["targets"][0]["default_choice_source"], "llm")
            self.assertEqual(item["targets"][0]["default_reading"], "た")
            self.assertEqual(
                item["rendered_yomi"],
                "「/「 断/タ 」/」 断つ/タツ こと/コト 。/。",
            )
            self.assertEqual(item["rendered_yomi_ruby_tokens"][1]["reading"], "タ")

    def test_rendered_yomi_with_review_defaults_uses_no_ruby_default(self) -> None:
        rendered = "ZUZU/ズズ です/デス 。/。"
        targets = [
            {
                "surface": "ZUZU",
                "token_surface": "ZUZU",
                "token_index": 0,
                "default_choice_source": "none",
                "default_reading": None,
            }
        ]

        self.assertEqual(
            rendered_yomi_with_review_defaults(rendered, targets),
            "ZUZU/ です/デス 。/。",
        )

    def test_final_review_pack_uses_document_state_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            state_path = root / "document_state.json"
            units_path.write_text(
                "\n".join(
                    [
                        json.dumps(unit("doc1", "u1", "近々です。"), ensure_ascii=False),
                        json.dumps(unit("doc2", "u2", "学校です。", safe=True), ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "batch_name": "dev_batch_0001",
                        "track_name": "dev",
                        "created_at": "2026-07-03T00:00:00Z",
                        "updated_at": "2026-07-03T00:00:00Z",
                        "summary": {"document_count": 2, "state_counts": {}},
                        "documents": [
                            {
                                "doc_id": "doc1",
                                "doc_seq": 1,
                                "state": "final_pending",
                                "unit_count": 1,
                                "reviewed_unit_count": 0,
                                "skipped_unit_count": 0,
                                "strong_repair_item_count": 0,
                                "updated_at": "2026-07-03T00:00:00Z",
                            },
                            {
                                "doc_id": "doc2",
                                "doc_seq": 2,
                                "state": "complete",
                                "unit_count": 1,
                                "reviewed_unit_count": 1,
                                "skipped_unit_count": 0,
                                "strong_repair_item_count": 0,
                                "updated_at": "2026-07-03T00:00:00Z",
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                document_state_json=state_path,
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            docs = {doc["doc_id"]: doc for doc in pack["documents"]}
            self.assertTrue(docs["doc1"]["selectable"])
            self.assertFalse(docs["doc2"]["selectable"])
            self.assertEqual(docs["doc2"]["state"], "complete")
            self.assertEqual(docs["doc2"]["workflow_state"], "resolved")
            self.assertIsNone(docs["doc2"]["workflow_queue_stage"])
            self.assertEqual(pack["summary"]["document_state_counts"]["complete"], 1)

    def test_pack_drops_non_kana_reading_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            payload = {
                "doc_id": "doc1",
                "unit_id": "u1",
                "unit_seq": 1,
                "text": "Diploma Mill",
                "source_file": "source.jsonl.gz",
                "source_line_no": 1,
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "Diploma/ディプロマ Mill/mill",
                        }
                    },
                    "safety": {
                        "yomi": {
                            "targets": [
                                {
                                    "item_id": "u1:r0002c01",
                                    "unit_id": "u1",
                                    "token_index": 1,
                                    "chunk_index": 0,
                                    "surface": "Mill",
                                    "token_surface": "Mill",
                                    "current_reading": "mill",
                                    "current_reading_hiragana": "mill",
                                    "target_start": 8,
                                    "target_end": 12,
                                    "is_safe": False,
                                    "review_status": "unresolved",
                                    "highlight_level": "target",
                                    "accepted_signal_names": [],
                                    "signals": [
                                        {
                                            "name": "safe_by_llm_match",
                                            "accepted": False,
                                            "status": "mismatched",
                                            "llm_reading": "みる",
                                            "current_reading_hiragana": "mill",
                                        }
                                    ],
                                    "status_reason": "llm_reading_mismatched",
                                }
                            ]
                        }
                    },
                },
            }
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            target = pack["items"][0]["targets"][0]
            self.assertEqual(target["default_choice_source"], "llm")
            self.assertEqual(target["default_reading"], "みる")
            self.assertEqual(
                [(candidate["source"], candidate["reading"]) for candidate in target["candidates"]],
                [("llm", "みる"), ("none", None)],
            )
            self.assertEqual(pack["items"][0]["ruby_segments"][1]["reading"], "みる")

    def test_pack_defaults_laughter_w_to_no_ruby(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            payload = {
                "doc_id": "doc1",
                "unit_id": "u_w",
                "unit_seq": 1,
                "text": "ｗ　そして学校です。",
                "source_file": "source.jsonl.gz",
                "source_line_no": 1,
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "ｗ/ワット 　/　 そして/ソシテ 学校/ガッコウ です/デス 。/。",
                        }
                    },
                    "safety": {
                        "yomi": {
                            "targets": [
                                {
                                    "item_id": "u_w:r0001c01",
                                    "unit_id": "u_w",
                                    "token_index": 0,
                                    "chunk_index": 0,
                                    "surface": "ｗ",
                                    "token_surface": "ｗ",
                                    "current_reading": "ワット",
                                    "current_reading_hiragana": "わっと",
                                    "target_start": 0,
                                    "target_end": 1,
                                    "is_safe": True,
                                    "review_status": "safe",
                                    "highlight_level": "none",
                                    "accepted_signal_names": ["safe_by_no_ruby_laughter_w"],
                                    "signals": [
                                        {
                                            "name": "safe_by_no_ruby_laughter_w",
                                            "accepted": True,
                                            "reason": "standalone_lowercase_w_laughter_marker",
                                            "preferred_choice_source": "none",
                                        }
                                    ],
                                    "status_reason": "accepted_pre_llm_signal",
                                }
                            ]
                        }
                    },
                },
            }
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            target = pack["items"][0]["targets"][0]
            self.assertTrue(target["is_safe"])
            self.assertEqual(target["default_choice_source"], "none")
            self.assertIsNone(target["default_reading"])
            self.assertEqual(
                [
                    (
                        candidate["id"],
                        candidate["source"],
                        candidate["reading"],
                        candidate["accepted"],
                    )
                    for candidate in target["candidates"]
                ],
                [
                    ("current", "current", "わっと", False),
                    ("none", "none", None, False),
                    ("accepted_none", "none", None, True),
                ],
            )
            self.assertEqual(target["default_candidate_id"], "accepted_none")
            self.assertEqual(pack["items"][0]["ruby_segments"][0]["reading"], None)

    def test_replay_yomi_review_submissions_applies_later_overlap(self) -> None:
        pack = {
            "pack_id": "pack_1",
            "items": [
                {"item_id": "u1", "seq": 1, "skip_default": False},
                {"item_id": "u2", "seq": 2, "skip_default": False},
            ],
        }
        first = {
            "submission_id": "s1",
            "generated_at_epoch": 1,
            "reviewed_ranges": [{"from_seq": 1, "to_seq": 2}],
            "overrides": [{"item_id": "u2", "skip": True}],
        }
        second = {
            "submission_id": "s2",
            "generated_at_epoch": 2,
            "reviewed_ranges": [{"from_seq": 2, "to_seq": 2}],
            "overrides": [],
        }

        effective = replay_review_submissions(pack, [first, second])

        self.assertFalse(effective["u1"]["skip"])
        self.assertFalse(effective["u2"]["skip"])
        self.assertEqual(effective["u2"]["submission_id"], "s2")

    def test_replay_yomi_review_submissions_inherits_and_explicitly_clears_manual_flag(self) -> None:
        pack = {
            "pack_id": "pack_1",
            "items": [
                {
                    "item_id": "u1",
                    "seq": 1,
                    "skip_default": False,
                    "manual_correction_required": True,
                }
            ],
        }
        inherited = replay_review_submissions(
            pack,
            [
                {
                    "submission_id": "s1",
                    "generated_at_epoch": 1,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [],
                }
            ],
        )
        cleared = replay_review_submissions(
            pack,
            [
                {
                    "submission_id": "s2",
                    "generated_at_epoch": 2,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {"item_id": "u1", "manual_correction_required": False}
                    ],
                }
            ],
        )

        self.assertTrue(inherited["u1"]["manual_correction_required"])
        self.assertFalse(cleared["u1"]["manual_correction_required"])

    def test_strong_review_manual_flag_updates_unit_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            units_path = Path(tmp) / "units.jsonl"
            units_path.write_text(
                json.dumps({"unit_id": "u1", "analysis": {}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            summary = apply_manual_correction_flags_file(
                pack={"items": [{"item_id": "repair1", "unit_id": "u1"}]},
                effective={
                    "repair1": {
                        "manual_correction_required": True,
                        "submission_id": "strong-1",
                        "generated_at_epoch": 20,
                    }
                },
                units_jsonl=units_path,
                source_stage="yomi_strong_repair_review",
            )
            row = json.loads(units_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["changed_units"], 1)
        self.assertTrue(manual_correction_required(row))
        self.assertEqual(
            row["analysis"]["human_review"]["manual_correction"]["source_stage"],
            "yomi_strong_repair_review",
        )

    def test_apply_final_review_updates_exact_rendered_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            payload = unit("doc1", "u1", "近々です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "近々/キンキン です/デス 。/。"
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "manual_correction_required": True,
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "llm",
                                    "selected_reading": "ちかぢか",
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertTrue(summary["stage_complete"])
            self.assertEqual(summary["exact_rendered_updates"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/チカヂカ です/デス 。/。",
            )
            review = row["analysis"]["human_review"]["yomi_final"]
            self.assertTrue(review["reviewed"])
            self.assertEqual(review["target_overrides"][0]["item_id"], "u1:s0001-0002")
            self.assertEqual(
                review["target_overrides"][0]["legacy_target_item_id"],
                "u1:r0001c01",
            )
            self.assertEqual(review["target_overrides"][0]["selected_reading"], "ちかぢか")
            self.assertTrue(manual_correction_required(row))

    def test_exact_rendered_target_override_falls_back_when_token_index_is_stale(self) -> None:
        payload = {
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "『/『 Led/エルイーディー / Zeppelin/ツェッペリン 』/』"
                    }
                }
            }
        }

        updated = apply_exact_rendered_target_overrides(
            payload,
            [
                {
                    "surface": "Led",
                    "token_surface": "Led",
                    "token_index": 3,
                    "selected_reading": "れっど",
                }
            ],
        )

        self.assertEqual(updated, 1)
        self.assertEqual(
            payload["analysis"]["mechanical"]["yomi"]["rendered"],
            "『/『 Led/レッド / Zeppelin/ツェッペリン 』/』",
        )

    def test_exact_rendered_target_override_applies_intentional_no_ruby(self) -> None:
        payload = {
            "analysis": {
                "mechanical": {"yomi": {"rendered": "七五三/シチゴサン です/デス"}}
            }
        }

        updated = apply_exact_rendered_target_overrides(
            payload,
            [
                {
                    "surface": "七五三",
                    "token_surface": "七五三",
                    "token_index": 0,
                    "choice_source": "none",
                    "selected_reading": None,
                    "accepted_no_ruby": True,
                }
            ],
        )

        self.assertEqual(updated, 1)
        self.assertEqual(
            payload["analysis"]["mechanical"]["yomi"]["rendered"],
            "七五三/ です/デス",
        )

    def test_apply_final_review_applies_llm_default_for_reviewed_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            payload = unit("doc1", "u1", "近々です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "近々/キンキン です/デス 。/。"
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertTrue(summary["stage_complete"])
            self.assertEqual(summary["exact_rendered_updates"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/チカヂカ です/デス 。/。",
            )
            review = row["analysis"]["human_review"]["yomi_final"]
            self.assertEqual(review["target_overrides"][0]["choice_source"], "llm")
            self.assertEqual(review["target_overrides"][0]["selected_reading"], "ちかぢか")

    def test_apply_final_review_blocks_when_review_coverage_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            first = unit("doc1", "u1", "近々です。")
            second = unit("doc2", "u2", "近々です。")
            second["unit_seq"] = 2
            units_path.write_text(
                json.dumps(first, ensure_ascii=False)
                + "\n"
                + json.dumps(second, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertFalse(summary["stage_complete"])
            self.assertEqual(summary["reviewed_units"], 1)
            self.assertEqual(summary["unreviewed_units"], 1)
            self.assertIn("not been reviewed", summary["blocking_reason"])
            self.assertTrue(output_path.exists())

    def test_apply_final_review_applies_span_segmentation_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            payload = unit("doc1", "u1", "それを、旧池尻中学校を改装した。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。"
            )
            base_target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target0 = {**base_target}
            target0.update(
                {
                    "item_id": "u1:r0005c01",
                    "token_index": 4,
                    "surface": "池尻中",
                    "token_surface": "池尻中",
                    "current_reading": "イケジリナカ",
                    "current_reading_hiragana": "いけじりなか",
                    "target_start": 5,
                    "target_end": 8,
                    "is_safe": False,
                    "review_status": "unresolved",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            target1 = {**base_target}
            target1.update(
                {
                    "item_id": "u1:r0006c01",
                    "token_index": 5,
                    "surface": "学校",
                    "token_surface": "学校",
                    "current_reading": "ガッコウ",
                    "current_reading_hiragana": "がっこう",
                    "target_start": 8,
                    "target_end": 10,
                    "is_safe": True,
                    "review_status": "safe",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            payload["analysis"]["safety"]["yomi"]["targets"] = [target0, target1]
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["items"][0]["reading_hints"].get("中学校"), "ちゅうがっこう")
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "span_overrides": [
                                {
                                    "id": "u1:r0005c01|u1:r0006c01",
                                    "decision": "segmentation",
                                    "target_item_ids": ["u1:r0005c01", "u1:r0006c01"],
                                    "original_surface": "池尻中学校",
                                    "segments": [
                                        {"surface": "池尻", "reading": "いけじり"},
                                        {"surface": "中学校", "reading": "ちゅうがっこう"},
                                    ],
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertTrue(summary["stage_complete"])
            self.assertEqual(summary["span_override_count"], 1)
            self.assertEqual(summary["exact_rendered_span_updates"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻/イケジリ 中学校/チュウガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。",
            )
            review = row["analysis"]["human_review"]["yomi_final"]
            self.assertEqual(review["span_overrides"][0]["decision"], "segmentation")
            self.assertEqual(review["exact_rendered_span_updates"], 1)

    def test_skipped_item_records_target_override_without_applying_or_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            payload = unit("doc1", "u1", "近々です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "近々/キンキン です/デス 。/。"
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": True,
                            "escalate_sentence": True,
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "llm",
                                    "selected_reading": "ちかぢか",
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )
            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=reviewed_path,
                strong_queue_summary_json=queue_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertEqual(apply_summary["exact_rendered_updates"], 0)
            reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
            self.assertEqual(
                reviewed["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/キンキン です/デス 。/。",
            )
            review = reviewed["analysis"]["human_review"]["yomi_final"]
            self.assertTrue(review["skip"])
            self.assertNotIn("escalate_sentence", review)
            self.assertEqual(review["target_overrides"][0]["selected_reading"], "ちかぢか")
            self.assertEqual(queue_summary["queued_items"], 0)
            self.assertEqual(queue_path.read_text(encoding="utf-8"), "")
            self.assertTrue(final_summary["stage_complete"])
            self.assertEqual(final_summary["written_units"], 0)
            self.assertEqual(final_summary["skipped_units"], 1)

    def test_target_no_ruby_queue_uses_current_batch_case_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "真光元被害者の会が発足しました。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "真光/シンコウ 元/モト 被害者/ヒガイシャ の/ノ 会/カイ が/ガ 発足/ホッソク し/シ まし/マシ た/タ 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0002c01",
                    "token_index": 1,
                    "surface": "元",
                    "token_surface": "元",
                    "current_reading": "モト",
                    "current_reading_hiragana": "もと",
                    "target_start": 2,
                    "target_end": 3,
                }
            )
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0002",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": False,
                            "targets": [
                                {
                                    "item_id": "u1:r0002c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 1)
            self.assertEqual(queue_summary["target_escalations"], 1)
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "target_group")
            self.assertEqual(queued["repair_order"], 1)
            self.assertEqual(queued["reasons"], ["target_no_ruby"])
            self.assertEqual(queued["target_escalations"][0]["surface"], "元")
            self.assertEqual(queued["target_escalations"][0]["choice_source"], "none")
            self.assertEqual(
                queued["target_escalations"][0]["rejected_readings"],
                [{"surface": "元", "reading": "もと", "source": "human_no_ruby"}],
            )
            self.assertIn("真光元被害者", queued["text"])

    def test_laughter_w_no_ruby_override_does_not_queue_strong_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "ｗ　そして学校です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "ｗ/ワット 　/　 そして/ソシテ 学校/ガッコウ です/デス 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0001c01",
                    "token_index": 0,
                    "surface": "ｗ",
                    "token_surface": "ｗ",
                    "current_reading": "ワット",
                    "current_reading_hiragana": "わっと",
                    "target_start": 0,
                    "target_end": 1,
                    # Simulate an older artifact generated before the laughter-w
                    # safety rule was applied.
                    "is_safe": False,
                    "review_status": "unresolved",
                    "highlight_level": "target",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0002",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": False,
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 0)
            self.assertEqual(queue_summary["target_escalations"], 0)
            self.assertEqual(queue_path.read_text(encoding="utf-8"), "")

    def test_target_no_ruby_queue_can_carry_rejected_publisher_name_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "この本は「史輝出版」から刊行されました。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "この/コノ 本/ホン は/ハ 「/「 史輝/フミテル 出版/シュッパン 」/」 から/カラ 刊行/カンコウ さ/サ れ/レ まし/マシ た/タ 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0005c01",
                    "token_index": 4,
                    "surface": "史輝",
                    "token_surface": "史輝",
                    "current_reading": "フミテル",
                    "current_reading_hiragana": "ふみてる",
                    "target_start": 5,
                    "target_end": 7,
                }
            )
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0002",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": False,
                            "targets": [
                                {
                                    "item_id": "u1:r0005c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "target_group")
            self.assertEqual(queued["target_escalations"][0]["surface"], "史輝")
            self.assertEqual(
                queued["target_escalations"][0]["rejected_readings"],
                [{"surface": "史輝", "reading": "ふみてる", "source": "human_no_ruby"}],
            )

    def test_numeric_merge_span_queues_and_applies_strong_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            results_path = root / "results.jsonl"
            repaired_path = root / "repaired.jsonl"
            repair_summary_path = root / "repair_summary.json"
            payload = unit("doc1", "u1", "2ndです。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "2/ nd/エヌディー です/デス 。/。"
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0002c01",
                    "surface": "nd",
                    "token_surface": "nd",
                    "current_reading": "エヌディー",
                    "current_reading_hiragana": "えぬでぃー",
                    "target_start": 1,
                    "target_end": 3,
                    "token_index": 1,
                    "is_safe": False,
                    "review_status": "unresolved",
                    "highlight_level": "target",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "targets": [
                                {
                                    "item_id": "u1:r0002c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                            "span_overrides": [
                                {
                                    "id": "numeric-merge:u1:r0002c01:before:2",
                                    "decision": "segmentation",
                                    "target_item_ids": ["u1:r0002c01"],
                                    "original_surface": "2nd",
                                    "repair_required": True,
                                    "repair_reason": "numeric_merge_no_reading",
                                    "segments": [{"surface": "2nd", "reading": ""}],
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )
            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queue_summary["queued_items"], 1)
            self.assertEqual(queued["reasons"], ["numeric_merge_no_reading"])
            self.assertEqual(queued["target_escalations"][0]["surface"], "2nd")
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": queued["item_id"],
                        "parsed": [{"surface": "2nd", "reading": "せかんど"}],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=reviewed_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=repaired_path,
                summary_json=repair_summary_path,
            )
            self.assertEqual(summary["applied_items"], 1)
            row = json.loads(repaired_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "2nd/セカンド です/デス 。/。",
            )

    def test_kana_merge_span_queues_and_applies_strong_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            results_path = root / "results.jsonl"
            repaired_path = root / "repaired.jsonl"
            repair_summary_path = root / "repair_summary.json"
            payload = unit("doc1", "u1", "百瀬はる夏です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "百瀬/モモセ はる/ハル 夏/ナツ です/デス 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0003c01",
                    "surface": "夏",
                    "token_surface": "夏",
                    "current_reading": "ナツ",
                    "current_reading_hiragana": "なつ",
                    "target_start": 4,
                    "target_end": 5,
                    "token_index": 2,
                    "is_safe": False,
                    "review_status": "unresolved",
                    "highlight_level": "target",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            interaction_target = next(
                span for span in pack["items"][0]["interaction_spans"] if span["surface"] == "夏"
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "targets": [
                                {
                                    "item_id": interaction_target["item_id"],
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                            "span_overrides": [
                                {
                                    "id": f"kana-merge:{interaction_target['item_id']}:before:はる",
                                    "decision": "segmentation",
                                    "target_item_ids": [interaction_target["item_id"]],
                                    "original_surface": "はる夏",
                                    "repair_required": True,
                                    "repair_reason": "kana_merge_no_reading",
                                    "segments": [{"surface": "はる夏", "reading": ""}],
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )
            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queue_summary["queued_items"], 1)
            self.assertEqual(queued["reasons"], ["kana_merge_no_reading"])
            self.assertEqual(queued["target_escalations"][0]["surface"], "はる夏")
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": queued["item_id"],
                        "parsed": [{"surface": "はる夏", "reading": "はるか"}],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=reviewed_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=repaired_path,
                summary_json=repair_summary_path,
            )
            self.assertEqual(summary["applied_items"], 1)
            row = json.loads(repaired_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "百瀬/モモセ はる夏/ハルカ です/デス 。/。",
            )

    def test_target_no_ruby_ignores_legacy_sentence_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "真光元被害者の会が発足しました。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "真光/シンコウ 元/モト 被害者/ヒガイシャ の/ノ 会/カイ が/ガ 発足/ホッソク し/シ まし/マシ た/タ 。/。"
            )
            target0 = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target0.update(
                {
                    "item_id": "u1:r0001c01",
                    "token_index": 0,
                    "surface": "真光",
                    "token_surface": "真光",
                    "current_reading": "シンコウ",
                    "current_reading_hiragana": "しんこう",
                    "target_start": 0,
                    "target_end": 2,
                }
            )
            target1 = dict(target0)
            target1.update(
                {
                    "item_id": "u1:r0002c01",
                    "token_index": 1,
                    "surface": "元",
                    "token_surface": "元",
                    "current_reading": "モト",
                    "current_reading_hiragana": "もと",
                    "target_start": 2,
                    "target_end": 3,
                }
            )
            payload["analysis"]["safety"]["yomi"]["targets"].append(target1)
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0002",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": False,
                            "escalate_sentence": True,
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                },
                                {
                                    "item_id": "u1:r0002c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 1)
            self.assertEqual(queue_summary["target_escalations"], 2)
            rows = [
                json.loads(line)
                for line in queue_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["repair_scope"] for row in rows], ["target_group"])
            self.assertEqual([row["repair_order"] for row in rows], [1])
            self.assertEqual(
                [target["surface"] for target in rows[0]["target_escalations"]],
                ["真光", "元"],
            )

    def test_strong_queue_blocks_finalize_before_repair_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewed_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            reviewed_path.write_text(
                json.dumps(
                    {
                        "doc_id": "doc1",
                        "unit_id": "u1",
                        "text": "近々です。",
                        "analysis": {
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": False,
                                    "target_overrides": [
                                        {
                                            "item_id": "u1:r0001c01",
                                            "choice_source": "none",
                                        }
                                    ],
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )
            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=reviewed_path,
                strong_queue_summary_json=queue_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 1)
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "target_group")
            self.assertEqual(queued["repair_order"], 1)
            self.assertFalse(final_summary["stage_complete"])
            self.assertIn("has not been applied", final_summary["blocking_reason"])

    def test_harvest_yomi_finalization_artifacts_writes_rewrites_and_furigana(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_units = root / "units.yomi.final.jsonl"
            unit = {
                "unit_id": "u1",
                "text": "池尻中学校架空語。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "池尻/イケジリ 中学校/チュウガッコウ 架空語/カクウゴ 。/。"
                        }
                    },
                    "llm": {
                        "yomi_strong_repair": {
                            "repairs": [
                                {
                                    "item_id": "u1::target_group:1",
                                    "status": "applied",
                                    "rejected_span": "池尻中学校",
                                    "replacement": [
                                        {"surface": "池尻", "reading": "イケジリ"},
                                        {"surface": "中学校", "reading": "チュウガッコウ"},
                                    ],
                                }
                            ]
                        }
                    },
                },
            }
            final_units.write_text(json.dumps(unit, ensure_ascii=False) + "\n", encoding="utf-8")

            summary = harvest_yomi_finalization_artifacts_file(
                final_units_jsonl=final_units,
                batch_manual_rewrites_jsonl=root / "batch_rewrites.jsonl",
                batch_supplemental_furigana_tsv=root / "batch_furigana.tsv",
                global_manual_rewrites_jsonl=root / "global" / "manual_yomi_rewrites.jsonl",
                global_supplemental_furigana_tsv=root / "global" / "supplemental_furigana.tsv",
                summary_json=root / "summary.json",
                batch_name="dev_batch_0001",
                track_name="dev",
            )

            self.assertEqual(summary["manual_rewrite_count"], 1)
            rewrite = json.loads((root / "batch_rewrites.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(rewrite["original_surface"], "池尻中学校")
            self.assertEqual(rewrite["replacement_rendered"], "池尻/イケジリ 中学校/チュウガッコウ")
            furigana_text = (root / "batch_furigana.tsv").read_text(encoding="utf-8")
            self.assertIn("架空語\tカクウゴ\t架空語（かくうご）", furigana_text)
            self.assertTrue((root / "global" / "manual_yomi_rewrites.jsonl").exists())
            self.assertTrue((root / "global" / "supplemental_furigana.tsv").exists())

    def test_applies_target_group_strong_repair_and_blocks_finalize_until_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewed_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            results_path = root / "results.jsonl"
            strong_path = root / "strong.jsonl"
            strong_summary_path = root / "strong_summary.json"
            pack_path = root / "strong_pack.json"
            submission_store = root / "strong_submissions"
            confirmation_summary_path = root / "strong_confirmation_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            reviewed_path.write_text(
                json.dumps(
                    {
                        "doc_id": "doc1",
                        "unit_id": "u1",
                        "text": "それを、旧池尻中学校を改装した。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。"
                                }
                            },
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": False,
                                    "target_overrides": [
                                        {
                                            "item_id": "u1:r0005c01",
                                            "choice_source": "none",
                                            "surface": "池尻中",
                                            "token_index": 4,
                                            "chunk_index": 0,
                                            "current_reading_hiragana": "いけじりなか",
                                        },
                                        {
                                            "item_id": "u1:r0006c01",
                                            "choice_source": "none",
                                            "surface": "学校",
                                            "token_index": 5,
                                            "chunk_index": 0,
                                            "current_reading_hiragana": "がっこう",
                                        },
                                    ],
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": queued["item_id"],
                        "parsed": [
                            {
                                "surface": "池尻",
                                "reading": "いけじり",
                                "used_web_search": False,
                                "comment": "Established place-name reading.",
                            },
                            {
                                "surface": "中学校",
                                "reading": "ちゅうがっこう",
                                "used_web_search": False,
                                "comment": "Established place-name reading.",
                            },
                        ],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            strong_summary = apply_yomi_strong_repair_results_file(
                units_jsonl=reviewed_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=strong_path,
                summary_json=strong_summary_path,
            )
            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=strong_path,
                strong_queue_summary_json=queue_summary_path,
                strong_apply_summary_json=strong_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertTrue(strong_summary["stage_complete"])
            self.assertEqual(strong_summary["applied_items"], 1)
            repaired = json.loads(strong_path.read_text(encoding="utf-8"))
            self.assertIn("池尻/イケジリ 中学校/チュウガッコウ", repaired["analysis"]["mechanical"]["yomi"]["rendered"])
            self.assertEqual(
                repaired["analysis"]["llm"]["yomi_strong_repair"]["repairs"][0]["evidence"],
                [
                    {
                        "region_id": queued["item_id"],
                        "surface": "池尻中学校",
                        "comment": "Established place-name reading.",
                        "used_web_search": False,
                        "surface_occurrence_index": None,
                    }
                ],
            )
            self.assertFalse(final_summary["stage_complete"])
            self.assertIn("require human confirmation", final_summary["blocking_reason"])

            pack_summary = build_yomi_strong_repair_review_pack_file(
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                units_jsonl=strong_path,
                output_json=pack_path,
                pack_id="strong_pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            self.assertEqual(pack_summary.item_count, 1)
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["review_stage"], "yomi_strong_repair_review")
            self.assertEqual(pack["summary"]["document_count"], 1)
            self.assertEqual(pack["items"][0]["doc_id"], "doc1")
            self.assertEqual(pack["items"][0]["doc_seq"], 1)
            self.assertEqual(pack["items"][0]["rejected_span"], "池尻中学校")
            self.assertEqual(pack["items"][0]["repair_status"], "applied")
            self.assertEqual(
                pack["items"][0]["llm_comments"],
                ["Established place-name reading."],
            )

            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_strong_repair_review",
                    "pack_id": "strong_pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [],
                },
                submission_store_dir=submission_store,
            )
            confirmation_summary = apply_strong_repair_review_file(
                pack_json=pack_path,
                submission_store_dir=submission_store,
                strong_apply_summary_json=strong_summary_path,
                output_summary_json=confirmation_summary_path,
                units_jsonl=strong_path,
            )
            self.assertTrue(confirmation_summary["stage_complete"])
            confirmed = json.loads(strong_path.read_text(encoding="utf-8"))
            self.assertEqual(
                confirmed["analysis"]["human_review"]["strong_repair_evidence"][0]["comment"],
                "Established place-name reading.",
            )
            confirmed_repair_summary = json.loads(strong_summary_path.read_text(encoding="utf-8"))
            self.assertTrue(confirmed_repair_summary["confirmed"])

            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=strong_path,
                strong_queue_summary_json=queue_summary_path,
                strong_apply_summary_json=strong_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )
            self.assertTrue(final_summary["stage_complete"])
            self.assertEqual(final_summary["written_units"], 1)

    def test_strong_repair_preserves_okurigana_around_internal_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "strong.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "お箸で摘んだ。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "お/オ 箸/ハシ で/デ 摘ん/ツン だ/ダ 。/。"}
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {
                                "surface": "摘",
                                "token_surface": "摘ん",
                                "token_index": 3,
                                "current_reading_hiragana": "つ",
                                "rejected_readings": [
                                    {"surface": "摘", "reading": "つ", "source": "human_no_ruby"}
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [{"surface": "摘", "reading": "つま"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            repaired = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["applied_items"], 1)
            self.assertIn("摘ん/ツマン", repaired["analysis"]["mechanical"]["yomi"]["rendered"])

    def test_strong_repair_maps_span_across_token_boundary_and_partial_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "strong.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "一言添える。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "一/イチ 言添える/イイソエル 。/。"}
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {"surface": "一", "token_index": 0, "chunk_index": 0},
                            {"surface": "言添", "token_index": 1, "chunk_index": 0},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [
                            {"surface": "一言", "reading": "ひとこと"},
                            {"surface": "添", "reading": "そ"},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary["applied_items"], 1)
            repaired = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                repaired["analysis"]["mechanical"]["yomi"]["tokens"],
                [["一言", "ヒトコト"], ["添える", "ソエル"], ["。", "。"]],
            )

    def test_strong_repair_pack_canonicalizes_literal_slash_without_shifting_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            pack_path = root / "pack.json"
            units_path.write_text(
                json.dumps(
                    {
                        "doc_id": "doc70",
                        "unit_id": "u70",
                        "text": "HIV/AIDSで疲れた。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "HIV/エイチアイブイ /// AIDS/エイズ で/デ 疲れ/ツカレ た/タ 。/。"
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u70::target_group:1",
                        "unit_id": "u70",
                        "target_escalations": [
                            {"surface": "疲", "token_index": 4, "chunk_index": 0}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u70::target_group:1",
                        "parsed": [{"surface": "疲", "reading": "つか"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            build_yomi_strong_repair_review_pack_file(
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="strong_pack_slash",
                track_name="dev",
                batch_name="dev_batch_0010",
                created_at_epoch=123,
            )

            item = json.loads(pack_path.read_text(encoding="utf-8"))["items"][0]
            self.assertEqual(item["rendered_yomi_after_tokens"][1], ["/", "/"])
            self.assertEqual(
                "".join(surface for surface, _reading in item["rendered_yomi_after_tokens"]),
                item["text"],
            )
            self.assertEqual(item["mapping_error_count"], 0)
            self.assertEqual(item["regions"][0]["display_mapping"]["start"], 4)
            self.assertEqual(item["regions"][0]["display_mapping"]["suffix"], "れ")

    def test_finalize_merges_final_review_metadata_onto_strong_repaired_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewed_path = root / "reviewed.jsonl"
            strong_path = root / "strong.jsonl"
            queue_summary_path = root / "queue_summary.json"
            strong_summary_path = root / "strong_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            reviewed_unit = {
                "doc_id": "doc1",
                "unit_id": "u1",
                "text": "学校です。",
                "analysis": {
                    "human_review": {
                        "yomi_final": {
                            "reviewed": True,
                            "skip": False,
                            "submission_id": "s1",
                        },
                        "manual_correction": {
                            "required": True,
                            "events": [
                                {"required": True, "source_stage": "yomi_final_review"}
                            ],
                        },
                    },
                    "mechanical": {"yomi": {"rendered": "学校/ガッコウ です/デス 。/。"}},
                },
            }
            strong_unit = {
                "doc_id": "doc1",
                "unit_id": "u1",
                "text": "学校です。",
                "analysis": {
                    "mechanical": {"yomi": {"rendered": "学校/ガッコウ です/デス 。/。"}},
                },
            }
            reviewed_path.write_text(json.dumps(reviewed_unit, ensure_ascii=False) + "\n", encoding="utf-8")
            strong_path.write_text(json.dumps(strong_unit, ensure_ascii=False) + "\n", encoding="utf-8")
            queue_summary_path.write_text(
                json.dumps({"queued_items": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            strong_summary_path.write_text(
                json.dumps({"stage_complete": True, "confirmed": True}, ensure_ascii=False),
                encoding="utf-8",
            )

            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=strong_path,
                reviewed_units_jsonl=reviewed_path,
                strong_queue_summary_json=queue_summary_path,
                strong_apply_summary_json=strong_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertTrue(final_summary["stage_complete"])
            self.assertEqual(final_summary["written_units"], 1)
            self.assertEqual(final_summary["unreviewed_units"], 0)
            finalized = json.loads(final_path.read_text(encoding="utf-8"))
            self.assertEqual(
                finalized["analysis"]["human_review"]["yomi_final"]["submission_id"],
                "s1",
            )
            self.assertTrue(manual_correction_required(finalized))

    def test_strong_repair_pack_exposes_dictionary_substring_reading_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "text": "旧池尻中学校です。",
                        "rendered_yomi": "旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ です/デス 。/。",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {"surface": "池尻中"},
                            {"surface": "学校"},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [{"surface": "池尻中学校", "reading": "いけじりちゅうがっこう"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "旧/キュウ 池尻中学校/イケジリチュウガッコウ です/デス 。/。"
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "yomi_corpus.yomi.final_review.load_annotated_form_surface_readings",
                return_value={
                    "池尻": ("いけじり", "いけのしり"),
                    "中学校": ("ちゅうがっこう",),
                    "学校": ("がっこう",),
                },
            ):
                build_yomi_strong_repair_review_pack_file(
                    queue_jsonl=queue_path,
                    results_jsonl=results_path,
                    units_jsonl=units_path,
                    output_json=pack_path,
                    pack_id="strong_pack_1",
                    track_name="dev",
                    batch_name="dev_batch_0001",
                    created_at_epoch=123,
                )

            item = json.loads(pack_path.read_text(encoding="utf-8"))["items"][0]
            self.assertEqual(item["reading_candidates"]["中学校"], ["ちゅうがっこう"])
            self.assertEqual(item["reading_candidates"]["学校"], ["がっこう"])
            self.assertEqual(item["reading_candidates"]["池尻"], ["いけじり", "いけのしり"])
            self.assertEqual(item["reading_hints"]["中学校"], "ちゅうがっこう")

    def test_strong_repair_pack_preserves_full_batch_document_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            state_path = root / "document_state.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u2::target_group:1",
                        "unit_id": "u2",
                        "text": "Led Zeppelinです。",
                        "rendered_yomi": "Led/レッド Zeppelin/ツェッペリン です/デス 。/。",
                        "repair_scope": "target_group",
                        "target_escalations": [{"surface": "Zeppelin"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u2::target_group:1",
                        "parsed": [{"surface": "Zeppelin", "reading": "ツェッペリン"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            units_path.write_text(
                json.dumps(
                    {
                        "doc_id": "doc1",
                        "unit_id": "u1",
                        "text": "修正なし。",
                        "analysis": {"mechanical": {"yomi": {"rendered": "修正/シュウセイ なし/ナシ 。/。"}}},
                    },
                    ensure_ascii=False,
                )
                + "\n"
                + json.dumps(
                    {
                        "doc_id": "doc2",
                        "unit_id": "u2",
                        "text": "Led Zeppelinです。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {"rendered": "Led/レッド Zeppelin/ツェッペリン です/デス 。/。"}
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "batch_name": "dev_batch_0001",
                        "track_name": "dev",
                        "created_at": "2026-07-03T00:00:00Z",
                        "updated_at": "2026-07-03T00:00:00Z",
                        "summary": {"document_count": 2, "state_counts": {}},
                        "documents": [
                            {
                                "doc_id": "doc1",
                                "doc_seq": 1,
                                "state": "complete",
                                "unit_count": 1,
                                "reviewed_unit_count": 1,
                                "skipped_unit_count": 0,
                                "strong_repair_item_count": 0,
                                "updated_at": "2026-07-03T00:00:00Z",
                            },
                            {
                                "doc_id": "doc2",
                                "doc_seq": 2,
                                "state": "strong_pending",
                                "unit_count": 1,
                                "reviewed_unit_count": 1,
                                "skipped_unit_count": 0,
                                "strong_repair_item_count": 1,
                                "updated_at": "2026-07-03T00:00:00Z",
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            build_yomi_strong_repair_review_pack_file(
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="strong_pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                document_state_json=state_path,
                created_at_epoch=123,
            )

            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["queue_id"], "strong_repair")
            self.assertEqual(pack["summary"]["document_count"], 2)
            self.assertEqual(pack["summary"]["selectable_document_count"], 1)
            self.assertEqual([doc["doc_id"] for doc in pack["documents"]], ["doc1", "doc2"])
            self.assertEqual(pack["documents"][0]["item_count"], 0)
            self.assertEqual(pack["documents"][0]["state"], "complete")
            self.assertEqual(pack["documents"][0]["workflow_state"], "resolved")
            self.assertFalse(pack["documents"][0]["selectable"])
            self.assertEqual(pack["documents"][1]["item_count"], 1)
            self.assertEqual(pack["documents"][1]["state"], "strong_pending")
            self.assertEqual(pack["documents"][1]["workflow_state"], "escalated_repair")
            self.assertEqual(pack["documents"][1]["workflow_queue_stage"], "yomi_strong_repair_review")
            self.assertEqual(pack["documents"][1]["track_doc_seq"], 2)
            self.assertTrue(pack["documents"][1]["selectable"])
            self.assertEqual(pack["items"][0]["doc_seq"], 2)
            self.assertEqual(pack["items"][0]["track_doc_seq"], 2)

    def test_strong_repair_falls_back_to_unique_surface_span_when_token_index_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "strong.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "山根視来選手です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "山根/ヤマネ 視/シ 来/ライ 選手/センシュ です/デス 。/。"
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {"surface": "視", "token_index": 2, "chunk_index": 0},
                            {"surface": "来", "token_index": 3, "chunk_index": 0},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [{"surface": "視来", "reading": "ミキ"}],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertTrue(summary["stage_complete"])
            repaired = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("山根/ヤマネ 視来/ミキ 選手/センシュ", repaired["analysis"]["mechanical"]["yomi"]["rendered"])

    def test_strong_repair_review_manual_segments_override_llm_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strong_path = root / "strong.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            strong_summary_path = root / "strong_summary.json"
            confirmation_summary_path = root / "confirmation_summary.json"
            unit_payload = {
                "unit_id": "u1",
                "text": "それを、旧池尻中学校を改装した。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻中学校/イケジリチュウガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。"
                        }
                    },
                    "human_review": {"yomi_final": {"reviewed": True, "skip": False}},
                },
            }
            strong_path.write_text(
                json.dumps(unit_payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            pack_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_stage": "yomi_strong_repair_review",
                        "pack_id": "strong_pack_1",
                        "item_count": 1,
                        "items": [
                            {
                                "item_id": "u1::target_group:1",
                                "seq": 1,
                                "unit_id": "u1",
                                "rejected_span": "池尻中学校",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            strong_summary_path.write_text(
                json.dumps({"stage_complete": True, "confirmed": False}, ensure_ascii=False),
                encoding="utf-8",
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_strong_repair_review",
                    "pack_id": "strong_pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1::target_group:1",
                            "decision": "accept",
                            "manual_segments": [
                                {"surface": "池尻", "reading": "いけじり"},
                                {"surface": "中学校", "reading": "ちゅうがっこう"},
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_strong_repair_review_file(
                pack_json=pack_path,
                submission_store_dir=store_dir,
                strong_apply_summary_json=strong_summary_path,
                output_summary_json=confirmation_summary_path,
                units_jsonl=strong_path,
            )
            self.assertTrue(summary["stage_complete"])
            self.assertEqual(summary["manual_segment_overrides"]["applied_items"], 1)
            repaired = json.loads(strong_path.read_text(encoding="utf-8"))
            self.assertIn(
                "池尻/イケジリ 中学校/チュウガッコウ",
                repaired["analysis"]["mechanical"]["yomi"]["rendered"],
            )

    def test_strong_repair_review_normalizes_legacy_punctuation_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strong_path = root / "strong.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            strong_summary_path = root / "strong_summary.json"
            confirmation_summary_path = root / "confirmation_summary.json"
            strong_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "面白かった(笑)。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "面白かっ/オモシロカッ た/タ (笑)/ワライ 。/。"
                                }
                            },
                            "human_review": {"yomi_final": {"reviewed": True, "skip": False}},
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            pack_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_stage": "yomi_strong_repair_review",
                        "pack_id": "strong_pack_1",
                        "item_count": 1,
                        "items": [
                            {
                                "item_id": "u1::strong_repair",
                                "seq": 1,
                                "unit_id": "u1",
                                "regions": [
                                    {
                                        "region_id": "u1::target_group:1",
                                        "item_id": "u1::target_group:1",
                                        "unit_id": "u1",
                                        "rejected_span": "(笑)",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            strong_summary_path.write_text(
                json.dumps({"stage_complete": True, "confirmed": False}),
                encoding="utf-8",
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_strong_repair_review",
                    "pack_id": "strong_pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1::strong_repair",
                            "decision": "accept",
                            "regions": [
                                {
                                    "region_id": "u1::target_group:1",
                                    "manual_segments": [
                                        {"surface": "(", "reading": "/"},
                                        {"surface": "笑", "reading": "わらい"},
                                        {"surface": ")", "reading": "/"},
                                    ],
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_strong_repair_review_file(
                pack_json=pack_path,
                submission_store_dir=store_dir,
                strong_apply_summary_json=strong_summary_path,
                output_summary_json=confirmation_summary_path,
                units_jsonl=strong_path,
            )

            self.assertTrue(summary["stage_complete"])
            self.assertEqual(summary["manual_segment_overrides"]["applied_items"], 1)
            repaired = json.loads(strong_path.read_text(encoding="utf-8"))
            self.assertIn("(/( 笑/ワライ )/)", repaired["analysis"]["mechanical"]["yomi"]["rendered"])

    def test_strong_repair_keeps_reused_rejected_reading_as_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "strong.jsonl"
            summary_path = root / "summary.json"
            queue_summary_path = root / "queue_summary.json"
            pack_path = root / "pack.json"
            submission_store = root / "submissions"
            confirmation_summary_path = root / "confirmation_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "真光元被害者の会です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "真光/シンコウ 元/モト 被害者/ヒガイシャ の/ノ 会/カイ です/デス 。/。"
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {
                                "surface": "真光",
                                "token_index": 0,
                                "chunk_index": 0,
                                "rejected_readings": [{"surface": "真光", "reading": "しんこう"}],
                            },
                            {
                                "surface": "元",
                                "token_index": 1,
                                "chunk_index": 0,
                                "rejected_readings": [{"surface": "元", "reading": "もと"}],
                            },
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_summary_path.write_text(
                json.dumps({"queued_items": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [
                            {"surface": "真光", "reading": "まひかり"},
                            {"surface": "元", "reading": "もと"},
                        ],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertFalse(summary["stage_complete"])
            self.assertEqual(summary["invalid_items"], 0)
            self.assertEqual(summary["noop_items"], 1)
            self.assertEqual(summary["review_pending_items"], 1)
            self.assertEqual(summary["unresolved_items"], 0)
            self.assertIn("no-op items awaiting human confirmation", summary["blocking_reason"])
            repaired = json.loads(output_path.read_text(encoding="utf-8"))
            repair = repaired["analysis"]["llm"]["yomi_strong_repair"]["repairs"][0]
            self.assertEqual(repair["status"], "reused_rejected_reading")

            pack_summary = build_yomi_strong_repair_review_pack_file(
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                units_jsonl=output_path,
                output_json=pack_path,
                pack_id="strong_pack_noop",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            self.assertEqual(pack_summary.item_count, 1)
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_strong_repair_review",
                    "pack_id": "strong_pack_noop",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [],
                },
                submission_store_dir=submission_store,
            )
            confirmation = apply_strong_repair_review_file(
                pack_json=pack_path,
                submission_store_dir=submission_store,
                strong_apply_summary_json=summary_path,
                output_summary_json=confirmation_summary_path,
                units_jsonl=output_path,
            )
            self.assertTrue(confirmation["stage_complete"])
            confirmed_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertFalse(confirmed_summary["stage_complete_before_confirmation"])
            self.assertTrue(confirmed_summary["stage_complete"])
            self.assertTrue(confirmed_summary["confirmed"])
            self.assertEqual(confirmed_summary["confirmation_resolved_noop_items"], 1)
            self.assertEqual(confirmed_summary["post_confirmation_unresolved_items"], 0)

            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=output_path,
                strong_queue_summary_json=queue_summary_path,
                strong_apply_summary_json=summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )
            self.assertTrue(final_summary["stage_complete"])


def unit(doc_id: str, unit_id: str, text: str, *, safe: bool = False) -> dict:
    signals = [
        {
            "name": "safe_by_llm_match",
            "accepted": safe,
            "status": "matched" if safe else "mismatched",
            "llm_reading": "きんきん" if safe else "ちかぢか",
            "current_reading_hiragana": "きんきん",
        }
    ]
    return {
        "doc_id": doc_id,
        "unit_id": unit_id,
        "unit_seq": 1,
        "text": text,
        "source_file": "source.jsonl.gz",
        "source_line_no": 1,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": f"{text}/キンキン",
                }
            },
            "safety": {
                "yomi": {
                    "targets": [
                        {
                            "item_id": f"{unit_id}:r0001c01",
                            "unit_id": unit_id,
                            "token_index": 0,
                            "chunk_index": 0,
                            "surface": "近々",
                            "token_surface": "近々",
                            "current_reading": "キンキン",
                            "current_reading_hiragana": "きんきん",
                            "target_start": 0,
                            "target_end": 2,
                            "is_safe": safe,
                            "review_status": "safe" if safe else "unresolved",
                            "highlight_level": "none" if safe else "target",
                            "accepted_signal_names": ["safe_by_llm_match"] if safe else [],
                            "signals": signals,
                            "status_reason": "accepted_llm_match"
                            if safe
                            else "llm_reading_mismatched",
                        }
                    ]
                }
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
