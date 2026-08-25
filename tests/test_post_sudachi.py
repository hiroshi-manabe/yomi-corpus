from __future__ import annotations

import unittest

from yomi_corpus.yomi.post_sudachi import (
    NORMALIZER_VERSION,
    normalize_sudachi_tokens,
    normalized_sudachi_token_rows,
)
from yomi_corpus.yomi.strategies import apply_strategy, render_pairs_from_sudachi
from yomi_corpus.yomi.types import DecoderCandidate, DecoderEntry, SudachiToken


class PostSudachiNormalizationTests(unittest.TestCase):
    def test_preserves_source_and_records_structural_provenance(self) -> None:
        text = "Led Zeppelinと（株）BGM8"
        raw = [
            token("Led Zeppelin", "レッドツェッペリン", pos="名詞,固有名詞,一般,*,*,*"),
            token("と", "ト", pos="助詞,格助詞,*,*,*,*"),
            token("（株）", "カブシキガイシャ", pos="名詞,普通名詞,一般,*,*,*"),
            token("BGM8", "ビージーエムハチ", pos="名詞,固有名詞,一般,*,*,*"),
        ]

        result = normalize_sudachi_tokens(raw, text=text)

        self.assertEqual(result.normalizer_version, NORMALIZER_VERSION)
        self.assertEqual("".join(row.surface for row in result.tokens), text)
        self.assertEqual(
            [row.surface for row in result.tokens],
            ["Led", " ", "Zeppelin", "と", "（", "株", "）", "BGM", "8"],
        )
        self.assertEqual(result.tokens[1].reading, "")
        self.assertEqual(result.tokens[-1].reading, "")
        self.assertEqual(result.token_sources[:3], ((0,), (0,), (0,)))
        self.assertEqual(
            [row.rule_id for row in result.applications],
            [
                "split_space_spanning_sudachi_token",
                "split_parenthesis_spanning_sudachi_token",
                "split_mixed_arabic_numeric_token",
            ],
        )

    def test_applies_letter_kaomoji_and_lexical_defaults(self) -> None:
        text = "A（●＾o＾●）皆様"
        raw = [
            token("A", "アール"),
            token("（●＾o＾●）", "（●＾o＾●）", pos="補助記号,ＡＡ,顔文字,*,*,*"),
            token("皆", "ミナ"),
            token("様", "サマ"),
        ]

        result = normalize_sudachi_tokens(raw, text=text)

        self.assertEqual(
            [(row.surface, row.reading) for row in result.tokens],
            [("A", "エー"), ("（●＾o＾●）", "カオモジ"), ("皆様", "ミナサマ")],
        )
        self.assertEqual(result.token_sources, ((0,), (1,), (2, 3)))
        self.assertEqual(
            [row.rule_id for row in result.applications],
            [
                "normalize_uppercase_latin_letter_reading",
                "normalize_symbolic_sudachi_kaomoji",
                "canonicalize_minasama_boundary",
            ],
        )

    def test_joins_all_minasa_surface_variants(self) -> None:
        text = "皆様、皆さま、みな様、みなさま、皆さん、みなさん"
        raw = []
        for prefix, suffix in (
            ("皆", "様"),
            ("皆", "さま"),
            ("みな", "様"),
            ("みな", "さま"),
            ("皆", "さん"),
            ("みな", "さん"),
        ):
            if raw:
                raw.append(token("、", ""))
            raw.extend(
                [
                    token(prefix, "ミナ"),
                    token(suffix, "サマ" if suffix in {"様", "さま"} else "サン"),
                ]
            )

        result = normalize_sudachi_tokens(raw, text=text)

        self.assertEqual(
            [(row.surface, row.reading) for row in result.tokens],
            [
                ("皆様", "ミナサマ"),
                ("、", ""),
                ("皆さま", "ミナサマ"),
                ("、", ""),
                ("みな様", "ミナサマ"),
                ("、", ""),
                ("みなさま", "ミナサマ"),
                ("、", ""),
                ("皆さん", "ミナサン"),
                ("、", ""),
                ("みなさん", "ミナサン"),
            ],
        )
        self.assertEqual(
            [row.rule_id for row in result.applications],
            ["canonicalize_minasama_boundary"] * 6,
        )

    def test_is_idempotent_at_the_token_stream_level(self) -> None:
        text = "A ラ・カンパネラ 皆様"
        first = normalize_sudachi_tokens(
            [
                token("A", "アール"),
                token(" ", "キゴウ", pos="空白,*,*,*,*,*"),
                token("ラ・カンパネラ", "ラカンパネラ"),
                token(" ", "キゴウ", pos="空白,*,*,*,*,*"),
                token("皆", "ミナ"),
                token("様", "サマ"),
            ],
            text=text,
        )
        second = normalize_sudachi_tokens(first.tokens, text=text)

        self.assertEqual(second.tokens, first.tokens)
        self.assertEqual(
            render_pairs_from_sudachi(second.tokens),
            render_pairs_from_sudachi(first.tokens),
        )
        self.assertEqual(second.applications, ())

    def test_rejects_a_non_source_preserving_stream(self) -> None:
        with self.assertRaisesRegex(ValueError, "do not reproduce source text"):
            normalize_sudachi_tokens([token("AB", "エービー")], text="AC")

    def test_structural_normalization_preserves_legacy_rendered_output(self) -> None:
        text = "Led Zeppelin（株）（●＾o＾●）"
        raw = [
            token("Led Zeppelin", "レッドツェッペリン", pos="名詞,固有名詞,一般,*,*,*"),
            token("（株）", "カブシキガイシャ"),
            token("（●＾o＾●）", "（●＾o＾●）", pos="補助記号,ＡＡ,顔文字,*,*,*"),
        ]
        normalized = normalize_sudachi_tokens(raw, text=text)

        legacy = apply_strategy(
            "sudachi_only_v1",
            text=text,
            sudachi_tokens=raw,
            decoder_candidates=[],
        )
        migrated = apply_strategy(
            "sudachi_only_v1",
            text=text,
            sudachi_tokens=list(normalized.tokens),
            decoder_candidates=[],
        )

        self.assertEqual(migrated.rendered, legacy.rendered)

    def test_artifact_reader_prefers_normalized_tokens_and_falls_back(self) -> None:
        raw = {"surface": "A", "reading": "アール"}
        normalized = {"surface": "A", "reading": "エー"}

        self.assertEqual(
            normalized_sudachi_token_rows(
                {
                    "sudachi": {
                        "tokens": [raw],
                        "normalized": {"tokens": [normalized]},
                    }
                }
            ),
            [normalized],
        )
        self.assertEqual(
            normalized_sudachi_token_rows({"sudachi": {"tokens": [raw]}}),
            [raw],
        )

    def test_hybrid_decoder_cannot_undo_a_locked_normalization(self) -> None:
        normalized = normalize_sudachi_tokens(
            [token("A", "アール"), token("皆", "ミナ"), token("様", "サマ")],
            text="A皆様",
        )
        decoder = DecoderCandidate(
            rank=1,
            score=0.0,
            entries=[
                DecoderEntry("A", "アール", 2, [2]),
                DecoderEntry("皆", "ミナ", 2, [2]),
                DecoderEntry("様", "サマ", 2, [2]),
            ],
        )

        result = apply_strategy(
            "ngram_grouping_preferred_v1",
            text="A皆様",
            sudachi_tokens=list(normalized.tokens),
            decoder_candidates=[decoder],
        )

        self.assertEqual(result.rendered, "A/エー 皆様/ミナサマ")
        self.assertIn("preserve_post_sudachi_normalization", result.signals)

    def test_parenthesized_lexical_component_does_not_keep_punctuation_pos(self) -> None:
        normalized = normalize_sudachi_tokens(
            [token("（株）", "カブシキガイシャ", pos="補助記号,ＡＡ,一般,*,*,*")],
            text="（株）",
        )
        decoder = DecoderCandidate(
            rank=1,
            score=0.0,
            entries=[
                DecoderEntry("（", "", 1, [1]),
                DecoderEntry("株", "株", 2, [2]),
                DecoderEntry("）", "", 1, [1]),
            ],
        )

        result = apply_strategy(
            "ngram_grouping_preferred_v1",
            text="（株）",
            sudachi_tokens=list(normalized.tokens),
            decoder_candidates=[decoder],
        )

        self.assertEqual(result.rendered, "（/（ 株/カブ ）/）")


def token(surface: str, reading: str, *, pos: str = "名詞,普通名詞,一般,*,*,*") -> SudachiToken:
    return SudachiToken(
        surface=surface,
        pos=pos,
        dictionary_form=surface,
        normalized_form=surface,
        reading=reading,
    )


if __name__ == "__main__":
    unittest.main()
