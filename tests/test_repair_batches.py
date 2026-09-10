from copy import deepcopy
import json

import pytest

from yomi_corpus.llm.config import apply_llm_profile, load_llm_task_config
from yomi_corpus.llm.repair_batches import run_repair_batch_task
from yomi_corpus.pipeline import write_effective_yomi_strong_repair_results


class Client:
    def __init__(self):
        self.jobs = []
        self.files = {}
        self.lose_submission_response = False
        self.fail_retrieve = set()

    def upload(self, path):
        return "input-file"

    def submit(self, snapshot, file_id):
        remote = {"id": f"batch-{len(self.jobs)}", "status": "in_progress"}
        self.jobs.append((deepcopy(snapshot), remote))
        if self.lose_submission_response:
            self.lose_submission_response = False
            raise TimeoutError("lost response")
        return deepcopy(remote)

    def retrieve(self, identifier):
        if identifier in self.fail_retrieve:
            raise TimeoutError("poll failed")
        return deepcopy(
            next(remote for _, remote in self.jobs if remote["id"] == identifier)
        )

    def reconcile(self, identifier):
        return next(
            (
                deepcopy(remote)
                for snapshot, remote in self.jobs
                if snapshot["id"] == identifier
            ),
            None,
        )

    def download(self, identifier):
        return self.files[identifier]

    def finish(self, index, *, missing=(), invalid=(), status="completed"):
        snapshot, remote = self.jobs[index]
        rows = []
        for key, item in snapshot["items"].items():
            if item["item_id"] in missing:
                continue
            parsed = [
                {
                    "surface": item["metadata"]["rejected_span"],
                    "reading": "ネコ",
                    "used_web_search": False,
                }
            ]
            text = "malformed" if item["item_id"] in invalid else json.dumps(parsed)
            rows.append(
                {
                    "custom_id": key,
                    "response": {
                        "status_code": 200,
                        "body": {
                            "status": "completed",
                            "output_text": text,
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 10,
                                "total_tokens": 20,
                            },
                        },
                    },
                }
            )
        self.files[remote["id"]] = "".join(json.dumps(row) + "\n" for row in rows)
        remote.update(status=status, output_file_id=remote["id"])


@pytest.fixture
def setup(tmp_path):
    task = apply_llm_profile(
        load_llm_task_config("config/llm/yomi_repair.toml"), "economy"
    )
    client = Client()
    source = tmp_path / "queue.jsonl"
    output = tmp_path / "results.jsonl"
    rows = [{"item_id": "a", "text": "猫。", "rejected_span": "猫"}]

    def run(rows, round_name="primary"):
        source.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
        return run_repair_batch_task(
            source,
            output,
            task_config=task,
            job_dir=tmp_path / round_name,
            client=client,
        )

    return client, rows, run, source, output


def test_submit_poll_and_queue_growth(setup):
    client, rows, run, _, output = setup
    assert run(rows).status == "running"
    frozen = deepcopy(client.jobs[0][0])
    rows.append({"item_id": "b", "text": "猫です。", "rejected_span": "猫"})
    assert run(rows).status == "running"
    assert len(client.jobs) == 2
    assert [item["item_id"] for item in client.jobs[1][0]["items"].values()] == ["b"]
    assert client.jobs[0][0] == frozen
    client.finish(0)
    assert run(rows).completed_items == 1
    client.finish(1)
    assert run(rows).status == "completed"
    assert run(list(reversed(rows))).status == "completed"
    assert len(client.jobs) == 2
    assert {
        json.loads(line)["item_id"] for line in output.read_text().splitlines()
    } == {"a", "b"}


def test_changed_request_same_item_id_needs_new_job(setup):
    client, rows, run, _, _ = setup
    run(rows)
    client.finish(0)
    assert run(rows).status == "completed"
    rows[0]["text"] = "別の文脈の猫。"
    assert run(rows).status == "running"
    assert len(client.jobs) == 2
    assert run(rows).completed_items == 0


def test_uncertain_submission_reconciles_without_duplicate(setup):
    client, rows, run, _, _ = setup
    client.lose_submission_response = True
    assert run(rows).status == "running"
    assert run(rows).status == "running"
    assert len(client.jobs) == 1
    client.finish(0)
    assert run(rows).status == "completed"


def test_poll_failure_does_not_block_other_cohorts(setup):
    client, rows, run, _, _ = setup
    run(rows)
    rows.append({"item_id": "b", "text": "別の猫。", "rejected_span": "猫"})
    run(rows)
    client.fail_retrieve.add("batch-0")
    client.finish(1)
    assert run(rows).completed_items == 1
    assert len(client.jobs) == 2


def test_submit_rate_limit_retries_after_backoff(setup, monkeypatch):
    client, rows, run, _, _ = setup
    clock = [1000]
    monkeypatch.setattr("yomi_corpus.llm.repair_batches.time.time", lambda: clock[0])
    original = client.submit

    class RateLimit(Exception):
        status_code = 429

    def reject(snapshot, file_id):
        raise RateLimit("rate limited")

    client.submit = reject
    assert run(rows).status == "running"
    client.submit = original
    assert run(rows).status == "running"
    assert len(client.jobs) == 0
    clock[0] += 301
    assert run(rows).status == "running"
    assert len(client.jobs) == 1


@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_only_failed_items_enter_retry_round(setup, failure, tmp_path):
    client, rows, run, source, output = setup
    rows.append({"item_id": "b", "text": "別の猫。", "rejected_span": "猫"})
    run(rows)
    client.finish(
        0,
        **{failure: ("b",)},
        status="expired" if failure == "missing" else "completed",
    )
    summary = run(rows)
    assert summary.status == "completed"
    assert summary.failed_items == 1
    retry = write_effective_yomi_strong_repair_results(
        queue_jsonl=source,
        result_jsonls=[output],
        output_jsonl=tmp_path / "effective.jsonl",
    )
    assert [row["item_id"] for row in retry] == ["b"]
    run(retry, "retry1")
    assert [item["item_id"] for item in client.jobs[1][0]["items"].values()] == ["b"]


def test_pending_removed_request_does_not_block_current_queue(setup):
    client, rows, run, _, _ = setup
    run(rows)
    new = [{"item_id": "b", "text": "別の猫。", "rejected_span": "猫"}]
    run(new)
    client.finish(1)
    assert run(new).status == "completed"


def test_rate_limits_do_not_consume_response_retry_rounds(setup, monkeypatch):
    client, rows, run, _, _ = setup
    clock = [1000]
    monkeypatch.setattr("yomi_corpus.llm.repair_batches.time.time", lambda: clock[0])
    rows.append({"item_id": "b", "text": "別の猫。", "rejected_span": "猫"})
    run(rows)
    client.finish(0)
    raw = [json.loads(line) for line in client.files["batch-0"].splitlines()]
    raw[1]["response"] = {
        "status_code": 429,
        "body": {"error": {"code": "rate_limit_exceeded"}},
    }
    client.files["batch-0"] = "".join(json.dumps(row) + "\n" for row in raw)
    assert run(rows).status == "running"
    assert run(rows).failed_items == 0
    assert len(client.jobs) == 1
    clock[0] += 301
    assert run(rows).status == "running"
    assert len(client.jobs) == 2
    assert len(client.jobs[1][0]["items"]) == 1
    client.finish(1)
    assert run(rows).status == "completed"
    assert run(rows).failed_items == 0


def test_no_credentials_needed_for_collected_jobs(setup, monkeypatch):
    client, rows, run, source, output = setup
    run(rows)
    client.finish(0)
    run(rows)
    monkeypatch.setattr(
        "yomi_corpus.llm.repair_batches.RepairBatchClient",
        lambda: pytest.fail("Should not need network"),
    )
    task = apply_llm_profile(
        load_llm_task_config("config/llm/yomi_repair.toml"), "economy"
    )
    assert (
        run_repair_batch_task(
            source, output, task_config=task, job_dir=source.parent / "primary"
        ).status
        == "completed"
    )


def test_pipeline_batch_path_is_nonblocking_and_retries_only_failures(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from yomi_corpus.pipeline import PipelineWorkspace

    client = Client()
    monkeypatch.setattr(
        "yomi_corpus.llm.repair_batches.RepairBatchClient", lambda: client
    )
    monkeypatch.setattr(
        "yomi_corpus.pipeline.run_llm_task",
        lambda *a, **k: pytest.fail("Blocking runner used"),
    )
    workspace = PipelineWorkspace(tmp_path)
    monkeypatch.setattr(
        workspace,
        "load_batch_state",
        lambda name: SimpleNamespace(
            llm_policy={"yomi_repair": "economy"},
            llm_execution_policy={"yomi_repair": "batch"},
        ),
    )
    monkeypatch.setattr(
        workspace, "_prepare_strong_repair_review_pack", lambda *a, **k: {}
    )
    monkeypatch.setattr(workspace, "_llm_completed_artifacts", lambda **k: {})
    monkeypatch.setattr(
        "yomi_corpus.pipeline.apply_yomi_strong_repair_results_file",
        lambda **k: {
            "stage_complete": True,
            "confirmed": True,
            "queued_items": 2,
            "applied_items": 2,
            "unapplied_items": 0,
            "noop_items": 0,
            "unresolved_items": 0,
            "parse_error_items": 0,
            "missing_results": 0,
        },
    )
    directory = workspace.batch_dir("test")
    directory.mkdir(parents=True)
    rows = [
        {"item_id": key, "text": text, "rejected_span": "猫"}
        for key, text in [("a", "猫。"), ("b", "別の猫。")]
    ]
    queue = directory / "yomi_strong_repair_queue.jsonl"
    queue.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    assert workspace._run_yomi_strong_repair("test")["stage_complete"] is False
    client.finish(0, invalid=("b",))
    assert workspace._run_yomi_strong_repair("test")["stage_complete"] is False
    assert len(client.jobs) == 2
    assert [item["item_id"] for item in client.jobs[1][0]["items"].values()] == ["b"]
    client.finish(1)
    assert workspace._run_yomi_strong_repair("test").get("stage_complete", True)
    assert workspace._run_yomi_strong_repair("test").get("stage_complete", True)
    assert len(client.jobs) == 2
    rows[0]["text"] = "新しい文脈の猫。"
    queue.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    assert workspace._run_yomi_strong_repair("test")["stage_complete"] is False
    assert len(client.jobs) == 3
    assert [item["item_id"] for item in client.jobs[2][0]["items"].values()] == ["a"]
