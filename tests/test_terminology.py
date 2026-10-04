from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtr_pipeline.core import PipelineError
from mtr_pipeline.omegat import TranslationUnit
from mtr_pipeline.terminology import (
    GlossaryEntry,
    audit_terminology,
    parse_glossary,
    write_audit_reports,
)


ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = ROOT / "terminology" / "mtr-glossary.txt"


def test_repository_glossary_parses_entries():
    entries = parse_glossary(GLOSSARY)

    assert entries


def test_glossary_rejects_duplicate_sources(tmp_path):
    path = tmp_path / "glossary.txt"
    path.write_text("Example Term\t首选译法\nexample term\t另一译法\n", encoding="utf-8")

    with pytest.raises(PipelineError, match="Duplicate glossary source term"):
        parse_glossary(path)


def test_audit_reports_missing_preferred_and_forbidden_translation():
    units = [
        TranslationUnit("good", "The Example Term begins.", "首选译法开始。", "paragraph", "one.yaml"),
        TranslationUnit("missing", "The Example Term ends.", "其他译法结束。", "paragraph", "two.yaml"),
        TranslationUnit("forbidden", "Another event.", "弃用译法开始。", "paragraph", "three.yaml"),
    ]
    entries = [
        GlossaryEntry("Example Term", "首选译法", "禁用译法：弃用译法", ("弃用译法",))
    ]

    findings = audit_terminology(units, entries)

    assert [(item.code, item.unit_id) for item in findings] == [
        ("preferred-translation-missing", "missing"),
        ("forbidden-translation", "forbidden"),
    ]


def test_write_audit_reports(tmp_path):
    entries = [GlossaryEntry("Example Term", "首选译法", "", ())]
    findings = audit_terminology(
        [
            TranslationUnit(
                "missing", "Example Term", "其他译法", "paragraph", "one.yaml"
            )
        ],
        entries,
    )
    json_path = tmp_path / "audit.json"
    markdown_path = tmp_path / "audit.md"

    write_audit_reports(entries, findings, json_path, markdown_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["warningCount"] == 1
    assert payload["errorCount"] == 0
    assert "preferred-translation-missing" in json_path.read_text(encoding="utf-8")
    assert "MTR 术语审计" in markdown_path.read_text(encoding="utf-8")
