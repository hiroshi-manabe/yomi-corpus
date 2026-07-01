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


if __name__ == "__main__":
    unittest.main()
