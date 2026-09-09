from yomi_corpus.queue_preflight import (
    stable_checks,
    next_unchecked,
    check_with_isolation,
)
import pytest


def test_existing_checks_survive_code_and_model_updates():
    old = {"old-code-model:42": {"source_sha256": "source", "source_line": 42}}
    assert "source:42" in stable_checks(old)
    assert "new-source:42" not in stable_checks(old)


def test_queue_order_and_failures():
    class Store:
        def read_slots(self, start, count):
            return [900, 42, 200][start - 1 : start - 1 + count]

    manifest = {"source_content_sha256": "s", "cursor": 1, "document_count": 3}
    assert next_unchecked(Store(), manifest, {"s:42": {}}, {"s:900": {}}, 10) == [200]


def test_failure_isolated_and_later_documents_checked():
    calls, records = [], []

    def check(lines):
        calls.append(lines)
        return (
            {"status": "failed", "error_type": "YomiTokenError"}
            if 2 in lines
            else {"status": "passed"}
        )

    check_with_isolation(
        [1, 2, 3],
        check,
        lambda lines, result: records.append((lines, result["status"])),
    )
    assert calls == [[1, 2, 3], [1], [2], [3]]
    assert records == [([1], "passed"), ([2], "failed"), ([3], "passed")]


def test_infrastructure_error_not_recorded_as_document_failure():
    with pytest.raises(RuntimeError, match="infrastructure"):
        check_with_isolation(
            [1, 2],
            lambda lines: {"status": "failed", "error_type": "OSError"},
            lambda *args: pytest.fail("must not checkpoint infrastructure failure"),
        )


def test_successful_chunk_not_split():
    records = []
    check_with_isolation(
        [1, 2],
        lambda lines: {"status": "passed"},
        lambda lines, result: records.append(lines),
    )
    assert records == [[1, 2]]


def test_child_timeout_kills_process_group(monkeypatch, tmp_path):
    import subprocess
    from yomi_corpus import queue_preflight as module

    killed = []

    class Process:
        pid = 123

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("check", timeout)
            return -9

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: killed.append(pid))
    with pytest.raises(subprocess.TimeoutExpired):
        module.run_check(tmp_path, "dev", [1], tmp_path, 1)
    assert killed == [123]
