from __future__ import annotations

import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from yomi_corpus.yomi.corpus_frequency import (
    EVIDENCE_SCOPE_TRAILING_KANA_STEM,
    SurfaceReadingCount,
    SurfaceReadingStats,
)
from yomi_corpus.yomi.llm_readings import build_yomi_llm_reading_queue_file
from yomi_corpus.yomi.safety import (
    build_pre_llm_safety_records,
    resolve_corpus_frequency_stats_artifact,
    safe_yomi_item_ids,
    set_yomi_safety_records,
)
from yomi_corpus.yomi.stable_surface_lexicon import StableSurfaceReadingLexicon


def unit() -> dict:
    return {
        "unit_id": "u1",
        "text": "学校は上です。",
        "analysis": {
            "mechanical": {
                "yomi": {
                    "sudachi": {
                        "tokens": [
                            {
                                "surface": "学校",
                                "pos": "名詞,普通名詞,一般,*,*,*",
                                "dictionary_form": "学校",
                                "normalized_form": "学校",
                                "reading": "ガッコウ",
                            },
                            {
                                "surface": "は",
                                "pos": "助詞,係助詞,*,*,*,*",
                                "dictionary_form": "は",
                                "normalized_form": "は",
                                "reading": "ハ",
                            },
                            {
                                "surface": "上",
                                "pos": "名詞,普通名詞,副詞可能,*,*,*",
                                "dictionary_form": "上",
                                "normalized_form": "上",
                                "reading": "ウエ",
                            },
                            {
                                "surface": "です",
                                "pos": "助動詞,*,*,*,助動詞-ダ,終止形-一般",
                                "dictionary_form": "だ",
                                "normalized_form": "だ",
                                "reading": "デス",
                            },
                            {
                                "surface": "。",
                                "pos": "補助記号,句点,*,*,*,*",
                                "dictionary_form": "。",
                                "normalized_form": "。",
                                "reading": "。",
                            },
                        ]
                    }
                }
            }
        },
    }


class YomiSafetyTests(unittest.TestCase):
    def test_local_stable_span_prevents_llm_for_ambiguous_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stable_surface_readings.tsv"
            artifact.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "segmentation_counts_json\tsource_corpus_version\n"
                '数日\tスウジツ\t27\t27\t1\t[{"surfaces":["数","日"],"count":27}]\tfixture\n'
                '数日後\tスウジツゴ\t6\t6\t1\t[{"surfaces":["数","日","後"],"count":6}]\tfixture\n',
                encoding="utf-8",
            )
            payload = {
                "unit_id": "u-local-span",
                "text": "数日後です。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "tokens": [
                                ["数", "スウ"],
                                ["日", "ジツ"],
                                ["後", "ゴ"],
                                ["です", "デス"],
                                ["。", "。"],
                            ],
                            "sudachi": {
                                "tokens": [
                                    token("数", "スウ"),
                                    token("日", "ジツ"),
                                    token("後", "ゴ"),
                                    token("です", "デス"),
                                    token("。", "。"),
                                ]
                            },
                        }
                    }
                },
            }
            records = build_pre_llm_safety_records(
                payload,
                stable_checker=StableSurfaceReadingLexicon.load_tsv(artifact),
            )
            set_yomi_safety_records(payload, records)

        by_surface = {record["surface"]: record for record in records}
        self.assertTrue(by_surface["数"]["is_safe"])
        self.assertTrue(by_surface["日"]["is_safe"])
        self.assertTrue(by_surface["後"]["is_safe"])
        day_signal = next(
            signal
            for signal in by_surface["日"]["signals"]
            if signal["name"] == "safe_by_local_stable_span"
        )
        self.assertEqual(day_signal["evidence_surface"], "数日")
        self.assertEqual(day_signal["count"], 27)
        self.assertEqual(safe_yomi_item_ids(payload), {record["item_id"] for record in records})

    def test_local_stable_span_does_not_accept_mismatched_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stable_surface_readings.tsv"
            artifact.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "segmentation_counts_json\tsource_corpus_version\n"
                '数日後\tスウジツゴ\t6\t6\t1\t[{"surfaces":["数","日","後"],"count":6}]\tfixture\n',
                encoding="utf-8",
            )
            payload = {
                "unit_id": "u-local-span-mismatch",
                "text": "数日後です。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "tokens": [
                                ["数", "スウ"],
                                ["日", "ニチ"],
                                ["後", "ゴ"],
                                ["です", "デス"],
                                ["。", "。"],
                            ],
                            "sudachi": {
                                "tokens": [
                                    token("数", "スウ"),
                                    token("日", "ニチ"),
                                    token("後", "ゴ"),
                                    token("です", "デス"),
                                    token("。", "。"),
                                ]
                            },
                        }
                    }
                },
            }
            records = build_pre_llm_safety_records(
                payload,
                stable_checker=StableSurfaceReadingLexicon.load_tsv(artifact),
            )

        self.assertFalse(any(record["is_safe"] for record in records))
        self.assertFalse(
            any(
                signal["name"] == "safe_by_local_stable_span"
                for record in records
                for signal in record["signals"]
            )
        )

    def test_local_stable_span_requires_matching_observed_segmentation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stable_surface_readings.tsv"
            artifact.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "segmentation_counts_json\tsource_corpus_version\n"
                '月末\tガツマツ\t65\t67\t0.970149\t'
                '[{"surfaces":["月","末"],"count":65}]\tfixture\n',
                encoding="utf-8",
            )
            lexicon = StableSurfaceReadingLexicon.load_tsv(artifact)

        self.assertTrue(
            lexicon.judge("月末", "ガツマツ", segmentation=("月", "末")).value
        )
        rejected = lexicon.judge("月末", "ガツマツ", segmentation=("月末",))
        self.assertFalse(rejected.value)
        self.assertEqual(rejected.reason, "stable_surface_segmentation_mismatch")
    def test_greek_reading_matching_sudachi_is_safe_without_llm(self) -> None:
        payload = {
            "unit_id": "u-greek",
            "text": "α波です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "α/アルファー 波/ハ です/デス 。/。",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "α",
                                    "pos": "記号,文字,*,*,*,*",
                                    "dictionary_form": "α",
                                    "normalized_form": "α",
                                    "reading": "アルファー",
                                },
                                token("波", "ハ"),
                                token("です", "デス"),
                                token("。", "。"),
                            ]
                        },
                    }
                }
            },
        }

        records = build_pre_llm_safety_records(payload)

        alpha = next(record for record in records if record["surface"] == "α")
        self.assertEqual(alpha["current_reading"], "アルファー")
        self.assertTrue(alpha["is_safe"])
        self.assertIn("safe_by_sudachi_greek", alpha["accepted_signal_names"])
        signal = next(
            row for row in alpha["signals"] if row["name"] == "safe_by_sudachi_greek"
        )
        self.assertEqual(signal["sudachi_reading"], "アルファー")

    def test_greek_reading_differing_from_sudachi_remains_unresolved(self) -> None:
        payload = {
            "unit_id": "u-greek-mismatch",
            "text": "αです。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "α/エー です/デス 。/。",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "α",
                                    "pos": "記号,文字,*,*,*,*",
                                    "dictionary_form": "α",
                                    "normalized_form": "α",
                                    "reading": "アルファー",
                                },
                                token("です", "デス"),
                                token("。", "。"),
                            ]
                        },
                    }
                }
            },
        }

        alpha = build_pre_llm_safety_records(payload)[0]

        self.assertFalse(alpha["is_safe"])
        self.assertNotIn("safe_by_sudachi_greek", alpha["accepted_signal_names"])

    def test_stable_surface_lexicon_rejects_wrong_mechanical_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stable_surface_readings.tsv"
            artifact.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "source_corpus_version\n"
                "一回\tイッカイ\t57\t57\t1\tfixture\n",
                encoding="utf-8",
            )
            payload = {
                "unit_id": "u-ikkai",
                "text": "一回です。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "sudachi": {
                                "tokens": [
                                    token("一回", "イチカイ"),
                                    token("です", "デス"),
                                    token("。", "。"),
                                ]
                            }
                        }
                    }
                },
            }

            records = build_pre_llm_safety_records(
                payload,
                stable_checker=StableSurfaceReadingLexicon.load_tsv(artifact),
            )

            record = next(row for row in records if row["surface"] == "一回")
            signal = next(
                row for row in record["signals"]
                if row["name"] == "safe_by_stable_surface_lexicon"
            )
            self.assertFalse(record["is_safe"])
            self.assertFalse(signal["accepted"])
            self.assertEqual(signal["reason"], "stable_surface_reading_mismatch:イッカイ")

    def test_single_japanese_numeral_does_not_prefer_no_ruby(self) -> None:
        payload = {
            "unit_id": "u-single-numeral",
            "text": "七時",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "七/ナナ 時/ジ",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "七",
                                    "pos": "名詞,数詞,*,*,*,*",
                                    "dictionary_form": "七",
                                    "normalized_form": "七",
                                    "reading": "ナナ",
                                },
                                {
                                    "surface": "時",
                                    "pos": "名詞,普通名詞,助数詞可能,*,*,*",
                                    "dictionary_form": "時",
                                    "normalized_form": "時",
                                    "reading": "ジ",
                                },
                            ]
                        },
                    }
                }
            },
        }

        records = build_pre_llm_safety_records(payload)

        numeral = next(record for record in records if record["surface"] == "七")
        self.assertNotIn("safe_by_no_ruby_numeric_surface", numeral["accepted_signal_names"])

    def test_japanese_numeral_targets_are_omitted_when_canonical_run_has_no_ruby(self) -> None:
        payload = {
            "unit_id": "u-numeral",
            "text": "二〇〇二年",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "二〇〇二/ 年/ネン",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "二〇〇二",
                                    "pos": "名詞,数詞,*,*,*,*",
                                    "dictionary_form": "二〇〇二",
                                    "normalized_form": "二〇〇二",
                                    "reading": "ニレイレイニ",
                                },
                                {
                                    "surface": "年",
                                    "pos": "名詞,普通名詞,助数詞可能,*,*,*",
                                    "dictionary_form": "年",
                                    "normalized_form": "年",
                                    "reading": "ネン",
                                },
                            ]
                        },
                    }
                }
            },
        }

        records = build_pre_llm_safety_records(payload)

        self.assertNotIn("二〇〇二", {record["surface"] for record in records})

    def test_japanese_numeral_proper_name_reading_is_not_forced_to_no_ruby(self) -> None:
        payload = {
            "unit_id": "u-numeral-name",
            "text": "加藤一二三",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "加藤/カトウ 一二三/ヒフミ",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "加藤",
                                    "pos": "名詞,固有名詞,人名,姓,*,*",
                                    "dictionary_form": "加藤",
                                    "normalized_form": "加藤",
                                    "reading": "カトウ",
                                },
                                {
                                    "surface": "一二三",
                                    "pos": "名詞,固有名詞,人名,名,*,*",
                                    "dictionary_form": "一二三",
                                    "normalized_form": "一二三",
                                    "reading": "ヒフミ",
                                },
                            ]
                        },
                    }
                }
            },
        }

        records = build_pre_llm_safety_records(payload)

        numeral = next(record for record in records if record["surface"] == "一二三")
        self.assertNotIn(
            "safe_by_no_ruby_numeric_surface", numeral["accepted_signal_names"]
        )

    def test_model_local_frequency_stats_override_configured_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback = root / "fallback.tsv"
            model_dir = root / "model"
            model_dir.mkdir()
            model_stats = model_dir / "surface_reading_stats.tsv"
            fallback.write_text("fallback", encoding="utf-8")
            model_stats.write_text("model", encoding="utf-8")

            resolved = resolve_corpus_frequency_stats_artifact(
                configured_path=fallback,
                decoder_model_dir=model_dir,
            )

            self.assertEqual(resolved, model_stats)

    def test_frequency_stats_fall_back_for_legacy_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback = root / "fallback.tsv"
            model_dir = root / "legacy-model"
            model_dir.mkdir()

            resolved = resolve_corpus_frequency_stats_artifact(
                configured_path=fallback,
                decoder_model_dir=model_dir,
            )

            self.assertEqual(resolved, fallback)

    def test_corpus_frequency_marks_matching_target_safe(self) -> None:
        stats = make_stats(
            [
                SurfaceReadingCount(
                    surface="学校",
                    reading="ガッコウ",
                    count=5,
                    surface_total_count=5,
                    share=1.0,
                    source_corpus_version="fixture",
                )
            ]
        )

        records = build_pre_llm_safety_records(unit(), corpus_stats=stats)

        by_surface = {record["surface"]: record for record in records}
        self.assertTrue(by_surface["学校"]["is_safe"])
        self.assertIn("safe_by_corpus_frequency", by_surface["学校"]["accepted_signal_names"])
        self.assertFalse(by_surface["上"]["is_safe"])

    def test_exact_frequency_does_not_hide_conflicting_cross_segmentation_reading(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stable_surface_readings.tsv"
            artifact.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "source_corpus_version\n"
                "学校\tガッコウ\t20\t20\t1\tfixture\n",
                encoding="utf-8",
            )
            payload = {
                "unit_id": "u-ichinichi",
                "text": "一日を過ごします。",
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "sudachi": {
                                "tokens": [
                                    token("一日", "ツイタチ"),
                                    token("を", "ヲ"),
                                    token("過ごし", "スゴシ", dictionary_form="過ごす"),
                                    token("ます", "マス"),
                                    token("。", "。"),
                                ]
                            }
                        }
                    }
                },
            }
            stats = make_stats(
                [
                    SurfaceReadingCount(
                        surface="一日",
                        reading="ツイタチ",
                        count=47,
                        surface_total_count=48,
                        share=47 / 48,
                        source_corpus_version="fixture",
                    )
                ]
            )

            records = build_pre_llm_safety_records(
                payload,
                stable_checker=StableSurfaceReadingLexicon.load_tsv(artifact),
                corpus_stats=stats,
            )

            record = next(row for row in records if row["surface"] == "一日")
            signal = next(
                row for row in record["signals"]
                if row["name"] == "safe_by_corpus_frequency"
            )
            self.assertFalse(record["is_safe"])
            self.assertFalse(signal["accepted"])
            self.assertEqual(signal["dominant"]["reading"], "ツイタチ")
            self.assertEqual(
                signal["pooled_surface_guard"]["reason"],
                "missing_stable_surface",
            )

    def test_exact_frequency_remains_safe_when_pooled_surface_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stable_surface_readings.tsv"
            artifact.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "source_corpus_version\n"
                "学校\tガッコウ\t20\t20\t1\tfixture\n",
                encoding="utf-8",
            )
            stats = make_stats(
                [
                    SurfaceReadingCount(
                        surface="学校",
                        reading="ガッコウ",
                        count=20,
                        surface_total_count=20,
                        share=1.0,
                        source_corpus_version="fixture",
                    )
                ]
            )

            records = build_pre_llm_safety_records(
                unit(),
                stable_checker=StableSurfaceReadingLexicon.load_tsv(artifact),
                corpus_stats=stats,
            )

            record = next(row for row in records if row["surface"] == "学校")
            signal = next(
                row for row in record["signals"]
                if row["name"] == "safe_by_corpus_frequency"
            )
            self.assertTrue(record["is_safe"])
            self.assertTrue(signal["accepted"])
            self.assertTrue(signal["pooled_surface_guard"]["accepted"])

    def test_corpus_frequency_marks_inflected_full_token_safe(self) -> None:
        payload = {
            "unit_id": "u_inflected",
            "text": "そう思った。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "sudachi": {
                            "tokens": [
                                token("そう", "ソウ"),
                                token("思っ", "オモッ", dictionary_form="思う"),
                                token("た", "タ"),
                                token("。", "。"),
                            ]
                        }
                    }
                }
            },
        }
        stats = make_stats(
            [
                SurfaceReadingCount(
                    surface="思っ",
                    reading="オモッ",
                    count=497,
                    surface_total_count=497,
                    share=1.0,
                    source_corpus_version="fixture",
                )
            ]
        )

        records = build_pre_llm_safety_records(payload, corpus_stats=stats)

        record = next(row for row in records if row["surface"] == "思")
        signal = next(row for row in record["signals"] if row["name"] == "safe_by_corpus_frequency")
        self.assertTrue(record["is_safe"])
        self.assertEqual(record["token_surface"], "思っ")
        self.assertEqual(signal["evidence_scope"], "token")
        self.assertEqual(signal["evidence_surface"], "思っ")
        self.assertEqual(signal["evidence_reading"], "オモッ")
        self.assertEqual(signal["dominant"]["count"], 497)

    def test_corpus_frequency_uses_trailing_kana_stem_after_sparse_exact_form(self) -> None:
        payload = {
            "unit_id": "u_inflection_family",
            "text": "そう思った。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "sudachi": {
                            "tokens": [
                                token("そう", "ソウ"),
                                token("思っ", "オモッ", dictionary_form="思う"),
                                token("た", "タ"),
                                token("。", "。"),
                            ]
                        }
                    }
                }
            },
        }
        stats = make_stats(
            [
                SurfaceReadingCount(
                    surface="思っ",
                    reading="オモッ",
                    count=1,
                    surface_total_count=1,
                    share=1.0,
                    source_corpus_version="fixture",
                ),
                SurfaceReadingCount(
                    surface="思",
                    reading="オモ",
                    count=20,
                    surface_total_count=20,
                    share=1.0,
                    source_corpus_version="fixture",
                    evidence_scope=EVIDENCE_SCOPE_TRAILING_KANA_STEM,
                ),
            ]
        )

        records = build_pre_llm_safety_records(payload, corpus_stats=stats)

        record = next(row for row in records if row["surface"] == "思")
        signal = next(row for row in record["signals"] if row["name"] == "safe_by_corpus_frequency")
        self.assertTrue(record["is_safe"])
        self.assertEqual(signal["evidence_scope"], EVIDENCE_SCOPE_TRAILING_KANA_STEM)
        self.assertEqual(signal["evidence_surface"], "思")
        self.assertEqual(signal["evidence_reading"], "オモ")
        self.assertEqual(signal["normalization_rule"], "strip_matching_trailing_hiragana_v1")
        self.assertEqual(signal["dominant"]["count"], 20)

    def test_corpus_frequency_accepts_95_percent_share_at_default_threshold(self) -> None:
        stats = make_stats(
            [
                SurfaceReadingCount(
                    surface="学校",
                    reading="ガッコウ",
                    count=19,
                    surface_total_count=20,
                    share=0.95,
                    source_corpus_version="fixture",
                )
            ]
        )

        records = build_pre_llm_safety_records(unit(), corpus_stats=stats)

        by_surface = {record["surface"]: record for record in records}
        self.assertTrue(by_surface["学校"]["is_safe"])

    def test_whole_unit_auto_accept_marks_all_targets_safe(self) -> None:
        payload = unit()
        payload["analysis"]["mechanical"]["yomi"]["auto_accept"] = {
            "value": True,
            "rule": "fixture_auto_accept",
        }

        records = build_pre_llm_safety_records(payload)

        self.assertEqual([record["surface"] for record in records], ["学校", "上"])
        self.assertTrue(all(record["is_safe"] for record in records))
        self.assertTrue(
            all("safe_by_unit_auto_accept" in record["accepted_signal_names"] for record in records)
        )

    def test_standalone_laughter_w_is_safe_with_no_ruby_preference(self) -> None:
        payload = {
            "unit_id": "u_w",
            "text": "ｗ　そして学校です。",
            "analysis": {
                "mechanical": {
                    "yomi": {
                        "rendered": "ｗ/ワット 　/　 そして/ソシテ 学校/ガッコウ です/デス 。/。",
                        "sudachi": {
                            "tokens": [
                                {
                                    "surface": "ｗ",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                    "dictionary_form": "ｗ",
                                    "normalized_form": "ｗ",
                                    "reading": "ワット",
                                },
                                {
                                    "surface": "　",
                                    "pos": "空白,*,*,*,*,*",
                                    "dictionary_form": "　",
                                    "normalized_form": "　",
                                    "reading": "　",
                                },
                                {
                                    "surface": "そして",
                                    "pos": "接続詞,*,*,*,*,*",
                                    "dictionary_form": "そして",
                                    "normalized_form": "そして",
                                    "reading": "ソシテ",
                                },
                                {
                                    "surface": "学校",
                                    "pos": "名詞,普通名詞,一般,*,*,*",
                                    "dictionary_form": "学校",
                                    "normalized_form": "学校",
                                    "reading": "ガッコウ",
                                },
                                {
                                    "surface": "です",
                                    "pos": "助動詞,*,*,*,助動詞-ダ,終止形-一般",
                                    "dictionary_form": "だ",
                                    "normalized_form": "だ",
                                    "reading": "デス",
                                },
                                {
                                    "surface": "。",
                                    "pos": "補助記号,句点,*,*,*,*",
                                    "dictionary_form": "。",
                                    "normalized_form": "。",
                                    "reading": "。",
                                },
                            ]
                        },
                    }
                }
            },
        }

        records = build_pre_llm_safety_records(payload)

        by_surface = {record["surface"]: record for record in records}
        self.assertTrue(by_surface["ｗ"]["is_safe"])
        self.assertIn("safe_by_no_ruby_laughter_w", by_surface["ｗ"]["accepted_signal_names"])
        signal = next(
            row for row in by_surface["ｗ"]["signals"] if row["name"] == "safe_by_no_ruby_laughter_w"
        )
        self.assertEqual(signal["preferred_choice_source"], "none")

    def test_queue_skips_targets_marked_safe_by_safety_records(self) -> None:
        payload = unit()
        records = build_pre_llm_safety_records(
            payload,
            corpus_stats=make_stats(
                [
                    SurfaceReadingCount(
                        surface="学校",
                        reading="ガッコウ",
                        count=5,
                        surface_total_count=5,
                        share=1.0,
                        source_corpus_version="fixture",
                    )
                ]
            ),
        )
        set_yomi_safety_records(payload, records)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            queue_path = root / "queue.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            summary = build_yomi_llm_reading_queue_file(
                input_jsonl=input_path,
                output_jsonl=queue_path,
                summary_json=summary_path,
                skip_stable_two_kanji=False,
            )

            rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["surface"] for row in rows], ["上"])
            self.assertEqual(summary.queued_items, 1)
            self.assertEqual(summary.safety_skipped, 1)


def make_stats(rows: list[SurfaceReadingCount]) -> SurfaceReadingStats:
    by_scope_surface = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_scope_surface[row.evidence_scope][row.surface].append(row)
    normalized = {
        scope: dict(by_surface) for scope, by_surface in by_scope_surface.items()
    }
    return SurfaceReadingStats(
        rows_by_surface=normalized.get("exact", {}),
        rows_by_scope_surface=normalized,
        source_corpus_version="fixture",
    )


def token(surface: str, reading: str, *, dictionary_form: str | None = None) -> dict[str, str]:
    return {
        "surface": surface,
        "pos": "動詞,一般,*,*,五段-ワア行,連用形-促音便",
        "dictionary_form": dictionary_form or surface,
        "normalized_form": dictionary_form or surface,
        "reading": reading,
    }


if __name__ == "__main__":
    unittest.main()
