from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.omegat import TranslationUnit, export_pilot, iter_translation_units
from mtr_pipeline.omegat_import import PoEntry, parse_po_collection
from mtr_pipeline.review_status import (
    calculate_status,
    empty_ledger,
    mark_reviewed,
    parse_omegat_notes,
    select_review_entries,
    write_status_reports,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "mtr" / "2026-02-27" / "manifest.yaml"
SCHEMA = ROOT / "schema" / "mtr-source.schema.json"
GLOSSARY = ROOT / "terminology" / "mtr-glossary.txt"


@pytest.fixture(scope="module")
def project():
    return load_project(MANIFEST, SCHEMA, ROOT)


def _pilot_collections(project, tmp_path):
    omegat_project = tmp_path / "omegat"
    export_pilot(project, omegat_project, GLOSSARY)
    shutil.copytree(
        omegat_project / "source", omegat_project / "target", dirs_exist_ok=True
    )
    return (
        parse_po_collection(omegat_project / "source"),
        parse_po_collection(omegat_project / "target"),
    )


def _replace_intro_translation(path: Path, project, suffix: str) -> None:
    before = next(
        unit.zh for unit in iter_translation_units(project)
        if unit.unit_id == "mtr-introduction"
    )
    old = f"msgstr {json.dumps(before, ensure_ascii=False)}"
    new = f"msgstr {json.dumps(before + suffix, ensure_ascii=False)}"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_mark_reviewed_records_explicit_unchanged_batch(project, tmp_path):
    reference, translated = _pilot_collections(project, tmp_path)
    ledger = empty_ledger("20260227")

    reviewed = mark_reviewed(
        project,
        ledger,
        reference,
        translated,
        files=["mtr-zh-pilot.po"],
        id_prefixes=[],
        unit_ids=[],
        notes={"mtr-introduction": "修订理由"},
    )

    assert len(reviewed) == 28
    assert set(ledger["units"]) == set(reviewed)
    assert {record["outcome"] for record in ledger["units"].values()} == {
        "unchanged"
    }
    assert ledger["units"]["mtr-introduction"]["note"] == "修订理由"
    statuses = calculate_status(list(iter_translation_units(project)), ledger)
    assert sum(item.status == "reviewed-unchanged" for item in statuses) == 28
    assert sum(item.status == "unreviewed" for item in statuses) == 1453 - 28


def test_parse_omegat_notes_maps_shared_translations_and_rejects_unknown(tmp_path):
    translated = {
        "one.po": [
            PoEntry("one", "Repeated source", "相同译文"),
            PoEntry("two", "Repeated source", "相同译文"),
        ]
    }
    tmx = tmp_path / "project_save.tmx"
    tmx.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.1"><body><tu>
<note>共同的修订理由</note>
<tuv lang="en-US"><seg>Repeated source</seg></tuv>
<tuv lang="zh-CN"><seg>相同译文</seg></tuv>
</tu></body></tmx>
""",
        encoding="utf-8",
    )

    assert parse_omegat_notes(tmx, translated) == {
        "one": "共同的修订理由",
        "two": "共同的修订理由",
    }

    tmx.write_text(
        tmx.read_text(encoding="utf-8").replace("Repeated source", "Unknown source"),
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="does not match"):
        parse_omegat_notes(tmx, translated)


def test_review_marking_requires_selector_and_applied_target(project, tmp_path):
    reference, translated = _pilot_collections(project, tmp_path)
    with pytest.raises(PipelineError, match="requires --file"):
        select_review_entries(translated, files=[], id_prefixes=[], unit_ids=[])

    translated_path = tmp_path / "omegat" / "target" / "mtr-zh-pilot.po"
    _replace_intro_translation(translated_path, project, "（尚未回写）")
    translated = parse_po_collection(tmp_path / "omegat" / "target")
    with pytest.raises(PipelineError, match="has not been applied"):
        mark_reviewed(
            project,
            empty_ledger("20260227"),
            reference,
            translated,
            files=[],
            id_prefixes=[],
            unit_ids=["mtr-introduction"],
        )


def test_changed_reviewed_unit_becomes_stale_and_reports_are_written(project, tmp_path):
    reference, translated = _pilot_collections(project, tmp_path)
    ledger = empty_ledger("20260227")
    mark_reviewed(
        project,
        ledger,
        reference,
        translated,
        files=[],
        id_prefixes=[],
        unit_ids=["mtr-introduction"],
    )
    units = list(iter_translation_units(project))
    first = next(unit for unit in units if unit.unit_id == "mtr-introduction")
    changed = TranslationUnit(
        first.unit_id,
        first.en,
        first.zh + "（后来又改）",
        first.kind,
        first.source_file,
    )
    changed_units = [changed if unit.unit_id == changed.unit_id else unit for unit in units]

    statuses = calculate_status(changed_units, ledger)
    json_report = tmp_path / "status.json"
    markdown_report = tmp_path / "status.md"
    write_status_reports(statuses, json_report, markdown_report)

    introduction_status = next(
        item.status for item in statuses if item.unit_id == "mtr-introduction"
    )
    assert introduction_status == "stale"
    assert '"stale": 1' in json_report.read_text(encoding="utf-8")
    assert "| 文件 | 未审 | 已审未改 | 已审有改 | 失效 |" in markdown_report.read_text(
        encoding="utf-8"
    )
