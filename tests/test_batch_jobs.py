from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.batch_jobs import (
    ITEMS_FILENAME,
    MANIFEST_FILENAME,
    PARSED_RESULTS_FILENAME,
    RAW_RESULTS_FILENAME,
    REQUESTS_FILENAME,
    STATUS_FILENAME,
    fetch_batch_job,
    list_batch_jobs,
    poll_batch_job,
    prepare_batch_job,
    submit_batch_job,
)


class FakeBatchBackend:
    def submit_batch(self, requests_jsonl_path, *, endpoint, completion_window):
        return {
            "input_file_id": "file-input-1",
            "batch_id": "batch-1",
            "status": "validating",
            "created_at": 123,
            "output_file_id": None,
            "error_file_id": None,
        }

    def retrieve_batch(self, batch_id):
        return {
            "batch_id": batch_id,
            "status": "completed",
            "output_file_id": "file-output-1",
            "error_file_id": None,
            "usage": {
                "input_tokens": 600,
                "input_tokens_details": {"cached_tokens": 512},
                "output_tokens": 5,
                "output_tokens_details": {"reasoning_tokens": 1},
                "total_tokens": 605,
            },
        }

    def download_file(self, file_id, output_path):
        Path(output_path).write_text(
            json.dumps(
                {
                    "custom_id": "led zeppelin",
                    "response": {
                        "output_text": '{"status":"out_of_scope","confidence":"medium","note":"band name"}',
                        "body": {
                            "usage": {
                                "input_tokens": 600,
                                "input_tokens_details": {"cached_tokens": 512},
                                "output_tokens": 5,
                                "output_tokens_details": {"reasoning_tokens": 1},
                                "total_tokens": 605,
                            }
                        },
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


class BatchJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = PROJECT_ROOT / "tests" / "tmp_batch_job"
        if self.tmp_root.exists():
            for path in sorted(self.tmp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        self.tmp_root.mkdir(parents=True, exist_ok=True)

        self.input_path = self.tmp_root / "input.jsonl"
        self.input_path.write_text(
            json.dumps(
                {
                    "entity_key": "led zeppelin",
                    "surface_forms": ["Led Zeppelin"],
                    "occurrence_count": 1,
                    "unit_count": 1,
                    "example_texts": ["Led Zeppelinが好きです。"],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.job_dir = self.tmp_root / "job_0001"

    def tearDown(self) -> None:
        if self.tmp_root.exists():
            for path in sorted(self.tmp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()

    def test_prepare_submit_poll_fetch_lifecycle(self) -> None:
        prepare_batch_job(
            "config/llm/alphabetic_entity_judge.toml",
            str(self.input_path),
            str(self.job_dir),
        )
        self.assertTrue((self.job_dir / REQUESTS_FILENAME).exists())
        self.assertTrue((self.job_dir / ITEMS_FILENAME).exists())
        self.assertTrue((self.job_dir / MANIFEST_FILENAME).exists())
        status = json.loads((self.job_dir / STATUS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "prepared")

        submit_status = submit_batch_job(str(self.job_dir), backend=FakeBatchBackend())
        self.assertEqual(submit_status["batch_id"], "batch-1")
        self.assertEqual(submit_status["state"], "running")

        poll_status = poll_batch_job(str(self.job_dir), backend=FakeBatchBackend())
        self.assertEqual(poll_status["remote_status"], "completed")
        self.assertEqual(poll_status["state"], "completed")
        self.assertEqual(poll_status["remote_snapshot"]["usage"]["input_tokens"], 600)

        fetch_status = fetch_batch_job(str(self.job_dir), backend=FakeBatchBackend())
        self.assertEqual(fetch_status["state"], "fetched")
        self.assertTrue((self.job_dir / RAW_RESULTS_FILENAME).exists())
        self.assertTrue((self.job_dir / PARSED_RESULTS_FILENAME).exists())
        parsed_rows = [
            json.loads(line)
            for line in (self.job_dir / PARSED_RESULTS_FILENAME).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(parsed_rows[0]["item_id"], "led zeppelin")
        self.assertEqual(parsed_rows[0]["parsed"]["status"], "out_of_scope")
        self.assertEqual(parsed_rows[0]["usage"]["cached_input_tokens"], 512)

    def test_list_batch_jobs(self) -> None:
        prepare_batch_job(
            "config/llm/alphabetic_entity_judge.toml",
            str(self.input_path),
            str(self.job_dir),
        )
        jobs = list_batch_jobs(str(self.tmp_root))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "prepared")
