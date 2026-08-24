from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.issue_watch import run_issue_watch_pass


def submission(submission_id: str, doc_id: str) -> dict:
    return {
        "schema_version": 1,
        "submission_type": "review_patch",
        "review_stage": "yomi_final_review",
        "pack_id": "pack-1",
        "submission_id": submission_id,
        "task": {"doc_ids": [doc_id]},
        "items": [],
    }


def issue(number: int, payloads: list[dict]) -> dict:
    body = "\n".join(
        f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```" for payload in payloads
    )
    return {"number": number, "body": body}


class IssueWatchTests(unittest.TestCase):
    def write_pack(self, root: Path, *doc_ids: str) -> None:
        path = root / "data" / "review_packs" / "yomi_final" / "pack-1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack-1",
                    "documents": [{"doc_id": doc_id} for doc_id in doc_ids],
                }
            ),
            encoding="utf-8",
        )

    def test_acknowledges_valid_submission_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_pack(root, "doc-1")
            issues = [issue(10, [submission("s1", "doc-1")])]
            first = run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=100,
                fetch_issues=lambda *_args, **_kwargs: issues,
                fetch_comments=lambda *_args: [],
            )
            path = Path(first["acknowledgment_path"])
            first_text = path.read_text(encoding="utf-8")
            second = run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=101,
                fetch_issues=lambda *_args, **_kwargs: issues,
                fetch_comments=lambda *_args: [],
            )
            self.assertEqual(first["status"], "changed")
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(path.read_text(encoding="utf-8"), first_text)
            self.assertEqual(json.loads(first_text)["records"][0]["doc_ids"], ["doc-1"])

    def test_marks_overlapping_submissions_as_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.write_pack(Path(tmp), "doc-1")
            result = run_issue_watch_pass(
                Path(tmp),
                track_name="dev",
                repo="owner/repo",
                now_epoch=100,
                fetch_issues=lambda *_args, **_kwargs: [
                    issue(10, [submission("s1", "doc-1")]),
                    issue(11, [submission("s2", "doc-1")]),
                ],
                fetch_comments=lambda *_args: [],
            )
            payload = json.loads(Path(result["acknowledgment_path"]).read_text(encoding="utf-8"))
            self.assertEqual(result["conflict_count"], 1)
            self.assertEqual(payload["conflicting_doc_ids"], ["doc-1"])
            self.assertTrue(all(row["conflict"] for row in payload["records"]))

    def test_closed_issue_removes_acknowledgment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_pack(root, "doc-1")
            run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=100,
                fetch_issues=lambda *_args, **_kwargs: [issue(10, [submission("s1", "doc-1")])],
                fetch_comments=lambda *_args: [],
            )
            result = run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=101,
                fetch_issues=lambda *_args, **_kwargs: [],
                fetch_comments=lambda *_args: [],
            )
            payload = json.loads(Path(result["acknowledgment_path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["records"], [])

    def test_no_trigger_probe_does_not_start_retry_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_pack(root, "doc-1")
            issues = [issue(10, [submission("s1", "doc-1")])]
            run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=100,
                mark_triggered=False,
                fetch_issues=lambda *_args, **_kwargs: issues,
                fetch_comments=lambda *_args: [],
            )
            result = run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=101,
                fetch_issues=lambda *_args, **_kwargs: issues,
                fetch_comments=lambda *_args: [],
            )
            self.assertTrue(result["trigger_required"])


if __name__ == "__main__":
    unittest.main()
