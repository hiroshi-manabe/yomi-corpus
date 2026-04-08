from __future__ import annotations

import unittest

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from import_alphabetic_review_issue import (
    extract_attachment_records,
    extract_attachment_urls,
    extract_inline_submission_records,
    fetch_issue_comments,
    parse_submissions_from_text,
)


class AlphabeticReviewIssueImportTests(unittest.TestCase):
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

    def test_parse_submissions_from_text_accepts_raw_json(self) -> None:
        submissions = parse_submissions_from_text(
            """
            {
              "submission_type": "review_patch",
              "review_stage": "alphabetic_candidate_review",
              "pack_id": "pack_1",
              "submission_id": "sub_1"
            }
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
              "review_stage": "alphabetic_candidate_review",
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
                  "review_stage": "alphabetic_candidate_review",
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
                      "review_stage": "alphabetic_candidate_review",
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


if __name__ == "__main__":
    unittest.main()
