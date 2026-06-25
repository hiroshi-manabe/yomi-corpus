from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.final_review import build_yomi_final_review_pack_file


class YomiFinalReviewTests(unittest.TestCase):
    def test_build_pack_groups_units_and_exposes_dropdown_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            summary_path = root / "summary.json"
            units_path.write_text(
                "\n".join(
                    [
                        json.dumps(unit("doc1", "u1", "近々です。"), ensure_ascii=False),
                        json.dumps(unit("doc2", "u2", "学校です。", safe=True), ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="yomi_final_dev_batch_0001_v1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary.item_count, 2)
            self.assertEqual(summary.unresolved_item_count, 1)
            self.assertEqual(summary.unresolved_target_count, 1)
            self.assertEqual(pack["review_stage"], "yomi_final_review")
            self.assertEqual(pack["items"][0]["doc_seq"], 1)
            self.assertEqual(pack["items"][1]["doc_seq"], 2)
            target = pack["items"][0]["targets"][0]
            self.assertFalse(target["is_safe"])
            self.assertEqual(
                [(candidate["source"], candidate["reading"]) for candidate in target["candidates"]],
                [
                    ("current", "きんきん"),
                    ("llm", "ちかぢか"),
                    ("other", None),
                ],
            )
            self.assertFalse(pack["items"][0]["all_targets_safe"])
            self.assertTrue(pack["items"][1]["all_targets_safe"])

            summary_path.write_text(json.dumps(summary.__dict__), encoding="utf-8")


def unit(doc_id: str, unit_id: str, text: str, *, safe: bool = False) -> dict:
    signals = [
        {
            "name": "safe_by_llm_match",
            "accepted": safe,
            "status": "matched" if safe else "mismatched",
            "llm_reading": "きんきん" if safe else "ちかぢか",
            "current_reading_hiragana": "きんきん",
        }
    ]
    return {
        "doc_id": doc_id,
        "unit_id": unit_id,
        "unit_seq": 1,
        "text": text,
        "source_file": "source.jsonl.gz",
        "source_line_no": 1,
        "analysis": {
            "mechanical": {
                "yomi": {
                    "rendered": f"{text}/キンキン",
                }
            },
            "llm": {
                "scope_triage": {
                    "status": "Keep",
                    "source": "llm",
                }
            },
            "safety": {
                "yomi": {
                    "targets": [
                        {
                            "item_id": f"{unit_id}:r0001c01",
                            "unit_id": unit_id,
                            "token_index": 0,
                            "chunk_index": 0,
                            "surface": "近々",
                            "token_surface": "近々",
                            "current_reading": "キンキン",
                            "current_reading_hiragana": "きんきん",
                            "target_start": 0,
                            "target_end": 2,
                            "is_safe": safe,
                            "review_status": "safe" if safe else "unresolved",
                            "highlight_level": "none" if safe else "target",
                            "accepted_signal_names": ["safe_by_llm_match"] if safe else [],
                            "signals": signals,
                            "status_reason": "accepted_llm_match"
                            if safe
                            else "llm_reading_mismatched",
                        }
                    ]
                }
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
