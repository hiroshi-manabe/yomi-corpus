from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.final_review import (
    apply_final_review_file,
    build_strong_repair_queue_file,
    build_yomi_final_review_pack_file,
    finalize_reviewed_yomi_file,
    replay_review_submissions,
    store_review_submission,
)


class YomiFinalReviewTests(unittest.TestCase):
    def test_build_pack_groups_units_and_exposes_tappable_ruby_candidates(self) -> None:
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
                    ("none", None),
                ],
            )
            self.assertEqual(
                pack["items"][0]["ruby_segments"],
                [
                    {
                        "type": "ruby",
                        "text": "近々",
                        "target_item_id": "u1:r0001c01",
                        "reading": "きんきん",
                        "is_safe": False,
                        "highlight_level": "target",
                    },
                    {"type": "text", "text": "です。"},
                ],
            )
            self.assertFalse(pack["items"][0]["all_targets_safe"])
            self.assertTrue(pack["items"][1]["all_targets_safe"])

            summary_path.write_text(json.dumps(summary.__dict__), encoding="utf-8")

    def test_replay_yomi_review_submissions_applies_later_overlap(self) -> None:
        pack = {
            "pack_id": "pack_1",
            "items": [
                {"item_id": "u1", "seq": 1, "skip_default": False},
                {"item_id": "u2", "seq": 2, "skip_default": False},
            ],
        }
        first = {
            "submission_id": "s1",
            "generated_at_epoch": 1,
            "reviewed_ranges": [{"from_seq": 1, "to_seq": 2}],
            "overrides": [{"item_id": "u2", "skip": True}],
        }
        second = {
            "submission_id": "s2",
            "generated_at_epoch": 2,
            "reviewed_ranges": [{"from_seq": 2, "to_seq": 2}],
            "overrides": [],
        }

        effective = replay_review_submissions(pack, [first, second])

        self.assertFalse(effective["u1"]["skip"])
        self.assertFalse(effective["u2"]["skip"])
        self.assertEqual(effective["u2"]["submission_id"], "s2")

    def test_apply_final_review_updates_exact_rendered_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            payload = unit("doc1", "u1", "近々です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "近々/キンキン です/デス 。/。"
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "llm",
                                    "selected_reading": "ちかぢか",
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertTrue(summary["stage_complete"])
            self.assertEqual(summary["exact_rendered_updates"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/チカヂカ です/デス 。/。",
            )
            review = row["analysis"]["human_review"]["yomi_final"]
            self.assertTrue(review["reviewed"])
            self.assertEqual(review["target_overrides"][0]["selected_reading"], "ちかぢか")

    def test_sentence_escalation_preserves_target_reading_override_as_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "近々です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "近々/キンキン です/デス 。/。"
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "escalate_sentence": True,
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "llm",
                                    "selected_reading": "ちかぢか",
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 1)
            reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
            self.assertEqual(
                reviewed["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/チカヂカ です/デス 。/。",
            )
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "sentence")
            self.assertEqual(queued["repair_order"], 2)
            self.assertEqual(queued["reasons"], ["sentence_escalation"])
            self.assertEqual(queued["target_escalations"], [])
            self.assertEqual(queued["target_overrides"], [])
            self.assertEqual(
                queued["target_constraints"],
                [
                    {
                        "item_id": "u1:r0001c01",
                        "choice_source": "llm",
                        "selected_reading": "ちかぢか",
                        "surface": "近々",
                        "token_surface": "近々",
                        "token_index": 0,
                        "chunk_index": 0,
                        "current_reading_hiragana": "きんきん",
                    }
                ],
            )

    def test_skipped_item_records_target_override_without_applying_or_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            payload = unit("doc1", "u1", "近々です。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = "近々/キンキン です/デス 。/。"
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": True,
                            "escalate_sentence": True,
                            "targets": [
                                {
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "llm",
                                    "selected_reading": "ちかぢか",
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_summary = apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )
            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=reviewed_path,
                strong_queue_summary_json=queue_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertEqual(apply_summary["exact_rendered_updates"], 0)
            reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
            self.assertEqual(
                reviewed["analysis"]["mechanical"]["yomi"]["rendered"],
                "近々/キンキン です/デス 。/。",
            )
            review = reviewed["analysis"]["human_review"]["yomi_final"]
            self.assertTrue(review["skip"])
            self.assertTrue(review["escalate_sentence"])
            self.assertEqual(review["target_overrides"][0]["selected_reading"], "ちかぢか")
            self.assertEqual(queue_summary["queued_items"], 0)
            self.assertEqual(queue_path.read_text(encoding="utf-8"), "")
            self.assertTrue(final_summary["stage_complete"])
            self.assertEqual(final_summary["written_units"], 0)
            self.assertEqual(final_summary["skipped_units"], 1)

    def test_target_no_ruby_queue_uses_current_batch_case_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "真光元被害者の会が発足しました。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "真光/シンコウ 元/モト 被害者/ヒガイシャ の/ノ 会/カイ が/ガ 発足/ホッソク し/シ まし/マシ た/タ 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0002c01",
                    "token_index": 1,
                    "surface": "元",
                    "token_surface": "元",
                    "current_reading": "モト",
                    "current_reading_hiragana": "もと",
                    "target_start": 2,
                    "target_end": 3,
                }
            )
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0002",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": False,
                            "targets": [
                                {
                                    "item_id": "u1:r0002c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 1)
            self.assertEqual(queue_summary["target_escalations"], 1)
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "target")
            self.assertEqual(queued["repair_order"], 1)
            self.assertEqual(queued["reasons"], ["target_no_ruby"])
            self.assertEqual(queued["target_escalations"][0]["surface"], "元")
            self.assertEqual(queued["target_escalations"][0]["choice_source"], "none")
            self.assertIn("真光元被害者", queued["text"])

    def test_target_no_ruby_is_queued_before_sentence_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "真光元被害者の会が発足しました。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "真光/シンコウ 元/モト 被害者/ヒガイシャ の/ノ 会/カイ が/ガ 発足/ホッソク し/シ まし/マシ た/タ 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0002c01",
                    "token_index": 1,
                    "surface": "元",
                    "token_surface": "元",
                    "current_reading": "モト",
                    "current_reading_hiragana": "もと",
                    "target_start": 2,
                    "target_end": 3,
                }
            )
            units_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=pack_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0002",
                created_at_epoch=123,
            )
            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [
                        {
                            "item_id": "u1",
                            "skip": False,
                            "escalate_sentence": True,
                            "targets": [
                                {
                                    "item_id": "u1:r0002c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                }
                            ],
                        }
                    ],
                },
                submission_store_dir=store_dir,
            )

            apply_final_review_file(
                units_jsonl=units_path,
                pack_json=pack_path,
                submission_store_dir=store_dir,
                output_jsonl=reviewed_path,
                summary_json=review_summary_path,
            )
            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 2)
            rows = [
                json.loads(line)
                for line in queue_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["repair_scope"] for row in rows], ["target", "sentence"])
            self.assertEqual([row["repair_order"] for row in rows], [1, 2])
            self.assertEqual(rows[1]["target_constraints"][0]["surface"], "元")

    def test_strong_queue_blocks_finalize_when_no_ruby_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewed_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            reviewed_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "近々です。",
                        "analysis": {
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": False,
                                    "escalate_sentence": False,
                                    "target_overrides": [
                                        {
                                            "item_id": "u1:r0001c01",
                                            "choice_source": "none",
                                        }
                                    ],
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            queue_summary = build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )
            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=reviewed_path,
                strong_queue_summary_json=queue_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertEqual(queue_summary["queued_items"], 1)
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "target")
            self.assertEqual(queued["repair_order"], 1)
            self.assertFalse(final_summary["stage_complete"])
            self.assertIn("not implemented", final_summary["blocking_reason"])


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
