from __future__ import annotations

from pathlib import Path
import runpy
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTING_EVAL = runpy.run_path(
    str(PROJECT_ROOT / "scripts" / "build_yomi_reading_routing_eval.py"),
    run_name="yomi_reading_routing_eval_test",
)
is_absorbed_numeric_compound_target = ROUTING_EVAL[
    "is_absorbed_numeric_compound_target"
]
CURATED_EXPECTED_READING_OVERRIDES = ROUTING_EVAL[
    "CURATED_EXPECTED_READING_OVERRIDES"
]
CURATED_ACCEPTABLE_READING_OVERRIDES = ROUTING_EVAL[
    "CURATED_ACCEPTABLE_READING_OVERRIDES"
]


class YomiReadingRoutingEvalTests(unittest.TestCase):
    def assert_absorbed(self, text: str, surface: str) -> None:
        start = text.index(surface)
        self.assertTrue(
            is_absorbed_numeric_compound_target(
                text=text,
                start=start,
                end=start + len(surface),
                surface=surface,
            )
        )

    def assert_retained(self, text: str, surface: str) -> None:
        start = text.index(surface)
        self.assertFalse(
            is_absorbed_numeric_compound_target(
                text=text,
                start=start,
                end=start + len(surface),
                surface=surface,
            )
        )

    def test_excludes_configured_numeric_compound_targets(self) -> None:
        self.assert_absorbed("2人で行く", "人")
        self.assert_absorbed("２人で行く", "人")
        self.assert_absorbed("3月10日です", "日")
        self.assert_absorbed("9月20日です", "日")

    def test_retains_ordinary_and_generic_counter_targets(self) -> None:
        self.assert_retained("人が行く", "人")
        self.assert_retained("4人で行く", "人")
        self.assert_retained("良い日です", "日")
        self.assert_retained("3月15日です", "日")

    def test_corrects_compound_voicing_missed_during_final_review(self) -> None:
        self.assertEqual(
            CURATED_EXPECTED_READING_OVERRIDES[
                "ja_cc_level2:0000000026:u0021:r0003c01"
            ],
            "び",
        )

    def test_preserves_valid_contextual_variants(self) -> None:
        self.assertEqual(
            CURATED_ACCEPTABLE_READING_OVERRIDES[
                "ja_cc_level2:0000000025:u0108:r0016c01"
            ],
            ["い", "ゆ"],
        )
        self.assertEqual(
            CURATED_ACCEPTABLE_READING_OVERRIDES[
                "ja_cc_level2:0000000016:u0036:r0003c01"
            ],
            ["ちゅう", "じゅう"],
        )


if __name__ == "__main__":
    unittest.main()
