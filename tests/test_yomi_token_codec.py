from __future__ import annotations

import unittest

from yomi_corpus.yomi.token_codec import (
    YomiTokenError,
    editable_rendered_to_yomi_tokens,
    legacy_rendered_to_yomi_tokens,
    yomi_tokens_to_editable_rendered,
)


class YomiTokenCodecTests(unittest.TestCase):
    def test_legacy_alignment_recovers_literal_slash(self) -> None:
        self.assertEqual(
            legacy_rendered_to_yomi_tokens("3/ /// 22/", text="3/22"),
            [["3", ""], ["/", "/"], ["22", ""]],
        )

    def test_editable_format_escapes_slashes(self) -> None:
        tokens = [["3", ""], ["/", "/"], ["22", ""]]
        rendered = yomi_tokens_to_editable_rendered(tokens)

        self.assertEqual(rendered, r"3/ \//\/ 22/")
        self.assertEqual(
            editable_rendered_to_yomi_tokens(rendered, text="3/22"),
            tokens,
        )

    def test_editable_format_escapes_ascii_space_tokens(self) -> None:
        tokens = [["A", "エー"], [" ", ""], ["B", "ビー"]]
        rendered = yomi_tokens_to_editable_rendered(tokens)

        self.assertEqual(rendered, r"A/エー \s/ B/ビー")
        self.assertEqual(editable_rendered_to_yomi_tokens(rendered, text="A B"), tokens)

    def test_legacy_alignment_preserves_source_ascii_space(self) -> None:
        self.assertEqual(
            legacy_rendered_to_yomi_tokens("A/エー \u00a0/\u00a0 B/ビー", text="A B"),
            [["A", "エー"], [" ", "\u00a0"], ["B", "ビー"]],
        )

    def test_legacy_bare_slash_recovers_collapsed_ascii_space_token(self) -> None:
        self.assertEqual(
            legacy_rendered_to_yomi_tokens("A/エー / B/ビー", text="A B"),
            [["A", "エー"], [" ", ""], ["B", "ビー"]],
        )

    def test_legacy_phantom_bare_slashes_are_dropped(self) -> None:
        self.assertEqual(
            legacy_rendered_to_yomi_tokens("…/… / / 次/ツギ", text="…次"),
            [["…", "…"], ["次", "ツギ"]],
        )

    def test_explicit_nbsp_space_wins_over_phantom_bare_slashes(self) -> None:
        self.assertEqual(
            legacy_rendered_to_yomi_tokens("…/… / / \u00a0/\u00a0 次/ツギ", text="… 次"),
            [["…", "…"], [" ", "\u00a0"], ["次", "ツギ"]],
        )

    def test_mismatched_surface_is_rejected(self) -> None:
        with self.assertRaises(YomiTokenError):
            legacy_rendered_to_yomi_tokens("別/ベツ", text="違")


if __name__ == "__main__":
    unittest.main()
