from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.furigana import (
    FuriganaConverter,
    has_han,
    is_han,
    is_variation_selector,
    parse_annotated_chunks,
)


def write_lookup(path: Path, rows: list[tuple[str, str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["surface", "reading", "annotated_surface"])
        writer.writerows(rows)


class FuriganaConverterTests(unittest.TestCase):
    def test_supplementary_cjk_characters_are_han(self) -> None:
        self.assertTrue(is_han("𠮟"))
        self.assertTrue(is_han("𩸽"))
        self.assertTrue(has_han("𠮟られる"))
        self.assertFalse(is_han("学校"))

    def test_supplementary_cjk_characters_receive_furigana(self) -> None:
        converter = FuriganaConverter()

        self.assertEqual(converter.convert("𠮟られる", "シカラレル").annotated_surface, "𠮟（しか）られる")
        self.assertEqual(converter.convert("𩸽", "ホッケ").annotated_surface, "𩸽（ほっけ）")

    def test_variation_selector_extends_preceding_han_surface(self) -> None:
        converter = FuriganaConverter()

        self.assertTrue(is_variation_selector("󠄀"))
        self.assertFalse(is_han("󠄀"))
        self.assertEqual(
            converter.convert("禰󠄀豆子", "ネズコ").annotated_surface,
            "禰󠄀豆子（ねずこ）",
        )

    def test_parse_annotated_chunks(self) -> None:
        self.assertEqual(
            parse_annotated_chunks("読（よ）み仮名（がな）"),
            [("読", "よ"), ("仮名", "がな")],
        )

    def test_exact_lookup_wins(self) -> None:
        converter = FuriganaConverter(
            exact_lookup={("送っ", "オクッ"): {"送（おく）っ"}},
        )
        result = converter.convert("送っ", "オクッ")
        self.assertEqual(result.annotated_surface, "送（おく）っ")
        self.assertEqual(result.method, "exact_lookup")
        self.assertEqual(result.confidence, "high")

    def test_from_tsv_many_merges_supplemental_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.tsv"
            supplemental = root / "supplemental.tsv"
            write_lookup(base, [("送っ", "オクッ", "送（おく）っ")])
            write_lookup(supplemental, [("決め", "キメ", "決（き）め")])

            converter = FuriganaConverter.from_tsv_many([base, supplemental])

            self.assertEqual(converter.convert("送っ", "オクッ").annotated_surface, "送（おく）っ")
            self.assertEqual(converter.convert("決め", "キメ").annotated_surface, "決（き）め")

    def test_plain_digit_han_lookup_blocks_bad_fallback_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lookup = Path(tmp) / "lookup.tsv"
            write_lookup(lookup, [("1人1人", "ヒトリヒトリ", "1人1人")])

            converter = FuriganaConverter.from_tsv(lookup)
            result = converter.convert("1人1人", "ヒトリヒトリ")

            self.assertEqual(result.annotated_surface, "1人1人")
            self.assertEqual(result.method, "exact_lookup")
            self.assertEqual(result.confidence, "high")

    def test_unique_alignment_handles_okurigana(self) -> None:
        converter = FuriganaConverter()
        result = converter.convert("読み仮名", "ヨミガナ")
        self.assertEqual(result.annotated_surface, "読（よ）み仮名（がな）")
        self.assertEqual(result.method, "unique_alignment")

    def test_ke_variant_allows_ga_reading_without_exact_lookup(self) -> None:
        converter = FuriganaConverter()
        result = converter.convert("里ヶ浦", "サトガウラ")
        self.assertEqual(result.annotated_surface, "里（さと）ヶ浦（うら）")
        self.assertEqual(result.method, "unique_alignment")
        self.assertEqual(result.confidence, "high")

    def test_scored_alignment_uses_dictionary_evidence(self) -> None:
        converter = FuriganaConverter(
            evidence={
                ("大", "たい"): 20,
                ("麻", "ま"): 20,
                ("大", "た"): 1,
                ("麻", "いま"): 1,
            }
        )
        result = converter.convert("大・麻", "タイマ")
        self.assertEqual(result.annotated_surface, "大（たい）・麻（ま）")
        self.assertEqual(result.method, "scored_alignment")

    def test_scored_alignment_resolves_non_unique_okurigana_like_case(self) -> None:
        converter = FuriganaConverter(
            evidence={
                ("篠", "しの"): 50,
                ("里", "さと"): 50,
                ("篠", "し"): 1,
                ("里", "のさと"): 1,
            }
        )
        result = converter.convert("篠の里", "シノノサト")
        self.assertEqual(result.annotated_surface, "篠（しの）の里（さと）")
        self.assertEqual(result.method, "scored_alignment")
        self.assertEqual(result.confidence, "medium")

    def test_exact_ambiguous_is_not_silently_chosen(self) -> None:
        converter = FuriganaConverter(
            exact_lookup={("市場", "シジョウ"): {"市場（しじょう）", "市（し）場（じょう）"}},
        )
        result = converter.convert("市場", "シジョウ")
        self.assertIsNone(result.annotated_surface)
        self.assertEqual(result.method, "exact_lookup_ambiguous")
        self.assertEqual(result.confidence, "ambiguous")

    def test_cli_outputs_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lookup = root / "lookup.tsv"
            write_lookup(lookup, [("送っ", "オクッ", "送（おく）っ")])
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/render_furigana_form.py",
                    "--lookup",
                    str(lookup),
                    "--surface",
                    "送っ",
                    "--reading",
                    "オクッ",
                    "--format",
                    "jsonl",
                ],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )
            row = json.loads(completed.stdout)
            self.assertEqual(row["annotated_surface"], "送（おく）っ")
            self.assertEqual(row["method"], "exact_lookup")


if __name__ == "__main__":
    unittest.main()
