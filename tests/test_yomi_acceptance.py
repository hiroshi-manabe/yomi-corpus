from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.acceptance import (
    AUTO_ACCEPT_RULE,
    AUTO_ACCEPT_PROFILE_OFF,
    AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI,
    apply_yomi_auto_acceptance_file,
    judge_yomi_auto_accept,
)
from yomi_corpus.yomi.ngram_diagnostics import StableTwoKanjiChecker


def unit(
    text: str,
    rendered: str,
    *,
    sudachi_rendered: str | None = None,
    decoder_rendered: str | None = None,
    entries: list[dict] | None = None,
) -> dict:
    if sudachi_rendered is None:
        sudachi_rendered = rendered
    if decoder_rendered is None:
        decoder_rendered = rendered
    if entries is None:
        entries = fully_supported_entries(rendered)
    return {
        "unit_id": "u1",
        "text": text,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": rendered,
                    "certain": False,
                    "sudachi": {
                        "rendered": sudachi_rendered,
                    },
                    "ngram_decoder": {
                        "candidates": [
                            {
                                "rank": 1,
                                "score": -1.0,
                                "rendered": decoder_rendered,
                                "entries": entries,
                            }
                        ],
                    },
                }
            }
        },
    }


def fully_supported_entries(rendered: str) -> list[dict]:
    entries = []
    for index, pair in enumerate(rendered.split()):
        surface, reading = pair.rsplit("/", 1)
        entries.append(
            {
                "surface": surface,
                "reading": reading,
                "final_order": 2,
                "piece_orders": [2] if index else [1, 2],
            }
        )
    return entries


class YomiAcceptanceTests(unittest.TestCase):
    def test_accepts_when_sudachi_and_decoder_agree_with_full_support(self) -> None:
        judgment = judge_yomi_auto_accept(
            unit("大学に行く。", "大学/ダイガク に/ニ 行く/イク 。/。")
        )
        self.assertTrue(judgment.value)
        self.assertEqual(judgment.rule, AUTO_ACCEPT_RULE)
        self.assertIn("sudachi_decoder_agree", judgment.signals)
        self.assertIn("decoder_full_repeated_ngram_support", judgment.signals)

    def test_accepts_grouped_numeric_run_with_empty_reading(self) -> None:
        judgment = judge_yomi_auto_accept(unit("2021です。", "2021/ です/デス 。/。"))
        self.assertTrue(judgment.value)

    def test_accepts_grouped_japanese_numeral_run_with_empty_reading(self) -> None:
        judgment = judge_yomi_auto_accept(unit("二〇〇二年です。", "二〇〇二/ 年/ネン です/デス 。/。"))
        self.assertTrue(judgment.value)

    def test_auto_accept_profile_off_rejects_even_supported_unit(self) -> None:
        judgment = judge_yomi_auto_accept(
            unit("大学に行く。", "大学/ダイガク に/ニ 行く/イク 。/。"),
            auto_accept_profile=AUTO_ACCEPT_PROFILE_OFF,
        )
        self.assertFalse(judgment.value)
        self.assertEqual(judgment.signals, ["auto_accept_profile_off"])

    def test_rejects_when_sudachi_and_decoder_disagree(self) -> None:
        judgment = judge_yomi_auto_accept(
            unit(
                "中は本屋です。",
                "中/チュウ は/ハ 本屋/ホンヤ です/デス 。/。",
                sudachi_rendered="中/ナカ は/ハ 本屋/ホンヤ です/デス 。/。",
                decoder_rendered="中/チュウ は/ハ 本屋/ホンヤ です/デス 。/。",
            )
        )
        self.assertFalse(judgment.value)
        self.assertIn("sudachi_decoder_disagree", judgment.signals)

    def test_rejects_when_first_entry_lacks_repeated_support(self) -> None:
        rendered = "大学/ダイガク に/ニ 行く/イク 。/。"
        entries = fully_supported_entries(rendered)
        entries[0]["final_order"] = 1
        judgment = judge_yomi_auto_accept(unit("大学に行く。", rendered, entries=entries))
        self.assertFalse(judgment.value)
        self.assertIn("decoder_lacks_full_repeated_ngram_support", judgment.signals)

    def test_rejects_when_later_boundary_lacks_repeated_support(self) -> None:
        rendered = "大学/ダイガク に/ニ 行く/イク 。/。"
        entries = fully_supported_entries(rendered)
        entries[2]["piece_orders"] = [1]
        judgment = judge_yomi_auto_accept(unit("大学に行く。", rendered, entries=entries))
        self.assertFalse(judgment.value)
        self.assertIn("decoder_lacks_full_repeated_ngram_support", judgment.signals)

    def test_rejects_missing_decoder_candidate(self) -> None:
        row = unit("大学です。", "大学/ダイガク です/デス 。/。")
        row["analysis"]["mechanical"]["yomi"]["ngram_decoder"]["candidates"] = []
        judgment = judge_yomi_auto_accept(row)
        self.assertFalse(judgment.value)
        self.assertIn("missing_decoder_candidate", judgment.signals)

    def test_rejects_empty_non_numeric_reading(self) -> None:
        judgment = judge_yomi_auto_accept(unit("です。", "です/ 。/。"))
        self.assertFalse(judgment.value)
        self.assertIn("has_unresolved_non_numeric_reading", judgment.signals)

    def test_stable_two_kanji_relaxation_accepts_unique_raw_sudachi_reading(self) -> None:
        checker = make_stable_checker(
            "記事,5146,5146,7253,記事,名詞,普通名詞,一般,*,*,*,キジ,記事,*,A,*,*,*,*\n"
        )
        rendered = "記事/キジ です/デス 。/。"
        entries = fully_supported_entries(rendered)
        entries[0]["final_order"] = 1
        entries[0]["piece_orders"] = [1]

        judgment = judge_yomi_auto_accept(
            unit("記事です。", rendered, entries=entries),
            auto_accept_profile=AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI,
            stable_two_kanji_checker=checker,
        )

        self.assertTrue(judgment.value)
        self.assertIn("decoder_lacks_full_repeated_ngram_support", judgment.signals)
        self.assertIn("decoder_full_support_with_stable_two_kanji_relaxation", judgment.signals)
        self.assertIn("stable_two_kanji_relaxation_used", judgment.signals)

    def test_stable_two_kanji_relaxation_does_not_accept_ambiguous_raw_sudachi_reading(self) -> None:
        checker = make_stable_checker(
            "大麻,5146,5146,7253,大麻,名詞,普通名詞,一般,*,*,*,タイマ,大麻,*,A,*,*,*,*\n"
            "大麻,-1,-1,0,大麻,名詞,固有名詞,地名,一般,*,*,オオアサ,大麻,*,A,*,*,*,*\n"
        )
        rendered = "大麻/タイマ です/デス 。/。"
        entries = fully_supported_entries(rendered)
        entries[0]["final_order"] = 1
        entries[0]["piece_orders"] = [1]

        judgment = judge_yomi_auto_accept(
            unit("大麻です。", rendered, entries=entries),
            auto_accept_profile=AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI,
            stable_two_kanji_checker=checker,
        )

        self.assertFalse(judgment.value)
        self.assertIn("stable_two_kanji_relaxation_failed", judgment.signals)

    def test_file_application_writes_judgments_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps(unit("ありがとう。", "ありがとう/アリガトウ 。/。"), ensure_ascii=False),
                        json.dumps(
                            unit(
                                "大学です。",
                                "大学/ダイガク です/デス 。/。",
                                decoder_rendered="大学/ダイガク です/デス 。/。",
                                entries=[
                                    {"surface": "大学", "reading": "ダイガク", "final_order": 1, "piece_orders": [1]},
                                    {"surface": "です", "reading": "デス", "final_order": 2, "piece_orders": [2]},
                                    {"surface": "。", "reading": "。", "final_order": 2, "piece_orders": [2]},
                                ],
                            ),
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_auto_acceptance_file(
                input_jsonl=input_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.rejected, 1)
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(rows[0]["analysis"]["mechanical"]["yomi"]["auto_accept"]["value"])
            self.assertFalse(rows[1]["analysis"]["mechanical"]["yomi"]["auto_accept"]["value"])
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["accepted"], 1)

    def test_file_application_can_enable_stable_two_kanji_relaxation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            (raw_dir / "core_lex.csv").write_text(
                "記事,5146,5146,7253,記事,名詞,普通名詞,一般,*,*,*,キジ,記事,*,A,*,*,*,*\n",
                encoding="utf-8",
            )
            input_path = root / "input.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            rendered = "記事/キジ です/デス 。/。"
            entries = fully_supported_entries(rendered)
            entries[0]["final_order"] = 1
            entries[0]["piece_orders"] = [1]
            input_path.write_text(
                json.dumps(unit("記事です。", rendered, entries=entries), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_auto_acceptance_file(
                input_jsonl=input_path,
                output_jsonl=output_path,
                summary_json=summary_path,
                enable_stable_two_kanji=True,
                raw_sudachi_dict_dir=raw_dir,
            )

            self.assertEqual(summary.accepted, 1)
            self.assertTrue(summary.stable_two_kanji_enabled)
            self.assertEqual(summary.auto_accept_profile, AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI)
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["stable_two_kanji_enabled"])
            self.assertEqual(payload["auto_accept_profile"], AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI)

    def test_file_application_uses_pinned_stable_surface_lexicon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "stable_surface_readings.tsv").write_text(
                "surface\treading\tcount\tsurface_total_count\tshare\t"
                "min_span_tokens\tmax_span_tokens\tsegmentation_counts_json\t"
                "source_corpus_version\n"
                "一回\tイッカイ\t57\t57\t1\t1\t2\t[]\tfixture\n"
                "株式会社\tカブシキガイシャ\t38\t38\t1\t1\t2\t[]\tfixture\n",
                encoding="utf-8",
            )
            input_path = root / "input.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            rows = []
            for text, rendered in [
                ("株式会社です。", "株式会社/カブシキガイシャ です/デス 。/。"),
                ("一回です。", "一回/イチカイ です/デス 。/。"),
            ]:
                entries = fully_supported_entries(rendered)
                entries[0]["final_order"] = 1
                entries[0]["piece_orders"] = [1]
                rows.append(unit(text, rendered, entries=entries))
            input_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_auto_acceptance_file(
                input_jsonl=input_path,
                output_jsonl=output_path,
                summary_json=summary_path,
                auto_accept_profile=AUTO_ACCEPT_PROFILE_STABLE_TWO_KANJI,
                decoder_model_dir=model_dir,
            )

            results = [json.loads(line) for line in output_path.read_text().splitlines()]
            self.assertTrue(results[0]["analysis"]["mechanical"]["yomi"]["auto_accept"]["value"])
            self.assertFalse(results[1]["analysis"]["mechanical"]["yomi"]["auto_accept"]["value"])
            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.rejected, 1)
            self.assertEqual(
                summary.stable_surface_lexicon_artifact,
                str(model_dir / "stable_surface_readings.tsv"),
            )


def make_stable_checker(raw_csv: str) -> StableTwoKanjiChecker:
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name)
    (path / "core_lex.csv").write_text(raw_csv, encoding="utf-8")
    checker = StableTwoKanjiChecker(
        rows=[],
        decoder_lexicon_path=Path("missing.jsonl"),
        raw_sudachi_dict_dir=path,
    )
    checker._tmpdir = tmp  # type: ignore[attr-defined]
    return checker


if __name__ == "__main__":
    unittest.main()
