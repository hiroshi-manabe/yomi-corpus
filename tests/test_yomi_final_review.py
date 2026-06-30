from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.yomi.final_review import (
    apply_final_review_file,
    apply_strong_repair_review_file,
    apply_yomi_strong_repair_results_file,
    build_strong_repair_queue_file,
    build_yomi_strong_repair_review_pack_file,
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
            self.assertEqual(target["default_choice_source"], "llm")
            self.assertEqual(target["default_reading"], "ちかぢか")
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
                        "reading": "ちかぢか",
                        "is_safe": False,
                        "highlight_level": "target",
                    },
                    {"type": "text", "text": "です。"},
                ],
            )
            self.assertFalse(pack["items"][0]["all_targets_safe"])
            self.assertTrue(pack["items"][1]["all_targets_safe"])

            summary_path.write_text(json.dumps(summary.__dict__), encoding="utf-8")

    def test_pack_drops_non_kana_reading_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            output_path = root / "pack.json"
            payload = {
                "doc_id": "doc1",
                "unit_id": "u1",
                "unit_seq": 1,
                "text": "Diploma Mill",
                "source_file": "source.jsonl.gz",
                "source_line_no": 1,
                "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "Diploma/ディプロマ Mill/mill",
                        }
                    },
                    "llm": {"scope_triage": {"status": "Keep", "source": "llm"}},
                    "safety": {
                        "yomi": {
                            "targets": [
                                {
                                    "item_id": "u1:r0002c01",
                                    "unit_id": "u1",
                                    "token_index": 1,
                                    "chunk_index": 0,
                                    "surface": "Mill",
                                    "token_surface": "Mill",
                                    "current_reading": "mill",
                                    "current_reading_hiragana": "mill",
                                    "target_start": 8,
                                    "target_end": 12,
                                    "is_safe": False,
                                    "review_status": "unresolved",
                                    "highlight_level": "target",
                                    "accepted_signal_names": [],
                                    "signals": [
                                        {
                                            "name": "safe_by_llm_match",
                                            "accepted": False,
                                            "status": "mismatched",
                                            "llm_reading": "みる",
                                            "current_reading_hiragana": "mill",
                                        }
                                    ],
                                    "status_reason": "llm_reading_mismatched",
                                }
                            ]
                        }
                    },
                },
            }
            units_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            build_yomi_final_review_pack_file(
                units_jsonl=units_path,
                output_json=output_path,
                pack_id="pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )

            pack = json.loads(output_path.read_text(encoding="utf-8"))
            target = pack["items"][0]["targets"][0]
            self.assertEqual(target["default_choice_source"], "llm")
            self.assertEqual(target["default_reading"], "みる")
            self.assertEqual(
                [(candidate["source"], candidate["reading"]) for candidate in target["candidates"]],
                [("llm", "みる"), ("none", None)],
            )
            self.assertEqual(pack["items"][0]["ruby_segments"][1]["reading"], "みる")

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

    def test_apply_final_review_applies_llm_default_for_reviewed_range(self) -> None:
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
                    "overrides": [],
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
            self.assertEqual(review["target_overrides"][0]["choice_source"], "llm")
            self.assertEqual(review["target_overrides"][0]["selected_reading"], "ちかぢか")

    def test_apply_final_review_applies_span_segmentation_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            output_path = root / "reviewed.jsonl"
            summary_path = root / "summary.json"
            payload = unit("doc1", "u1", "それを、旧池尻中学校を改装した。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。"
            )
            base_target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target0 = {**base_target}
            target0.update(
                {
                    "item_id": "u1:r0005c01",
                    "token_index": 4,
                    "surface": "池尻中",
                    "token_surface": "池尻中",
                    "current_reading": "イケジリナカ",
                    "current_reading_hiragana": "いけじりなか",
                    "target_start": 5,
                    "target_end": 8,
                    "is_safe": False,
                    "review_status": "unresolved",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            target1 = {**base_target}
            target1.update(
                {
                    "item_id": "u1:r0006c01",
                    "token_index": 5,
                    "surface": "学校",
                    "token_surface": "学校",
                    "current_reading": "ガッコウ",
                    "current_reading_hiragana": "がっこう",
                    "target_start": 8,
                    "target_end": 10,
                    "is_safe": True,
                    "review_status": "safe",
                    "accepted_signal_names": [],
                    "signals": [],
                }
            )
            payload["analysis"]["safety"]["yomi"]["targets"] = [target0, target1]
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
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["items"][0]["reading_hints"].get("中学校"), "ちゅうがっこう")
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
                            "span_overrides": [
                                {
                                    "id": "u1:r0005c01|u1:r0006c01",
                                    "decision": "segmentation",
                                    "target_item_ids": ["u1:r0005c01", "u1:r0006c01"],
                                    "original_surface": "池尻中学校",
                                    "segments": [
                                        {"surface": "池尻", "reading": "いけじり"},
                                        {"surface": "中学校", "reading": "ちゅうがっこう"},
                                    ],
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
            self.assertEqual(summary["span_override_count"], 1)
            self.assertEqual(summary["exact_rendered_span_updates"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                row["analysis"]["mechanical"]["yomi"]["rendered"],
                "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻/イケジリ 中学校/チュウガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。",
            )
            review = row["analysis"]["human_review"]["yomi_final"]
            self.assertEqual(review["span_overrides"][0]["decision"], "segmentation")
            self.assertEqual(review["exact_rendered_span_updates"], 1)

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
            self.assertEqual(queued["repair_scope"], "target_group")
            self.assertEqual(queued["repair_order"], 1)
            self.assertEqual(queued["reasons"], ["target_no_ruby"])
            self.assertEqual(queued["target_escalations"][0]["surface"], "元")
            self.assertEqual(queued["target_escalations"][0]["choice_source"], "none")
            self.assertEqual(
                queued["target_escalations"][0]["rejected_readings"],
                [{"surface": "元", "reading": "もと", "source": "human_no_ruby"}],
            )
            self.assertIn("真光元被害者", queued["text"])

    def test_target_no_ruby_queue_can_carry_rejected_publisher_name_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "units.jsonl"
            pack_path = root / "pack.json"
            store_dir = root / "submissions"
            reviewed_path = root / "reviewed.jsonl"
            review_summary_path = root / "review_summary.json"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            payload = unit("doc1", "u1", "この本は「史輝出版」から刊行されました。")
            payload["analysis"]["mechanical"]["yomi"]["rendered"] = (
                "この/コノ 本/ホン は/ハ 「/「 史輝/フミテル 出版/シュッパン 」/」 から/カラ 刊行/カンコウ さ/サ れ/レ まし/マシ た/タ 。/。"
            )
            target = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target.update(
                {
                    "item_id": "u1:r0005c01",
                    "token_index": 4,
                    "surface": "史輝",
                    "token_surface": "史輝",
                    "current_reading": "フミテル",
                    "current_reading_hiragana": "ふみてる",
                    "target_start": 5,
                    "target_end": 7,
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
                                    "item_id": "u1:r0005c01",
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
            build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )

            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(queued["repair_scope"], "target_group")
            self.assertEqual(queued["target_escalations"][0]["surface"], "史輝")
            self.assertEqual(
                queued["target_escalations"][0]["rejected_readings"],
                [{"surface": "史輝", "reading": "ふみてる", "source": "human_no_ruby"}],
            )

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
            target0 = payload["analysis"]["safety"]["yomi"]["targets"][0]
            target0.update(
                {
                    "item_id": "u1:r0001c01",
                    "token_index": 0,
                    "surface": "真光",
                    "token_surface": "真光",
                    "current_reading": "シンコウ",
                    "current_reading_hiragana": "しんこう",
                    "target_start": 0,
                    "target_end": 2,
                }
            )
            target1 = dict(target0)
            target1.update(
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
            payload["analysis"]["safety"]["yomi"]["targets"].append(target1)
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
                                    "item_id": "u1:r0001c01",
                                    "choice_source": "none",
                                    "selected_reading": None,
                                },
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
            self.assertEqual(queue_summary["target_escalations"], 2)
            rows = [
                json.loads(line)
                for line in queue_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["repair_scope"] for row in rows], ["target_group", "sentence"])
            self.assertEqual([row["repair_order"] for row in rows], [1, 2])
            self.assertEqual(
                [target["surface"] for target in rows[0]["target_escalations"]],
                ["真光", "元"],
            )
            self.assertEqual(
                [target["surface"] for target in rows[1]["target_constraints"]],
                ["真光", "元"],
            )

    def test_strong_queue_blocks_finalize_before_repair_is_applied(self) -> None:
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
            self.assertEqual(queued["repair_scope"], "target_group")
            self.assertEqual(queued["repair_order"], 1)
            self.assertFalse(final_summary["stage_complete"])
            self.assertIn("has not been applied", final_summary["blocking_reason"])

    def test_applies_target_group_strong_repair_and_blocks_finalize_until_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewed_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            queue_summary_path = root / "queue_summary.json"
            results_path = root / "results.jsonl"
            strong_path = root / "strong.jsonl"
            strong_summary_path = root / "strong_summary.json"
            pack_path = root / "strong_pack.json"
            submission_store = root / "strong_submissions"
            confirmation_summary_path = root / "strong_confirmation_summary.json"
            final_path = root / "final.jsonl"
            final_summary_path = root / "final_summary.json"
            reviewed_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "それを、旧池尻中学校を改装した。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ を/ヲ 改装/カイソウ し/シ た/タ 。/。"
                                }
                            },
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": False,
                                    "escalate_sentence": False,
                                    "target_overrides": [
                                        {
                                            "item_id": "u1:r0005c01",
                                            "choice_source": "none",
                                            "surface": "池尻中",
                                            "token_index": 4,
                                            "chunk_index": 0,
                                            "current_reading_hiragana": "いけじりなか",
                                        },
                                        {
                                            "item_id": "u1:r0006c01",
                                            "choice_source": "none",
                                            "surface": "学校",
                                            "token_index": 5,
                                            "chunk_index": 0,
                                            "current_reading_hiragana": "がっこう",
                                        },
                                    ],
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            build_strong_repair_queue_file(
                units_jsonl=reviewed_path,
                output_jsonl=queue_path,
                summary_json=queue_summary_path,
            )
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": queued["item_id"],
                        "parsed": [
                            {"surface": "池尻", "reading": "いけじり", "used_web_search": False},
                            {"surface": "中学校", "reading": "ちゅうがっこう", "used_web_search": False},
                        ],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            strong_summary = apply_yomi_strong_repair_results_file(
                units_jsonl=reviewed_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=strong_path,
                summary_json=strong_summary_path,
            )
            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=strong_path,
                strong_queue_summary_json=queue_summary_path,
                strong_apply_summary_json=strong_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )

            self.assertTrue(strong_summary["stage_complete"])
            self.assertEqual(strong_summary["applied_items"], 1)
            repaired = json.loads(strong_path.read_text(encoding="utf-8"))
            self.assertIn("池尻/イケジリ 中学校/チュウガッコウ", repaired["analysis"]["mechanical"]["yomi"]["rendered"])
            self.assertFalse(final_summary["stage_complete"])
            self.assertIn("require human confirmation", final_summary["blocking_reason"])

            pack_summary = build_yomi_strong_repair_review_pack_file(
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                units_jsonl=strong_path,
                output_json=pack_path,
                pack_id="strong_pack_1",
                track_name="dev",
                batch_name="dev_batch_0001",
                created_at_epoch=123,
            )
            self.assertEqual(pack_summary.item_count, 1)
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["review_stage"], "yomi_strong_repair_review")
            self.assertEqual(pack["items"][0]["rejected_span"], "池尻中学校")
            self.assertEqual(pack["items"][0]["repair_status"], "applied")

            store_review_submission(
                {
                    "submission_type": "review_patch",
                    "review_stage": "yomi_strong_repair_review",
                    "pack_id": "strong_pack_1",
                    "submission_id": "s1",
                    "generated_at_epoch": 10,
                    "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                    "overrides": [],
                },
                submission_store_dir=submission_store,
            )
            confirmation_summary = apply_strong_repair_review_file(
                pack_json=pack_path,
                submission_store_dir=submission_store,
                strong_apply_summary_json=strong_summary_path,
                output_summary_json=confirmation_summary_path,
            )
            self.assertTrue(confirmation_summary["stage_complete"])
            confirmed_repair_summary = json.loads(strong_summary_path.read_text(encoding="utf-8"))
            self.assertTrue(confirmed_repair_summary["confirmed"])

            final_summary = finalize_reviewed_yomi_file(
                units_jsonl=strong_path,
                strong_queue_summary_json=queue_summary_path,
                strong_apply_summary_json=strong_summary_path,
                output_jsonl=final_path,
                summary_json=final_summary_path,
            )
            self.assertTrue(final_summary["stage_complete"])
            self.assertEqual(final_summary["written_units"], 1)

    def test_strong_repair_falls_back_to_unique_surface_span_when_token_index_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "strong.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "山根視来選手です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "山根/ヤマネ 視/シ 来/ライ 選手/センシュ です/デス 。/。"
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {"surface": "視", "token_index": 2, "chunk_index": 0},
                            {"surface": "来", "token_index": 3, "chunk_index": 0},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [{"surface": "視来", "reading": "ミキ"}],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertTrue(summary["stage_complete"])
            repaired = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("山根/ヤマネ 視来/ミキ 選手/センシュ", repaired["analysis"]["mechanical"]["yomi"]["rendered"])

    def test_strong_repair_rejects_reused_rejected_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units_path = root / "reviewed.jsonl"
            queue_path = root / "queue.jsonl"
            results_path = root / "results.jsonl"
            output_path = root / "strong.jsonl"
            summary_path = root / "summary.json"
            units_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "真光元被害者の会です。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "真光/シンコウ 元/モト 被害者/ヒガイシャ の/ノ 会/カイ です/デス 。/。"
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "unit_id": "u1",
                        "repair_scope": "target_group",
                        "target_escalations": [
                            {
                                "surface": "真光",
                                "token_index": 0,
                                "chunk_index": 0,
                                "rejected_readings": [{"surface": "真光", "reading": "しんこう"}],
                            },
                            {
                                "surface": "元",
                                "token_index": 1,
                                "chunk_index": 0,
                                "rejected_readings": [{"surface": "元", "reading": "もと"}],
                            },
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            results_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1::target_group:1",
                        "parsed": [
                            {"surface": "真光", "reading": "まひかり"},
                            {"surface": "元", "reading": "もと"},
                        ],
                        "parse_error": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_yomi_strong_repair_results_file(
                units_jsonl=units_path,
                queue_jsonl=queue_path,
                results_jsonl=results_path,
                output_jsonl=output_path,
                summary_json=summary_path,
            )

            self.assertFalse(summary["stage_complete"])
            self.assertEqual(summary["invalid_items"], 1)


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
