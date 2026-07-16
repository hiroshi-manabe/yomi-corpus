from __future__ import annotations

import json
import unittest

from pathlib import Path

from yomi_corpus.yomi.final_review_issue_import import (
    extract_attachment_records,
    extract_attachment_urls,
    extract_inline_submission_records,
    fetch_issue_comments,
    import_issue_payloads,
    parse_submissions_from_text,
)
from yomi_corpus.yomi.final_review import apply_final_review_file


class YomiFinalReviewIssueImportTests(unittest.TestCase):
    def test_extract_attachment_urls_from_issue_and_comments(self) -> None:
        payloads = [
            {
                "number": 7,
                "body": "[submission](https://github.com/user-attachments/files/12345/a.json)",
            },
            {
                "id": 101,
                "body": "duplicate https://github.com/user-attachments/files/12345/a.json and new https://github.com/user-attachments/files/99999/b.json",
            },
        ]
        self.assertEqual(
            extract_attachment_urls(payloads),
            [
                "https://github.com/user-attachments/files/12345/a.json",
                "https://github.com/user-attachments/files/99999/b.json",
            ],
        )

    def test_extract_attachment_records_keeps_issue_metadata(self) -> None:
        records = extract_attachment_records(
            {
                "number": 7,
                "body": "https://github.com/user-attachments/files/12345/a.json",
            },
            [
                {
                    "id": 101,
                    "body": "https://github.com/user-attachments/files/99999/b.json",
                }
            ],
        )
        self.assertEqual(
            records,
            [
                {
                    "url": "https://github.com/user-attachments/files/12345/a.json",
                    "source_kind": "issue",
                    "issue_number": 7,
                    "comment_id": None,
                },
                {
                    "url": "https://github.com/user-attachments/files/99999/b.json",
                    "source_kind": "comment",
                    "issue_number": 7,
                    "comment_id": 101,
                },
            ],
        )

    def test_fetch_issue_comments_is_defined(self) -> None:
        self.assertTrue(callable(fetch_issue_comments))

    def test_parse_submissions_from_text_accepts_raw_json_with_surrounding_text(self) -> None:
        submissions = parse_submissions_from_text(
            """
            Here is the result:
            {
              "submission_type": "review_patch",
              "review_stage": "yomi_final_review",
              "pack_id": "pack_1",
              "submission_id": "sub_1"
            }
            Thanks.
            """
        )
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0]["submission_id"], "sub_1")

    def test_parse_submissions_from_text_accepts_finalized_correction_without_pack_id(self) -> None:
        submissions = parse_submissions_from_text(
            """
            {
              "submission_type": "finalized_correction_patch",
              "review_stage": "finalized_correction",
              "track_name": "dev",
              "batch_name": "dev_batch_0001",
              "units": [
                {
                  "unit_id": "u1",
                  "original_rendered_yomi": "今日/キョウ",
                  "proposed_rendered_yomi": "今日/コンニチ"
                }
              ]
            }
            """
        )
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0]["submission_type"], "finalized_correction_patch")

    def test_parse_submissions_from_text_accepts_fenced_json(self) -> None:
        submissions = parse_submissions_from_text(
            """
            Text above.

            ```json
            {
              "submission_type": "review_patch",
              "review_stage": "yomi_final_review",
              "pack_id": "pack_2",
              "submission_id": "sub_2"
            }
            ```
            """
        )
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0]["submission_id"], "sub_2")

    def test_extract_inline_submission_records_keeps_issue_metadata(self) -> None:
        records = extract_inline_submission_records(
            {
                "number": 7,
                "body": """
                {
                  "submission_type": "review_patch",
                  "review_stage": "yomi_final_review",
                  "pack_id": "pack_1",
                  "submission_id": "sub_1"
                }
                """,
            },
            [
                {
                    "id": 101,
                    "body": """
                    ```json
                    {
                      "submission_type": "review_patch",
                      "review_stage": "yomi_final_review",
                      "pack_id": "pack_2",
                      "submission_id": "sub_2"
                    }
                    ```
                    """,
                }
            ],
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["issue_number"], 7)
        self.assertIsNone(records[0]["comment_id"])
        self.assertEqual(records[1]["comment_id"], 101)
        self.assertEqual(records[0]["submission"]["submission_id"], "sub_1")
        self.assertEqual(records[1]["submission"]["submission_id"], "sub_2")

    def test_import_issue_payloads_stores_matching_yomi_submission(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_pack_root = root / "review_packs"
            submission_store_dir = root / "submissions"
            (review_pack_root / "yomi_final").mkdir(parents=True)
            (review_pack_root / "yomi_final" / "pack_1.json").write_text(
                json.dumps({"pack_id": "pack_1"}),
                encoding="utf-8",
            )
            issue = {
                "number": 7,
                "body": json.dumps(
                    {
                        "submission_type": "review_patch",
                        "review_stage": "yomi_final_review",
                        "pack_id": "pack_1",
                        "submission_id": "sub_1",
                        "generated_at_epoch": 10,
                        "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                        "overrides": [],
                    },
                    ensure_ascii=False,
                ),
            }
            comments = [
                {
                    "id": 101,
                    "body": json.dumps(
                        {
                            "submission_type": "review_patch",
                            "review_stage": "alphabetic_candidate_review",
                            "pack_id": "pack_1",
                            "submission_id": "sub_wrong",
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "id": 102,
                    "body": json.dumps(
                        {
                            "submission_type": "review_patch",
                            "review_stage": "yomi_final_review",
                            "pack_id": "unknown",
                            "submission_id": "sub_unknown",
                        },
                        ensure_ascii=False,
                    ),
                },
            ]

            summary = import_issue_payloads(
                issue_payload=issue,
                comment_payloads=comments,
                repo="owner/repo",
                issue_number=7,
                review_pack_root=review_pack_root,
                submission_store_dir=submission_store_dir,
            )

            self.assertEqual(summary["imported_submission_count"], 1)
            self.assertEqual(summary["inline_submission_count"], 3)
            self.assertEqual(
                [row["reason"] for row in summary["skipped"]],
                ["wrong_review_stage", "unknown_pack_id"],
            )
            stored = submission_store_dir / "sub_1.json"
            self.assertTrue(stored.exists())
            payload = json.loads(stored.read_text(encoding="utf-8"))
            self.assertEqual(payload["_source_issue"]["issue_number"], 7)

    def test_import_issue_payloads_can_target_strong_repair_review(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_pack_root = root / "review_packs"
            submission_store_dir = root / "strong_submissions"
            (review_pack_root / "yomi_strong_repair").mkdir(parents=True)
            (review_pack_root / "yomi_strong_repair" / "strong_pack_1.json").write_text(
                json.dumps({"pack_id": "strong_pack_1"}),
                encoding="utf-8",
            )
            issue = {
                "number": 8,
                "body": json.dumps(
                    {
                        "submission_type": "review_patch",
                        "review_stage": "yomi_strong_repair_review",
                        "pack_id": "strong_pack_1",
                        "submission_id": "strong_sub_1",
                        "generated_at_epoch": 10,
                        "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                        "overrides": [],
                    },
                    ensure_ascii=False,
                ),
            }
            comments = [
                {
                    "id": 101,
                    "body": json.dumps(
                        {
                            "submission_type": "review_patch",
                            "review_stage": "yomi_final_review",
                            "pack_id": "strong_pack_1",
                            "submission_id": "wrong_stage",
                        },
                        ensure_ascii=False,
                    ),
                }
            ]

            summary = import_issue_payloads(
                issue_payload=issue,
                comment_payloads=comments,
                repo="owner/repo",
                issue_number=8,
                review_pack_root=review_pack_root,
                submission_store_dir=submission_store_dir,
                review_stage="yomi_strong_repair_review",
            )

            self.assertEqual(summary["review_stage"], "yomi_strong_repair_review")
            self.assertEqual(summary["imported_submission_count"], 1)
            self.assertEqual([row["reason"] for row in summary["skipped"]], ["wrong_review_stage"])
            stored = submission_store_dir / "strong_sub_1.json"
            self.assertTrue(stored.exists())
            payload = json.loads(stored.read_text(encoding="utf-8"))
            self.assertEqual(payload["review_stage"], "yomi_strong_repair_review")

    def test_import_issue_payloads_unpacks_review_bundle_by_stage(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_pack_root = root / "review_packs"
            final_store = root / "final_submissions"
            strong_store = root / "strong_submissions"
            (review_pack_root / "yomi_final").mkdir(parents=True)
            (review_pack_root / "yomi_strong_repair").mkdir(parents=True)
            (review_pack_root / "yomi_final" / "final_pack_1.json").write_text(
                json.dumps({"pack_id": "final_pack_1"}),
                encoding="utf-8",
            )
            (review_pack_root / "yomi_strong_repair" / "strong_pack_1.json").write_text(
                json.dumps({"pack_id": "strong_pack_1"}),
                encoding="utf-8",
            )
            issue = {
                "number": 9,
                "body": json.dumps(
                    {
                        "submission_type": "review_bundle",
                        "review_stage": "unified_yomi_review",
                        "pack_id": "unified_pack",
                        "submission_id": "bundle_1",
                        "submissions": [
                            {
                                "submission_type": "review_patch",
                                "review_stage": "yomi_final_review",
                                "pack_id": "final_pack_1",
                                "submission_id": "final_sub_1",
                                "generated_at_epoch": 10,
                                "reviewed_ranges": [{"from_seq": 1, "to_seq": 2}],
                                "overrides": [],
                            },
                            {
                                "submission_type": "review_patch",
                                "review_stage": "yomi_strong_repair_review",
                                "pack_id": "strong_pack_1",
                                "submission_id": "strong_sub_1",
                                "generated_at_epoch": 10,
                                "reviewed_ranges": [{"from_seq": 3, "to_seq": 3}],
                                "overrides": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            }

            final_summary = import_issue_payloads(
                issue_payload=issue,
                comment_payloads=[],
                repo="owner/repo",
                issue_number=9,
                review_pack_root=review_pack_root,
                submission_store_dir=final_store,
                review_stage="yomi_final_review",
            )
            strong_summary = import_issue_payloads(
                issue_payload=issue,
                comment_payloads=[],
                repo="owner/repo",
                issue_number=9,
                review_pack_root=review_pack_root,
                submission_store_dir=strong_store,
                review_stage="yomi_strong_repair_review",
            )

            self.assertEqual(final_summary["imported_submission_count"], 1)
            self.assertEqual(strong_summary["imported_submission_count"], 1)
            self.assertTrue((final_store / "final_sub_1.json").exists())
            self.assertTrue((strong_store / "strong_sub_1.json").exists())
            self.assertEqual(
                json.loads((final_store / "final_sub_1.json").read_text(encoding="utf-8"))[
                    "_source_issue"
                ]["issue_number"],
                9,
            )

    def test_one_review_bundle_applies_to_two_batches_independently(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_pack_root = root / "review_packs"
            submission_store = root / "submissions"
            pack_dir = review_pack_root / "yomi_final"
            pack_dir.mkdir(parents=True)

            batches = [
                ("dev_batch_0001", "final_pack_1", "batch1:u1", "学校です。", "学校/ガッコウ です/デス 。/。"),
                ("dev_batch_0002", "final_pack_2", "batch2:u1", "今日です。", "今日/キョウ です/デス 。/。"),
            ]
            for batch_name, pack_id, unit_id, text, rendered in batches:
                batch_dir = root / "data" / "units" / batch_name
                batch_dir.mkdir(parents=True)
                (batch_dir / "units.yomi.auto_accept.jsonl").write_text(
                    json.dumps(
                        {
                            "unit_id": unit_id,
                            "doc_id": unit_id.split(":")[0],
                            "text": text,
                            "analysis": {"mechanical": {"yomi": {"rendered": rendered}}},
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (pack_dir / f"{pack_id}.json").write_text(
                    json.dumps(
                        {
                            "pack_id": pack_id,
                            "review_stage": "yomi_final_review",
                            "batch_name": batch_name,
                            "items": [
                                {
                                    "item_id": unit_id,
                                    "seq": 1,
                                    "skip_default": False,
                                    "targets": [],
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            issue = {
                "number": 10,
                "body": json.dumps(
                    {
                        "submission_type": "review_bundle",
                        "review_stage": "unified_yomi_review",
                        "pack_id": "unified_two_batches",
                        "submission_id": "bundle_two_batches",
                        "submissions": [
                            {
                                "submission_type": "review_patch",
                                "review_stage": "yomi_final_review",
                                "pack_id": pack_id,
                                "submission_id": f"submission_{batch_name}",
                                "generated_at_epoch": index,
                                "reviewed_ranges": [{"from_seq": 1, "to_seq": 1}],
                                "overrides": [],
                            }
                            for index, (batch_name, pack_id, *_rest) in enumerate(batches, start=1)
                        ],
                    },
                    ensure_ascii=False,
                ),
            }

            import_summary = import_issue_payloads(
                issue_payload=issue,
                comment_payloads=[],
                repo="owner/repo",
                issue_number=10,
                review_pack_root=review_pack_root,
                submission_store_dir=submission_store,
                review_stage="yomi_final_review",
            )

            self.assertEqual(import_summary["imported_submission_count"], 2)
            applied_batches = []
            for batch_name, pack_id, unit_id, _text, _rendered in reversed(batches):
                batch_dir = root / "data" / "units" / batch_name
                output = batch_dir / "units.yomi.reviewed.jsonl"
                summary = apply_final_review_file(
                    units_jsonl=batch_dir / "units.yomi.auto_accept.jsonl",
                    pack_json=pack_dir / f"{pack_id}.json",
                    submission_store_dir=submission_store,
                    output_jsonl=output,
                    summary_json=batch_dir / "final_review_apply_summary.json",
                )
                self.assertTrue(summary["stage_complete"])
                reviewed = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(
                    reviewed["analysis"]["human_review"]["yomi_final"]["submission_id"],
                    f"submission_{batch_name}",
                )
                self.assertEqual(reviewed["unit_id"], unit_id)
                applied_batches.append(batch_name)

            self.assertEqual(applied_batches, ["dev_batch_0002", "dev_batch_0001"])

    def test_import_issue_payloads_stores_finalized_correction_without_pack(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "correction_submissions"
            issue = {
                "number": 12,
                "body": json.dumps(
                    {
                        "submission_type": "finalized_correction_patch",
                        "review_stage": "finalized_correction",
                        "track_name": "dev",
                        "batch_name": "dev_batch_0001",
                        "units": [
                            {
                                "unit_id": "u1",
                                "text": "今日です。",
                                "original_rendered_yomi": "今日/キョウ です/デス 。/。",
                                "proposed_rendered_yomi": "今日/コンニチ です/デス 。/。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            }

            summary = import_issue_payloads(
                issue_payload=issue,
                comment_payloads=[],
                repo="owner/repo",
                issue_number=12,
                review_pack_root=root / "review_packs",
                submission_store_dir=store,
                review_stage="finalized_correction",
            )

            self.assertEqual(summary["imported_submission_count"], 1)
            stored = sorted(store.glob("*.json"))
            self.assertEqual(len(stored), 1)
            payload = json.loads(stored[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["review_stage"], "finalized_correction")
            self.assertEqual(payload["_source_issue"]["issue_number"], 12)
            self.assertTrue(payload["submission_id"].startswith("finalized_correction__issue_12__"))


if __name__ == "__main__":
    unittest.main()
