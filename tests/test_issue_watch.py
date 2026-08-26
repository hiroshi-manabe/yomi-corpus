from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.issue_watch import (
    publish_acknowledgments_to_github_pages,
    run_issue_watch_pass,
)


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
    def test_publishes_acknowledgments_directly_and_skips_known_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "dev.acknowledgments.json"
            source.write_text('{"records":[{"submission_id":"s1"}]}\n', encoding="utf-8")
            calls = []
            published = False

            def fake_run(command, **_kwargs):
                nonlocal published
                calls.append(command)
                if "GET" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=json.dumps(
                            {
                                "sha": "new" if published else "old",
                                "content": base64.b64encode(
                                    source.read_bytes() if published else b"{}\n"
                                ).decode("ascii"),
                            }
                        ),
                        stderr="",
                    )
                published = True
                return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

            first = publish_acknowledgments_to_github_pages(
                root,
                track_name="dev",
                repo="owner/repo",
                acknowledgment_path=source,
                run=fake_run,
            )
            second = publish_acknowledgments_to_github_pages(
                root,
                track_name="dev",
                repo="owner/repo",
                acknowledgment_path=source,
                run=fake_run,
            )

            self.assertEqual(first["status"], "published")
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(len(calls), 3)
            self.assertIn("sha=old", calls[1])

    def write_pack(self, root: Path, *doc_ids: str) -> None:
        path = root / "data" / "review_packs" / "yomi_final" / "pack-1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "review_stage": "yomi_final_review",
                    "pack_id": "pack-1",
                    "track_name": "dev",
                    "batch_name": "batch-1",
                    "documents": [{"doc_id": doc_id} for doc_id in doc_ids],
                }
            ),
            encoding="utf-8",
        )

    def write_document_state(self, root: Path, doc_id: str, state: str) -> None:
        path = root / "data" / "pipeline" / "document_states" / "batch-1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "documents": [
                        {
                            "doc_id": doc_id,
                            "state": state,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def write_imported_submission(self, root: Path, payload: dict) -> None:
        path = (
            root
            / "data"
            / "review_submissions"
            / "yomi_final"
            / f"{payload['submission_id']}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    **payload,
                    "_source_issue": {"issue_number": 10, "comment_id": None},
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

    def test_closed_issue_keeps_acknowledgment_until_imported_submission_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = submission("s1", "doc-1")
            self.write_pack(root, "doc-1")
            self.write_document_state(root, "doc-1", "final_pending")
            run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=100,
                fetch_issues=lambda *_args, **_kwargs: [issue(10, [payload])],
                fetch_comments=lambda *_args: [],
            )
            self.write_imported_submission(root, payload)

            published_states = {"doc-1": "final_pending"}
            with patch(
                "yomi_corpus.issue_watch._published_document_states",
                side_effect=lambda *_args: dict(published_states),
            ):
                pending = run_issue_watch_pass(
                    root,
                    track_name="dev",
                    repo="owner/repo",
                    now_epoch=101,
                    fetch_issues=lambda *_args, **_kwargs: [],
                    fetch_comments=lambda *_args: [],
                )
                pending_payload = json.loads(
                    Path(pending["acknowledgment_path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(pending_payload["records"][0]["submission_id"], "s1")
                self.assertEqual(pending_payload["records"][0]["doc_ids"], ["doc-1"])

                # Local application alone cannot remove globally visible status.
                self.write_document_state(root, "doc-1", "final_reviewed")
                local_only = run_issue_watch_pass(
                    root,
                    track_name="dev",
                    repo="owner/repo",
                    now_epoch=102,
                    fetch_issues=lambda *_args, **_kwargs: [],
                    fetch_comments=lambda *_args: [],
                )
                local_only_payload = json.loads(
                    Path(local_only["acknowledgment_path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(local_only_payload["records"][0]["submission_id"], "s1")

                published_states["doc-1"] = "final_reviewed"
                published = run_issue_watch_pass(
                    root,
                    track_name="dev",
                    repo="owner/repo",
                    now_epoch=103,
                    fetch_issues=lambda *_args, **_kwargs: [],
                    fetch_comments=lambda *_args: [],
                )
                published_payload = json.loads(
                    Path(published["acknowledgment_path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(published_payload["records"], [])

    def test_unreadable_published_state_keeps_imported_acknowledgment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = submission("s1", "doc-1")
            self.write_pack(root, "doc-1")
            self.write_document_state(root, "doc-1", "final_pending")
            run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=100,
                fetch_issues=lambda *_args, **_kwargs: [issue(10, [payload])],
                fetch_comments=lambda *_args: [],
            )
            self.write_imported_submission(root, payload)
            self.write_document_state(root, "doc-1", "final_reviewed")

            with patch(
                "yomi_corpus.issue_watch._published_document_states",
                return_value=None,
            ):
                result = run_issue_watch_pass(
                    root,
                    track_name="dev",
                    repo="owner/repo",
                    now_epoch=101,
                    fetch_issues=lambda *_args, **_kwargs: [],
                    fetch_comments=lambda *_args: [],
                )
            acknowledgment = json.loads(
                Path(result["acknowledgment_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(acknowledgment["records"][0]["submission_id"], "s1")

    def test_no_trigger_probe_does_not_consume_event_trigger(self) -> None:
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

    def test_triggered_payload_is_not_retriggered(self) -> None:
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
            second = run_issue_watch_pass(
                root,
                track_name="dev",
                repo="owner/repo",
                now_epoch=10000,
                fetch_issues=lambda *_args, **_kwargs: issues,
                fetch_comments=lambda *_args: [],
            )
            self.assertTrue(first["trigger_required"])
            self.assertFalse(second["trigger_required"])


if __name__ == "__main__":
    unittest.main()
