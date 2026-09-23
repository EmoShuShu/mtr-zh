from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mtr_pipeline.legacy_import import OfficialSection, OfficialUnit, sha256_file
from mtr_pipeline.official_update import (
    EXPECTED_TITLE,
    OfficialRelease,
    OfficialUpdateError,
    UpdateFinding,
    _exact_text_changes,
    _rebase_groups,
    _render_exact_text_changes,
    _write_update_report,
    load_source_state,
    parse_rules_page,
)


ROOT = Path(__file__).resolve().parents[1]


def _page(*documents: tuple[str, str, str]) -> str:
    return "".join(
        f"""
        <div class="downloadableDocument item">
          <div><div class="titleText">{title}</div></div>
          <div class="updated">{date}</div>
          <div><a href="{href}">Download</a></div>
        </div>
        """
        for title, date, href in documents
    )


def test_rules_page_parser_selects_exact_official_document():
    release = parse_rules_page(
        _page(
            ("Deck Checklist", "Mar 1, 2026", "https://media.wizards.com/checklist.pdf"),
            (
                EXPECTED_TITLE,
                "Feb 27, 2026",
                "https://media.wizards.com/ContentResources/WPN/MTG_MTR_2026_Feb27_EN.pdf",
            ),
        )
    )
    assert release.effective_date == "2026-02-27"
    assert release.pdf_url.endswith("MTG_MTR_2026_Feb27_EN.pdf")


def test_rules_page_parser_rejects_ambiguous_entries():
    document = (
        EXPECTED_TITLE,
        "Feb 27, 2026",
        "https://media.wizards.com/ContentResources/WPN/MTG_MTR_2026_Feb27_EN.pdf",
    )
    with pytest.raises(OfficialUpdateError, match="exactly one"):
        parse_rules_page(_page(document, document))


def test_rules_page_parser_rejects_non_wizards_pdf_host():
    with pytest.raises(OfficialUpdateError, match="Unexpected official PDF URL"):
        parse_rules_page(_page((EXPECTED_TITLE, "Feb 27, 2026", "https://example.test/mtr.pdf")))


def test_rules_page_parser_rejects_nonstandard_official_port():
    with pytest.raises(OfficialUpdateError, match="Unexpected official PDF URL"):
        parse_rules_page(
            _page((EXPECTED_TITLE, "Feb 27, 2026", "https://media.wizards.com:444/mtr.pdf"))
        )


def test_source_state_matches_current_version_pointer():
    state = load_source_state(ROOT / "src/mtr/official-source.yaml")
    version = (ROOT / "src/mtr/current-version.txt").read_text(encoding="utf-8").strip()
    assert state.effective_date == version
    snapshot = ROOT / "snapshots" / version / state.pdf_sha256[:12] / "official/MTR_EN.pdf"
    assert snapshot.is_file()
    assert sha256_file(snapshot) == state.pdf_sha256


def test_rebase_preserves_ids_chinese_annotations_and_split_boundaries():
    groups = [
        {
            "id": "mtr-1.1-g001",
            "type": "paragraph",
            "blocks": [
                {
                    "id": "mtr-1.1-b001",
                    "en": "First sentence.",
                    "zh": "第一句。",
                    "extras": [{"en": "Note", "zh": "注解"}],
                    "joinAfter": {"en": " ", "zh": ""},
                },
                {"id": "mtr-1.1-b002", "en": "Second sentence.", "zh": "第二句。"},
            ],
        }
    ]
    official = OfficialSection(
        "1.1",
        "Test",
        [OfficialUnit("paragraph", "First revised sentence. Second sentence.", (1,))],
    )
    findings: list[UpdateFinding] = []
    result = _rebase_groups(groups, official, "mtr-1.1", findings)
    assert result[0]["id"] == "mtr-1.1-g001"
    assert [block["id"] for block in result[0]["blocks"]] == [
        "mtr-1.1-b001",
        "mtr-1.1-b002",
    ]
    assert [block["zh"] for block in result[0]["blocks"]] == ["第一句。", "第二句。"]
    assert result[0]["blocks"][0]["extras"] == [{"en": "Note", "zh": "注解"}]
    assert findings[0].code == "official-text-changed"
    assert "similarity" not in findings[0].message


def test_exact_diff_reports_concrete_insertions_and_deletions():
    changes = _exact_text_changes(
        "A player draws one card.",
        "A player immediately draws two cards.",
    )
    assert "".join(change["text"] for change in changes if change["op"] != "insert") == (
        "A player draws one card."
    )
    assert "".join(change["text"] for change in changes if change["op"] != "delete") == (
        "A player immediately draws two cards."
    )
    rendered = _render_exact_text_changes(changes)
    assert "[-one-]" in rendered
    assert "{+immediately +}" in rendered
    assert "{+two+}" in rendered


def test_rebase_reports_punctuation_only_official_change():
    groups = [
        {
            "id": "mtr-1.1-g001",
            "type": "paragraph",
            "blocks": [{"id": "mtr-1.1-b001", "en": "A player acts.", "zh": "牌手行动。"}],
        }
    ]
    official = OfficialSection(
        "1.1",
        "Test",
        [OfficialUnit("paragraph", "A player acts!", (1,))],
    )
    findings: list[UpdateFinding] = []
    _rebase_groups(groups, official, "mtr-1.1", findings)
    assert len(findings) == 1
    assert findings[0].old_en == "A player acts."
    assert findings[0].new_en == "A player acts!"


def test_update_report_contains_human_and_machine_readable_exact_diff(tmp_path: Path):
    report_path = tmp_path / "update.json"
    report = _write_update_report(
        report_path,
        "2026-01-01",
        OfficialRelease(
            "https://wpn.wizards.com/en/rules-documents",
            EXPECTED_TITLE,
            "2026-02-01",
            "https://media.wizards.com/mtr.pdf",
            "a" * 64,
        ),
        [
            UpdateFinding(
                "official-text-changed",
                "warning",
                "1.1",
                "Official English changed; retained Chinese requires exact review.",
                "mtr-1.1-g001",
                "Draw one card.",
                "Draw two cards.",
            )
        ],
    )
    operations = [change["op"] for change in report["findings"][0]["changes"]]
    assert operations.count("delete") == 2
    assert operations.count("insert") == 2
    markdown = report_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "Draw [-one-]{+two+} [-card-]{+cards+}." in markdown


def test_rebase_new_official_unit_gets_stable_new_id_and_empty_translation():
    groups = [
        {
            "id": "mtr-1.1-g003",
            "type": "paragraph",
            "blocks": [{"id": "mtr-1.1-b004", "en": "Existing.", "zh": "既有。"}],
        }
    ]
    official = OfficialSection(
        "1.1",
        "Test",
        [
            OfficialUnit("paragraph", "Existing.", (1,)),
            OfficialUnit("paragraph", "New official text.", (1,)),
        ],
    )
    findings: list[UpdateFinding] = []
    result = _rebase_groups(groups, official, "mtr-1.1", findings)
    assert result[1] == {
        "id": "mtr-1.1-g004",
        "type": "paragraph",
        "blocks": [{"id": "mtr-1.1-b005", "en": "New official text.", "zh": ""}],
    }
    assert any(finding.code == "new-official-content" for finding in findings)


def test_rebase_multiblock_table_matches_rows_instead_of_position():
    groups = [
        {
            "id": "mtr-appendix-f-g002",
            "type": "table",
            "blocks": [
                {
                    "id": "mtr-appendix-f-b002",
                    "en": "| Program | REL |\n| --- | --- |",
                    "zh": "| 比赛 | 级别 |\n| --- | --- |",
                },
                {
                    "id": "mtr-appendix-f-b003",
                    "en": "| Existing Event | Regular |",
                    "zh": "| 既有比赛 | 一般 |",
                },
            ],
        }
    ]
    official = OfficialSection(
        "appendix-f",
        "Rules Enforcement Levels",
        [
            OfficialUnit(
                "table",
                "| Program | REL |\n| --- | --- |\n| Existing Event | Competitive |\n| New Event | Regular |",
                (1,),
            )
        ],
    )
    findings: list[UpdateFinding] = []
    result = _rebase_groups(groups, official, "mtr-appendix-f", findings)
    blocks = result[0]["blocks"]
    assert blocks[1]["id"] == "mtr-appendix-f-b003"
    assert blocks[1]["zh"] == "| 既有比赛 | 一般 |"
    assert blocks[2]["id"] == "mtr-appendix-f-b004"
    assert blocks[2]["zh"] == ""
    assert any(finding.code == "new-table-row" for finding in findings)


def test_workflows_keep_detection_review_and_release_separate():
    update_workflow = (ROOT / ".github/workflows/official-update.yml").read_text(encoding="utf-8")
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "gh pr create" in update_workflow
    assert "--draft" in update_workflow
    assert 'cron: "0 6 * * *"' in update_workflow
    assert 'timezone: "Asia/Shanghai"' in update_workflow
    assert '--assignee "$REPOSITORY_OWNER"' in update_workflow
    assert "git push --set-upstream" in update_workflow
    assert "gh release create" not in update_workflow
    assert 'branches:\n      - master' in release_workflow
    assert "release-artifacts/rules.json" in release_workflow
    assert "gh release create" in release_workflow


def test_all_github_workflows_are_valid_yaml():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        assert yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
