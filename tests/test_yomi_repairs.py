from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.repairs import apply_post_hybrid_repairs


class YomiRepairTests(unittest.TestCase):
    def test_applies_active_regex_rules_and_records_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / "rules.tsv"
            rules.write_text(
                "\n".join(
                    [
                        "rule_id\tpattern\treplacement\tstatus\tsource\tnote",
                        "r1\t(?<!\\S)若しくは/モシクワ(?!\\S)\t若しくは/モシクハ\tactive\tmanual_seed\tfinal は",
                        "r2\t(?<!\\S)身近/ミジカ(?!\\S)\t身近/ミヂカ\tactive\tmanual_seed\tprefer ヂ",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = apply_post_hybrid_repairs(
                "若しくは/モシクワ 身近/ミジカ 近身近/ミジカ",
                rules_path=rules,
            )

            self.assertEqual(result.rendered, "若しくは/モシクハ 身近/ミヂカ 近身近/ミジカ")
            self.assertEqual(result.metadata["applied_rule_ids"], ["r1", "r2"])
            self.assertEqual(result.metadata["applications"][0]["match"], "若しくは/モシクワ")
            self.assertEqual(result.metadata["applications"][0]["count"], 1)

    def test_ignores_inactive_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / "rules.tsv"
            rules.write_text(
                "\n".join(
                    [
                        "rule_id\tpattern\treplacement\tstatus\tsource\tnote",
                        "r1\t(?<!\\S)若しくは/モシクワ(?!\\S)\t若しくは/モシクハ\tdisabled\tmanual_seed\tfinal は",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = apply_post_hybrid_repairs("若しくは/モシクワ", rules_path=rules)

            self.assertEqual(result.rendered, "若しくは/モシクワ")
            self.assertEqual(result.metadata, {})

    def test_default_config_resolves_repair_rules(self) -> None:
        config = load_yomi_generation_config("config/yomi/default.toml")

        self.assertIsNotNone(config.post_hybrid_repair_rules)
        assert config.post_hybrid_repair_rules is not None
        self.assertTrue(config.post_hybrid_repair_rules.endswith("config/yomi/post_hybrid_repairs.tsv"))
        self.assertEqual(config.corpus_frequency_min_count, 5)
        self.assertEqual(config.corpus_frequency_min_share, 0.95)

    def test_default_rules_prefer_watashi_without_rewriting_other_readings(self) -> None:
        config = load_yomi_generation_config("config/yomi/default.toml")

        result = apply_post_hybrid_repairs(
            "私/ワタクシ は/ハ 私/ワタシ 私/アタシ",
            rules_path=config.post_hybrid_repair_rules,
        )

        self.assertEqual(result.rendered, "私/ワタシ は/ハ 私/ワタシ 私/アタシ")
        self.assertIn("yomi_repair_0003", result.metadata["applied_rule_ids"])


if __name__ == "__main__":
    unittest.main()
