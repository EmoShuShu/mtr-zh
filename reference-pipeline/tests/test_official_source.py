from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mtr_reference.official_source import (
    EXPECTED_TITLE,
    OfficialSourceError,
    check_for_update,
    parse_rules_page,
)


def _page(title: str, date: str, href: str) -> str:
    return f"""
    <div class="downloadableDocument item">
      <div class="titleText">{title}</div>
      <div class="updated">{date}</div>
      <a href="{href}">Download</a>
    </div>
    """


def test_rules_page_parser_selects_the_official_document():
    release = parse_rules_page(
        _page("Deck Checklist", "Feb 1, 2026", "https://media.wizards.com/deck.pdf")
        + _page(
            EXPECTED_TITLE,
            "Feb 27, 2026",
            "https://media.wizards.com/ContentResources/WPN/MTR.pdf",
        )
    )
    assert release.effective_date == "2026-02-27"
    assert release.pdf_url.endswith("/MTR.pdf")


def test_rules_page_parser_rejects_an_untrusted_pdf_host():
    with pytest.raises(OfficialSourceError, match="Unexpected official PDF URL"):
        parse_rules_page(_page(EXPECTED_TITLE, "Feb 27, 2026", "https://example.test/MTR.pdf"))


def test_state_cannot_reference_json_outside_its_directory(tmp_path: Path, monkeypatch):
    state = {
        "schemaVersion": 1,
        "pdfSha256": "0" * 64,
        "officialJson": "../outside.json",
    }
    state_path = tmp_path / "state/official-source.yaml"
    state_path.parent.mkdir()
    state_path.write_text(yaml.safe_dump(state), encoding="utf-8")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "mtr_reference.official_source.discover_official_release",
        lambda: type(
            "Release",
            (),
            {
                "effective_date": "2026-02-27",
                "page_url": "https://wpn.wizards.com/en/rules-documents",
                "pdf_url": "https://media.wizards.com/MTR.pdf",
            },
        )(),
    )
    monkeypatch.setattr(
        "mtr_reference.official_source._download",
        lambda *_args, **_kwargs: b"%PDF-" + b"test",
    )
    monkeypatch.setattr(
        "mtr_reference.official_source.parse_official_pdf",
        lambda _path: type(
            "Document",
            (),
            {
                "as_dict": lambda self: {
                    "formatVersion": 1,
                    "officialPdfSha256": "a" * 64,
                    "sectionCount": 0,
                    "unitCount": 0,
                    "sections": [],
                },
                "source_sha256": "a" * 64,
                "sections": [],
            },
        )(),
    )
    with pytest.raises(OfficialSourceError, match="escapes"):
        check_for_update(state_path, tmp_path / "candidate")
