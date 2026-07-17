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
    set_yomi_safety_records,
)


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
