import json
import subprocess
from types import SimpleNamespace
import pytest
from mtr_pipeline import update_pr


@pytest.mark.parametrize("state,retry,status,reopened", [
    ("OPEN", False, "review-open", False), ("CLOSED", False, "review-deferred", False),
    ("CLOSED", True, "review-open", True), ("MERGED", True, "review-merged", False)])
def test_previous_review_policy(monkeypatch, tmp_path, state, retry, status, reopened):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps([{"url": "https://github.com/test/repo/pull/1", "state": state, "number": 1}]))
    monkeypatch.setattr(update_pr.subprocess, "run", run)
    result = update_pr.previous_review(tmp_path, "test/repo", "automation/test", retry_closed=retry)
    assert result["status"] == status
    assert len(calls) == 1 + reopened
    if reopened:
        assert calls[-1][:4] == ["gh", "pr", "reopen", "1"]


def test_query_error_and_failed_reopen_propagate(monkeypatch, tmp_path):
    calls = []
    def fail(args, **kwargs):
        calls.append(args)
        if args[2] == "list":
            return SimpleNamespace(stdout=json.dumps([{"url": "test", "state": "CLOSED", "number": 1}]))
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(update_pr.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        update_pr.previous_review(tmp_path, "test/repo", "automation/test", retry_closed=True)
    assert len(calls) == 2
