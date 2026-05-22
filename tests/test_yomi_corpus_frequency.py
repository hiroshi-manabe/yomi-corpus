from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.corpus_frequency import (
    SurfaceReadingStats,
    build_surface_reading_stats,
    iter_source_corpus_tokens,
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
            self.assertEqual(manifest["script_version"], "surface_reading_stats_v1")
            self.assertEqual(manifest["source_corpus_version"], "fixture_v1")
            self.assertEqual(manifest["filters"]["surface_filter"], "target")
            self.assertEqual(manifest["summary"]["pair_count"], 10)
            self.assertEqual(manifest["summary"]["skipped_malformed_line_count"], 0)


if __name__ == "__main__":
    unittest.main()
