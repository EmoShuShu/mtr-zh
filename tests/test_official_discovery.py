import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from mtr_pipeline import official_discovery as update
PDF_URL = "https://media.wizards.com/test-only.pdf"
def _page(date="Sep 24, 2024"):
    return f'<div class="downloadableDocument"><div class="titleText">{update.EXPECTED_TITLE}</div><div class="updated">{date}</div><a href="{PDF_URL}">PDF</a></div>'

def _nuxt_page(fault=None):
    # Compact synthetic copy of the observed Nuxt reference-pool shape; no WPN content.
    pool = [["ShallowReactive", 1], {"path": 2, "data": 3}, "/en/rules-documents",
            ["ShallowReactive", 4], {"downloadableDocuments": 5}, {"entries": 6}, [7],
            {"fields": 8, "sys": 14}, {"title": 9, "updated": 10, "cta": 11}, update.EXPECTED_TITLE,
            "2024-09-24", {"fields": 12}, {"link": 13}, PDF_URL,
            {"locale": 15, "contentType": 16}, "en", {"sys": 17}, {"id": 18}, "downloadableDocument"]
    if fault == "duplicate": pool[6] *= 2
    if fault == "missing": pool[6] = []
    if fault == "date": pool[10] = "2024-02-30"
    if fault == "url": pool[13] = "https://evil.example/a.pdf"
    if fault == "type": pool[18] = "unknown"
    if fault == "locale": pool[15] = "zh-hans"
    if fault == "reference": pool[8]["cta"] = 99999
    if fault == "collection": pool[4] = {"unknown": 5}
    if fault == "wrapper": pool[0] = ["ShallowReactive", 0]
    text = json.dumps(pool)
    if fault == "duplicate-field": text = text.replace('"entries": 6', '"entries": 6, "entries": 6')
    return f'<script type="application/json" id="__NUXT_DATA__">{text}</script>'


def test_complete_embedded_collection_finds_mtr_outside_visible_first_page():
    visible = _page().replace(update.EXPECTED_TITLE, "TEST ONLY other document")
    result = update.parse_rules_page(visible + _nuxt_page())
    assert result["pageUpdatedDate"] == "2024-09-24" and result["url"] == PDF_URL
    assert "effectiveDate" not in result


@pytest.mark.parametrize("fault", ["duplicate", "missing", "date", "url", "type", "locale", "reference", "collection", "wrapper", "duplicate-field"])
def test_embedded_page_anomalies_stop_without_falling_back_to_visible_card(fault):
    with pytest.raises(ValueError): update.parse_rules_page(_page("Sep 24, 2024") + _nuxt_page(fault))


def test_embedded_and_visible_card_must_agree():
    with pytest.raises(ValueError, match="不一致"):
        update.parse_rules_page(_page("Sep 23, 2024") + _nuxt_page())



def test_pdf_effective_date_is_read_from_pdf_not_web_page(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: "Title\nEffective February 27, 2026\nBody")]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(update.pdfplumber, "open", lambda stream: PDF())
    assert update.pdf_effective_date(b"synthetic") == "2026-02-27"
    from mtr_pipeline import official_update
    def download(url, destination, **kwargs): destination.write_bytes(b"%PDF-test")
    monkeypatch.setattr(official_update, "_download", download)
    import tempfile
    with tempfile.TemporaryDirectory() as name:
        release = official_update.OfficialRelease(update.RULES_PAGE, update.EXPECTED_TITLE, "2026-03-01", PDF_URL)
        result = official_update.download_official_pdf(release, Path(name) / "test.pdf")
    assert result.effective_date == "2026-02-27" and result.page_updated_date == "2026-03-01"

def test_reject_redirect_before_following_untrusted_url():
    import urllib.request
    handler = update.SafeRedirect("pdf")
    with pytest.raises(ValueError):
        handler.redirect_request(urllib.request.Request(PDF_URL), None, 302, "", {}, "https://example.com/a.pdf")
    assert handler.hops == []
