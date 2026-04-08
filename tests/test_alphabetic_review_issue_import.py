from __future__ import annotations

import unittest

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from import_alphabetic_review_issue import extract_attachment_urls


class AlphabeticReviewIssueImportTests(unittest.TestCase):
    def test_extract_attachment_urls_from_issue_and_comments(self) -> None:
        payloads = [
            {
                "body": "[submission](https://github.com/user-attachments/files/12345/a.json)",
            },
            {
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


if __name__ == "__main__":
    unittest.main()
