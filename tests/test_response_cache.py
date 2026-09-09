from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yomi_corpus.llm.config import load_llm_task_config
from yomi_corpus.llm.response_cache import ResponseCache, request_identity, seed_results
from yomi_corpus.llm.runner import run_llm_task
from yomi_corpus.llm.schemas import PromptItem
from yomi_corpus.speculative_worker import (
    collect,
    import_batch_output,
    priority_reason,
    submit_prepared,
)


@pytest.fixture
def task():
    return load_llm_task_config("config/llm/yomi_reading.toml")


@pytest.fixture
def item():
    return PromptItem("old", "**test** ->", {"surface": "test"})


def good():
    return {
        "status": "completed",
        "raw_text": '{"test":"テスト"}',
        "usage": {"input_tokens": 100, "output_tokens": 5},
    }


def test_exact_identity(task, item):
    assert request_identity(task, item) == request_identity(
        task, replace(item, item_id="new", metadata={})
    )
    for field, value in [
        ("model", "different"),
        ("max_output_tokens", 123),
        ("verbosity", "high"),
        ("reasoning_effort", "medium"),
        ("text_format", "text"),
        ("enable_web_search", True),
        ("batch_endpoint", "/different"),
    ]:
        assert request_identity(task, item) != request_identity(
            replace(task, **{field: value}), item
        )
    assert request_identity(task, item) != request_identity(
        task, replace(item, prompt=item.prompt + " ")
    )


def test_validation_rebinding_and_usage(tmp_path, task, item):
    cache = ResponseCache(tmp_path / "cache.db")
    assert cache.lookup(task, item) is None
    cache.store(task, item, good(), "batch1")
    cache.store(task, item, {**good(), "raw_text": '{"test":"テストー"}'}, "batch2")
    result = cache.lookup(task, replace(item, item_id="new"))
    assert result["item_id"] == "new"
    assert result["parsed"] == {"test": "テスト"}
    assert result["usage"]["input_tokens"] == 0
    assert result["metadata"]["response_cache"]["original_usage"]["input_tokens"] == 100
    assert cache.lookup(task, replace(item, metadata={"surface": "other"})) is None
    for snapshot in [
        {**good(), "status": "incomplete"},
        {**good(), "raw_text": '{"test":"TEST"}'},
        {**good(), "raw_text": '{"other":"テスト"}'},
    ]:
        with pytest.raises(ValueError):
            cache.store(task, item, snapshot, "bad")


def test_pending_foreground_is_not_replaced(tmp_path, task, item):
    cache = ResponseCache(tmp_path / "cache.db")
    cache.store(task, item, good(), "origin")
    output = tmp_path / "out.jsonl"
    seed_results(cache, task, [item], output, [item.item_id])
    assert output.read_text() == ""
    seed_results(cache, task, [item], output)
    seed_results(cache, task, [item], output)
    assert len(output.read_text().splitlines()) == 1


def test_cache_lock_is_bounded(tmp_path):
    path = tmp_path / "cache.db"
    cache = ResponseCache(path)
    with cache.connect() as db:
        db.execute("BEGIN EXCLUSIVE")
        code = "import sqlite3,sys,time; t=time.monotonic(); c=sqlite3.connect(sys.argv[1],timeout=.15)\ntry: c.execute('SELECT * FROM responses'); sys.exit(2)\nexcept sqlite3.OperationalError: assert time.monotonic()-t < 1"
        subprocess.run([sys.executable, "-c", code, str(path)], check=True, timeout=5)
    assert cache.summary()["responses"] == 0


def test_runner_fails_open(tmp_path, monkeypatch):
    from yomi_corpus.llm import runner

    fake = Mock(return_value="ordinary")
    monkeypatch.setattr(runner, "_run_llm_task_uncached", fake)
    assert (
        run_llm_task(
            "absent",
            "absent",
            str(tmp_path / "out"),
            execution_mode="background",
            response_cache_path=tmp_path / "cache.db",
        )
        == "ordinary"
    )
    fake.assert_called_once()


def test_runner_hits_without_submitting(tmp_path, monkeypatch, task, item):
    from yomi_corpus.llm import runner

    cache = ResponseCache(tmp_path / "cache.db")
    cache.store(task, item, good(), "batch")
    monkeypatch.setattr(runner, "build_prompt_items", lambda *_: [item])
    monkeypatch.setattr(runner, "load_jsonl_rows", lambda *_: [])
    backend = Mock()
    monkeypatch.setattr(runner, "OpenAIResponsesBackend", lambda **_: backend)
    source = tmp_path / "input.jsonl"
    source.write_text("")
    result = run_llm_task(
        "unused",
        str(source),
        str(tmp_path / "out"),
        execution_mode="background",
        task_config_override=task,
        job_dir=str(tmp_path / "job"),
        response_cache_path=cache.path,
    )
    assert result.status == "completed"
    backend.submit_background_item.assert_not_called()
    backend.retrieve_response.assert_not_called()


def job_for(task, item):
    key, _ = request_identity(task, item)
    return {
        "id": "job",
        "state": "prepared",
        "task": asdict(task),
        "items": {key: asdict(item)},
        "endpoint": task.batch_endpoint,
        "completion_window": task.batch_completion_window,
    }


def batch_output(task, item):
    key, _ = request_identity(task, item)
    return json.dumps(
        {
            "custom_id": key,
            "response": {
                "status_code": 200,
                "body": {
                    "id": "response",
                    "status": "completed",
                    "output_text": '{"test":"テスト"}',
                    "usage": {"input_tokens": 100, "output_tokens": 5},
                },
            },
        }
    )


def test_partial_batch_and_idempotence(tmp_path, task, item):
    cache = ResponseCache(tmp_path / "cache.db")
    job = job_for(task, item)
    job.update(state="submitted", batch_id="batch")
    cache.save_job(job)
    client = Mock()
    client.retrieve.return_value = {
        "id": "batch",
        "status": "expired",
        "output_file_id": "output",
    }
    client.download.return_value = batch_output(task, item)
    collect(cache, client)
    import_batch_output(cache, job, batch_output(task, item))
    collect(cache, client)
    assert cache.lookup(task, item)
    assert cache.summary()["batch_usage"]["input_tokens"] == 100
    assert cache.summary()["jobs"] == {"fetched": 1}


def test_ambiguous_submission_is_not_repeated(tmp_path, task, item):
    cache = ResponseCache(tmp_path / "cache.db")
    job = job_for(task, item)
    cache.save_job(job)
    client = Mock()
    client.upload.return_value = "file"
    client.submit.side_effect = TimeoutError("unknown")
    with pytest.raises(TimeoutError):
        submit_prepared(cache, client, job, tmp_path)
    assert cache.active_jobs()[0]["state"] == "submitting"
    client.reconcile.return_value = None
    collect(cache, client)
    client.submit.assert_called_once()
    client.reconcile.return_value = {"id": "recovered", "status": "in_progress"}
    collect(cache, client)
    assert cache.active_jobs()[0]["batch_id"] == "recovered"


def test_priority_never_creates_or_removes_refill_lock(tmp_path, monkeypatch):
    from yomi_corpus import speculative_worker as worker

    lock = tmp_path / "data/state/refill/dev.lock"
    lock.parent.mkdir(parents=True)
    monkeypatch.setattr(
        worker,
        "aggregate_document_queue_summary",
        lambda **_: {"pool_counts": {"bulk-ready": 100}},
    )
    monkeypatch.setattr(
        worker,
        "load_review_sync_config",
        lambda *_: type("Config", (), {"bulk_review_target_ready_docs": 100})(),
    )
    assert priority_reason(tmp_path, "dev", None) is None
    assert not lock.exists()
    lock.write_text("owned by refill")
    assert priority_reason(tmp_path, "dev", None) == "refill_active"
    assert lock.read_text() == "owned by refill"


@pytest.mark.parametrize("case", ["empty", "reordered", "failure"])
def test_preparation_checkpoint_and_reorder(tmp_path, monkeypatch, task, case):
    from yomi_corpus import speculative_worker as worker

    config = {
        "database": "cache.db",
        "submit_enabled": True,
        "horizon": 500,
        "chunk_size": 10,
        "max_chunks_per_pass": 1,
        "max_pending_jobs": 50,
        "max_requests_per_job": 5000,
        "preparation_timeout_seconds": 60,
        "retention_days": 90,
    }
    monkeypatch.setattr(worker, "load_cache_config", lambda *_: config)
    monkeypatch.setattr(worker, "PipelineWorkspace", lambda *_: None)
    monkeypatch.setattr(worker, "priority_reason", lambda *_: None)
    manifest = {"source_content_sha256": "source", "order_generation": 1, "cursor": 1}
    prepared = []

    def horizon(*_):
        return manifest, [2] if case == "reordered" and prepared else [1]

    monkeypatch.setattr(worker, "upcoming", horizon)

    def prepare(*_):
        prepared.append(True)
        if case == "failure":
            raise subprocess.CalledProcessError(1, "prepare")
        return {
            "task": asdict(task),
            "items": [],
            "implementation_digest": "code",
            "report": {},
        }

    monkeypatch.setattr(worker, "prepare_process", prepare)
    client = Mock(side_effect=AssertionError("No API client expected"))
    monkeypatch.setattr(worker, "BatchClient", client)
    result = worker.run_pass(tmp_path, "dev")
    cache = ResponseCache(tmp_path / "cache.db")
    docs = cache.records("documents")
    if case == "empty":
        assert len(docs) == 1
        assert docs[0]["job_id"] is None
        worker.run_pass(tmp_path, "dev")
        assert len(prepared) == 1
    elif case == "reordered":
        assert not docs
        assert result["reason"] == "queue_changed"
    else:
        assert docs[0]["failures"] == 1
        worker.run_pass(tmp_path, "dev")
        assert len(prepared) == 1


def test_abandoned_checkpoint_can_be_reconsidered(tmp_path, task, item):
    cache = ResponseCache(tmp_path / "cache.db")
    job = job_for(task, item)
    job["documents"] = ["source:1"]
    cache.save_document("source:1", {"key": "source:1", "job_id": job["id"]})
    cache.forget_job_documents(job)
    assert cache.records("documents") == []


def test_capture_requires_submission_fingerprint(tmp_path, task, item):
    from yomi_corpus.llm.response_cache import capture_results

    cache = ResponseCache(tmp_path / "cache.db")
    output = tmp_path / "results.jsonl"
    result = {
        **good(),
        "item_id": item.item_id,
        "metadata": {"api_response": {"status": "completed", "request_key": "old"}},
    }
    output.write_text(json.dumps(result) + "\n")
    capture_results(cache, task, [item], output)
    assert cache.summary()["responses"] == 0
    result["metadata"]["api_response"]["request_key"] = request_identity(task, item)[0]
    output.write_text(json.dumps(result) + "\n")
    capture_results(cache, task, [item], output)
    assert cache.summary()["responses"] == 1
