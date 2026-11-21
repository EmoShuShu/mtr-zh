"""Shared acquisition behavior, with isolated copies of observed WPN failure shapes."""
import importlib
import json
from pathlib import Path
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "mtr_pipeline" if (ROOT / "mtr_pipeline").is_dir() else "ipg_pipeline"
update = importlib.import_module(PACKAGE + ".official_update")
discovery = importlib.import_module(PACKAGE + (".official_discovery" if PACKAGE == "mtr_pipeline" else ".official_update"))
checks = importlib.import_module(PACKAGE + ".official_check")


@pytest.fixture(autouse=True)
def isolated_actions_summary(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def page(fault=None):
    # Synthetic reference pool: same _errors/data shape as the Nov 13/16/18
    # diagnostic pages, without copying the website's unrelated content.
    pool = [["ShallowReactive", 1], {"path": 2, "data": 3, "_errors": 19}, "/en/rules-documents",
            ["ShallowReactive", 4], {"downloadableDocuments": 5}, {"entries": 6}, [7],
            {"fields": 8, "sys": 14}, {"title": 9, "updated": 10, "cta": 11}, discovery.EXPECTED_TITLE,
            "2024-09-24", {"fields": 12}, {"link": 13}, "https://media.wizards.com/test.pdf",
            {"locale": 15, "contentType": 16}, "en", {"sys": 17}, {"id": 18}, "downloadableDocument",
            ["ShallowReactive", 20], {"downloadableDocuments": 21}, None]
    if fault == "missing-entry": pool[9] = "TEST ONLY unrelated document"
    if fault == "missing-collection": del pool[4]["downloadableDocuments"]
    if fault in {"service-error", "service-error-stale-data"}:
        if fault == "service-error": del pool[4]["downloadableDocuments"]
        pool[21] = ["NuxtError", 22]
        pool.extend([{"message": 23, "statusCode": 24}, "[nuxt] instance unavailable", 500])
    if fault == "bad-error-reference": pool[20]["downloadableDocuments"] = 99999
    if fault == "bad-reference": pool[8]["cta"] = 99999
    if fault == "ambiguous":
        pool[6].append(len(pool))
        pool.append(dict(pool[7]))
    payload = json.dumps(pool)
    if fault == "invalid-json": payload = "{broken"
    return f'<script type="application/json" id="__NUXT_DATA__">{payload}</script>'


@pytest.mark.parametrize("fault,category", [
    ("missing-entry", "official-data-missing"), ("missing-collection", "official-data-missing"),
    ("service-error", "official-service-error"), ("service-error-stale-data", "official-service-error"),
    ("bad-reference", "page-validation-error"),
    ("bad-error-reference", "page-validation-error"), ("invalid-json", "page-validation-error"),
    ("ambiguous", "page-validation-error"),
])
def test_page_failures_are_distinguished_without_accepting_incomplete_evidence(fault, category):
    valid_card = (f'<div class="downloadableDocument"><div class="titleText">{discovery.EXPECTED_TITLE}</div>'
                  '<div class="updated">Sep 24, 2024</div><a href="https://media.wizards.com/test.pdf">PDF</a></div>')
    with pytest.raises((ValueError, RuntimeError)) as caught:
        update.parse_rules_page(valid_card + page(fault))
    details = checks.error_details(caught.value, "rules-page")
    assert details["errorCategory"] == category
    if fault.startswith("service-error"):
        assert details["upstreamStatusCode"] == 500
        assert "instance unavailable" in details["error"]
    assert discovery.parse_rules_page(page())["url"] == "https://media.wizards.com/test.pdf"


def test_recovery_after_minutes_preserves_each_page_and_does_not_repeat_success(tmp_path, monkeypatch):
    sleeps, calls = [], []
    pages = [page("missing-entry"), page("service-error"), page()]
    monkeypatch.setattr(update.time, "sleep", sleeps.append)
    def acquire(attempt, evidence):
        calls.append(attempt)
        (tmp_path / f'rules-page-attempt-{attempt}.html').write_text(pages[attempt - 1])
        return update.parse_rules_page(pages[attempt - 1])
    result = update._retry_official_request(acquire, stage="rules-page", diagnostics_dir=tmp_path)
    assert result and calls == [1, 2, 3] and sleeps == [60, 180]
    records = [json.loads((tmp_path / f'rules-page-attempt-{n}.json').read_text()) for n in (1, 2, 3)]
    assert [r["status"] for r in records] == ["failure", "failure", "success"]
    assert [r["nextRetrySeconds"] for r in records[:2]] == [60, 180]
    assert [r["errorCategory"] for r in records[:2]] == ["official-data-missing", "official-service-error"]
    assert all((tmp_path / f'rules-page-attempt-{n}.html').read_text() == pages[n-1] for n in (1,2,3))


def test_exhaustion_reports_failure_in_actions_and_never_claims_unchanged(tmp_path, monkeypatch):
    summary = tmp_path / 'summary.md'; monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    sleeps = []; monkeypatch.setattr(update.time, 'sleep', sleeps.append)
    def fail(attempt, evidence): return update.parse_rules_page(page('service-error'))
    with pytest.raises((ValueError, RuntimeError)):
        update._retry_official_request(fail, stage='rules-page', diagnostics_dir=tmp_path/'diagnostics')
    assert sleeps == [60, 180]
    record = json.loads((tmp_path/'diagnostics/rules-page-attempt-3.json').read_text())
    assert record['status'] == 'failure' and record['nextRetrySeconds'] is None
    assert record['upstreamStatusCode'] == 500 and 'changed' not in record
    text = summary.read_text()
    assert '官方检查未完成' in text and '未判定为无变化' in text and '500' in text and '已尝试 3 次' in text


@pytest.mark.parametrize('stage', ['rules-page', 'official-pdf'])
def test_network_failure_is_separate_from_content_validation(stage, tmp_path, monkeypatch):
    monkeypatch.setattr(update.time, 'sleep', lambda _: None)
    error = urllib.error.HTTPError('https://wpn.wizards.com/en/rules-documents', 503, 'Unavailable', {}, None)
    def fail(*args):
        if PACKAGE == 'mtr_pipeline': raise update.OfficialUpdateError('download failed') from error
        raise error
    with pytest.raises(Exception):
        update._retry_official_request(fail, stage=stage, diagnostics_dir=tmp_path)
    record = json.loads((tmp_path/f'{stage}-attempt-3.json').read_text())
    assert record['errorCategory'] == 'download-error' and record['upstreamStatusCode'] == 503


def test_unexpected_program_error_is_recorded_and_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(update.time, 'sleep', lambda _: pytest.fail('program errors must not be retried'))
    def fail(*args): raise TypeError('injected programming bug')
    with pytest.raises(TypeError, match='programming bug'):
        update._retry_official_request(fail, stage='rules-page', diagnostics_dir=tmp_path)
    records = list(tmp_path.glob('*.json')); assert len(records) == 1
    assert json.loads(records[0].read_text())['errorCategory'] == 'internal-error'


def test_pdf_validation_remains_blocking_and_summary_treats_details_as_text(tmp_path, monkeypatch):
    summary = tmp_path / 'summary.md'; monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr(update.time, 'sleep', lambda _: None)
    def fail(*args): raise ValueError('<script>bad PDF</script>')
    with pytest.raises(ValueError):
        update._retry_official_request(fail, stage='official-pdf', diagnostics_dir=tmp_path/'diagnostics')
    record = json.loads((tmp_path/'diagnostics/official-pdf-attempt-3.json').read_text())
    assert record['errorCategory'] == 'pdf-validation-error'
    assert '<script>' not in summary.read_text() and '&lt;script&gt;' in summary.read_text()
