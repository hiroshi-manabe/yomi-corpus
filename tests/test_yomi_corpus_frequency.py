from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.corpus_frequency import (
    EVIDENCE_SCOPE_TRAILING_KANA_STEM,
    SurfaceReadingStats,
    build_surface_reading_stats,
    iter_source_corpus_tokens,
    trailing_kana_stem_pair,
)


FIXTURE = Path(__file__).parent / "fixtures" / "yomi_source_corpus_small.txt"


class YomiCorpusFrequencyTests(unittest.TestCase):
    def test_iter_source_corpus_tokens_reads_surface_and_reading(self) -> None:
        tokens = list(iter_source_corpus_tokens(FIXTURE))

        self.assertEqual(tokens[0].surface, "学校")
        self.assertEqual(tokens[0].reading, "ガッコウ")
        self.assertIn(("大麻", "オオアサ"), [(token.surface, token.reading) for token in tokens])
        self.assertNotIn("EOS", [token.surface for token in tokens])
        self.assertNotIn(("。", ""), [(token.surface, token.reading) for token in tokens])

    def test_builds_target_surface_stats_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stats_path = root / "stats.tsv"
            manifest_path = root / "manifest.json"

            summary = build_surface_reading_stats(
                source_corpus=FIXTURE,
                output_tsv=stats_path,
                manifest_json=manifest_path,
                source_corpus_version="fixture_v1",
                surface_filter="target",
                checksum=True,
            )

            self.assertEqual(summary.token_count, 18)
            self.assertEqual(summary.counted_token_count, 11)
            self.assertEqual(summary.skipped_malformed_line_count, 0)
            self.assertEqual(summary.surface_count, 8)
            self.assertTrue(summary.checksum_sha256)

            stats = SurfaceReadingStats.load_tsv(stats_path)
            school = stats.dominant_reading("学校", min_count=2, min_share=0.995)
            self.assertIsNotNone(school)
            assert school is not None
            self.assertEqual(school.reading, "ガッコウ")
            self.assertTrue(stats.matches_dominant("学校", "ガッコウ", min_count=2, min_share=0.995))
            self.assertFalse(stats.matches_dominant("学校", "ガクコウ", min_count=2, min_share=0.995))
            self.assertIsNone(stats.dominant_reading("方", min_count=2, min_share=0.995))
            self.assertIsNone(stats.dominant_reading("大麻", min_count=2, min_share=0.995))
            self.assertIsNone(stats.dominant_reading("先生", min_count=2, min_share=0.995))

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["script_version"], "surface_reading_stats_v2")
            self.assertEqual(manifest["source_corpus_version"], "fixture_v1")
            self.assertEqual(manifest["filters"]["surface_filter"], "target")
            self.assertEqual(manifest["summary"]["pair_count"], 10)
            self.assertEqual(manifest["summary"]["skipped_malformed_line_count"], 0)

    def test_combines_base_and_reviewed_corpora(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.txt"
            reviewed = root / "reviewed.txt"
            stats_path = root / "stats.tsv"
            manifest_path = root / "manifest.json"
            base.write_text("学校\t*\t学校\t学校\tガッコウ\nEOS\n", encoding="utf-8")
            reviewed.write_text(
                "学校\t*\t学校\t学校\tガクコウ\n追加\t*\t追加\t追加\tツイカ\nEOS\n",
                encoding="utf-8",
            )

            summary = build_surface_reading_stats(
                source_corpus=base,
                additional_source_corpora=[reviewed],
                output_tsv=stats_path,
                manifest_json=manifest_path,
                source_corpus_version="model_v1",
            )

            self.assertEqual(summary.token_count, 3)
            stats = SurfaceReadingStats.load_tsv(stats_path)
            self.assertEqual(len(stats.rows_by_surface["学校"]), 2)
            self.assertEqual(stats.rows_by_surface["追加"][0].count, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_corpus_paths"], [str(base), str(reviewed)])
            self.assertEqual(len(manifest["source_corpora"]), 2)

    def test_loads_legacy_exact_only_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats_path = Path(tmp) / "legacy.tsv"
            stats_path.write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "source_corpus_version\n"
                "学校\tガッコウ\t5\t5\t1\tlegacy_v1\n",
                encoding="utf-8",
            )

            stats = SurfaceReadingStats.load_tsv(stats_path)

            dominant = stats.dominant_reading("学校", min_count=5, min_share=0.95)
            self.assertIsNotNone(dominant)
            assert dominant is not None
            self.assertEqual(dominant.reading, "ガッコウ")
            self.assertEqual(dominant.evidence_scope, "exact")

    def test_builds_separate_trailing_kana_stem_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "source.txt"
            stats_path = root / "stats.tsv"
            manifest_path = root / "manifest.json"
            corpus.write_text(
                "".join(
                    [
                        "思う\t*\t思う\t思う\tオモウ\n",
                        "思い\t*\t思う\t思う\tオモイ\n",
                        "思っ\t*\t思う\t思う\tオモッ\n",
                        "思わ\t*\t思う\t思う\tオモワ\n",
                        "思え\t*\t思う\t思う\tオモエ\n",
                        "思\t*\t思\t思\tオモ\n",
                        "勝つ\t*\t勝つ\t勝つ\tカツ\n" * 5,
                        "勝る\t*\t勝る\t勝る\tマサル\n" * 5,
                        "EOS\n",
                    ]
                ),
                encoding="utf-8",
            )

            summary = build_surface_reading_stats(
                source_corpus=corpus,
                output_tsv=stats_path,
                manifest_json=manifest_path,
                source_corpus_version="stem_fixture",
                checksum=False,
            )

            stats = SurfaceReadingStats.load_tsv(stats_path)
            self.assertIsNone(stats.dominant_reading("思っ", min_count=5, min_share=0.95))
            stem = stats.dominant_reading(
                "思",
                min_count=5,
                min_share=0.95,
                evidence_scope=EVIDENCE_SCOPE_TRAILING_KANA_STEM,
            )
            self.assertIsNotNone(stem)
            assert stem is not None
            self.assertEqual((stem.reading, stem.count), ("オモ", 5))
            self.assertEqual(stats.rows_by_surface["思"][0].count, 1)
            self.assertIsNone(
                stats.dominant_reading(
                    "勝",
                    min_count=5,
                    min_share=0.95,
                    evidence_scope=EVIDENCE_SCOPE_TRAILING_KANA_STEM,
                )
            )
            self.assertEqual(summary.trailing_kana_stem_token_count, 15)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["normalization"]["trailing_kana_stem_rule"],
                "strip_matching_trailing_hiragana_v1",
            )

    def test_trailing_kana_stem_requires_matching_reading_suffix(self) -> None:
        self.assertEqual(
            trailing_kana_stem_pair("思い知っ", "オモイシッ"),
            ("思い知", "オモイシ"),
        )
        self.assertEqual(trailing_kana_stem_pair("赤かぶ", "アカカブ"), ("赤", "アカ"))
        self.assertIsNone(trailing_kana_stem_pair("思", "オモ"))
        self.assertIsNone(trailing_kana_stem_pair("これは", "コレワ"))


if __name__ == "__main__":
    unittest.main()
