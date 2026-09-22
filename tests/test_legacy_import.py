from __future__ import annotations

import mtr_pipeline.legacy_migrate as legacy_migrate

from mtr_pipeline.legacy_import import (
    LegacyBlock,
    LegacySection,
    SourceSpan,
    pair_section,
    split_sections,
    tokenize_legacy_markdown,
    _is_document_footer,
)
from mtr_pipeline.legacy_migrate import _ensure_list, _partition_official, _should_not_inherit


def test_heading_is_split_without_blank_line():
    blocks = tokenize_legacy_markdown(
        "# MTR 1. Test\n## MTR 1.1 English 中文\nEnglish body.\n\n中文正文。\n"
    )
    assert [(block.kind, block.span.start, block.span.end) for block in blocks] == [
        ("heading", 1, 1),
        ("heading", 2, 2),
        ("paragraph", 3, 3),
        ("paragraph", 5, 5),
    ]
    sections, issues = split_sections(blocks)
    assert not issues
    assert [section.key for section in sections] == ["chapter-01", "1.1"]


def test_unmarked_quote_continuation_stays_in_annotation():
    section = LegacySection(
        key="1.1",
        heading=LegacyBlock("heading", "## MTR 1.1 English 中文", SourceSpan(1, 1)),
        blocks=tokenize_legacy_markdown(
            "English body.\n\n中文正文。\n\n>English note first line\nEnglish note continuation\n>\n>中文注解。\n"
        ),
    )
    pairs, issues = pair_section(section)
    assert not [issue for issue in issues if issue.severity == "error"]
    assert pairs[0].annotation_en == "English note first line\nEnglish note continuation"
    assert pairs[0].annotation_zh == "中文注解。"


def test_heading_after_empty_list_item_is_not_swallowed():
    blocks = tokenize_legacy_markdown("* \n# 附录B～时间限制\n正文\n")
    assert blocks[0].kind == "list"
    assert blocks[0].span == SourceSpan(1, 1)
    assert blocks[1].kind == "heading"
    assert blocks[1].span == SourceSpan(2, 2)


def test_legal_footer_detection_tracks_the_actual_last_page():
    assert _is_document_footer(56, 56, 9.0, 528.7)
    assert not _is_document_footer(55, 56, 9.0, 528.7)
    assert not _is_document_footer(56, 56, 11.0, 528.7)
    assert not _is_document_footer(56, 56, 9.0, 400.0)


def test_inline_bilingual_list_is_split():
    section = LegacySection(
        key="1.3",
        heading=LegacyBlock("heading", "## MTR 1.3 Roles 职责", SourceSpan(1, 1)),
        blocks=[LegacyBlock("list", "* Tournament Organizer 比赛主办人", SourceSpan(2, 2))],
    )
    pairs, issues = pair_section(section)
    assert not issues
    assert pairs[0].en == "Tournament Organizer"
    assert pairs[0].zh == "比赛主办人"


def test_list_marker_with_tab_is_canonicalized_without_duplication():
    assert _ensure_list("*\t中文列表项") == "* 中文列表项"
    assert _ensure_list("* English item") == "* English item"
    assert _ensure_list("中文列表项") == "* 中文列表项"


def test_manual_split_must_reconstruct_official_text_exactly():
    official = "First sentence. Second sentence."
    parts, delimiters = _partition_official(
        official,
        ["First sentence.", "Second sentence."],
        ["First sentence.", "Second sentence."],
    )
    assert parts == ["First sentence.", "Second sentence."]
    assert delimiters == [" "]


def test_explicit_no_inheritance_rule_requires_section_and_prefix():
    overrides = {
        "bodyNoInheritance": [
            {"section": "3.7", "officialStartsWith": "* Secrets of Strixhaven"}
        ]
    }
    assert _should_not_inherit(overrides, "3.7", "* Secrets of Strixhaven April 17, 2026")
    assert not _should_not_inherit(overrides, "6.3", "* Secrets of Strixhaven")


def test_chinese_translation_with_card_name_slashes_stays_with_pending_english():
    section = LegacySection(
        key="7.2",
        heading=LegacyBlock("heading", "## MTR 7.2 Card Use 限制赛中可用的牌", SourceSpan(1, 1)),
        blocks=tokenize_legacy_markdown(
            "* Players may add Plains, Island, Swamp, Mountain, or Forest.\n"
            "* 牌手可以加入平原/Plains、海岛/Island、沼泽/Swamp、山脉/Mountain或树林/Forest。\n"
        ),
    )
    pairs, issues = pair_section(section)
    assert not issues
    assert len(pairs) == 1
    assert pairs[0].en.startswith("Players may add")
    assert pairs[0].zh.startswith("牌手可以加入平原/Plains")


def test_slash_bilingual_card_does_not_consume_unrelated_pending_english():
    section = LegacySection(
        key="6.4",
        heading=LegacyBlock("heading", "## MTR 6.4 Modern 近代", SourceSpan(1, 1)),
        blocks=tokenize_legacy_markdown(
            "* Relic of Progenitus\n"
            "* 得享安息/Rest in Peace\n"
        ),
    )
    pairs, issues = pair_section(section)
    assert len(pairs) == 2
    assert pairs[0].en == "Relic of Progenitus"
    assert pairs[0].zh == ""
    assert pairs[1].en == "Rest in Peace"
    assert pairs[1].zh == "得享安息/Rest in Peace"
    assert [issue.code for issue in issues] == ["unpaired-english"]


def test_mixed_bilingual_table_is_split_cell_by_cell():
    section = LegacySection(
        key="appendix-f",
        heading=LegacyBlock("heading", "# Appendix F 附录F", SourceSpan(1, 1)),
        blocks=tokenize_legacy_markdown(
            "|Program赛事|Rules Enforcement Level执法严格度|\n"
            "|---|---|\n"
            "|Friday Night Magic周五认证赛|Regular一般|\n"
        ),
    )
    pairs, issues = pair_section(section)
    assert not issues
    assert len(pairs) == 1
    assert "|Program|Rules Enforcement Level|" in pairs[0].en
    assert "|赛事|执法严格度|" in pairs[0].zh
    assert "|Friday Night Magic|Regular|" in pairs[0].en
    assert "|周五认证赛|一般|" in pairs[0].zh


def test_fail_on_errors_returns_nonzero_after_draft_is_written(monkeypatch):
    monkeypatch.setattr(
        legacy_migrate,
        "migrate",
        lambda *args, **kwargs: {
            "fileCount": 18,
            "summary": {"error": 1, "warning": 0, "info": 0},
        },
    )
    result = legacy_migrate.main(
        [
            "AMTR_2025.md",
            "--official-pdf",
            "official.pdf",
            "--overrides",
            "migration.yaml",
            "--output-dir",
            "output",
            "--fail-on-errors",
        ]
    )
    assert result == 2
