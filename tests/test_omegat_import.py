from __future__ import annotations

import difflib
import json
from pathlib import Path
import shutil
import sys

import pytest

from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.omegat import TranslationUnit, export_pilot, iter_translation_units
from mtr_pipeline.omegat_import import (
    PoEntry,
    TranslationChange,
    apply_changes_to_source,
    flatten_po_collection,
    main,
    parse_po,
    parse_po_collection,
    patch_yaml_translations,
    plan_import,
    validate_po_collections,
    validate_po_pair,
    write_candidate,
    write_reports,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "mtr" / "2026-02-27" / "manifest.yaml"
SCHEMA = ROOT / "schema" / "mtr-source.schema.json"
GLOSSARY = ROOT / "terminology" / "mtr-glossary.txt"


@pytest.fixture(scope="module")
def project():
    return load_project(MANIFEST, SCHEMA, ROOT)


def _intro_translation(project, suffix: str) -> tuple[str, str]:
    current = next(
        unit.zh for unit in iter_translation_units(project)
        if unit.unit_id == "mtr-introduction"
    )
    return current, current + suffix


def _replace_po_translation(path: Path, before: str, after: str) -> None:
    old = f"msgstr {json.dumps(before, ensure_ascii=False)}"
    new = f"msgstr {json.dumps(after, ensure_ascii=False)}"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _assert_only_title_changed(before: str, after: str, expected: str) -> None:
    changed_lines = [
        line
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith(("- ", "+ "))
    ]
    assert len(changed_lines) == 2
    assert changed_lines[0].startswith("-   zh:")
    assert changed_lines[1] == f"+   zh: {json.dumps(expected, ensure_ascii=False)}"


def test_parse_po_supports_omegat_continuation_lines(tmp_path):
    po_path = tmp_path / "translated.po"
    po_path.write_text(
        '#. status: legacy-unreviewed\n'
        'msgctxt "mtr-example"\n'
        'msgid "English\\nsource"\n'
        'msgstr "中文第一行\\n"\n'
        '"中文第二行"\n',
        encoding="utf-8",
    )

    assert parse_po(po_path) == [
        PoEntry("mtr-example", "English\nsource", "中文第一行\n中文第二行")
    ]


def test_plan_import_reports_only_changed_targets(project, tmp_path):
    export_pilot(project, tmp_path, GLOSSARY)
    po_path = tmp_path / "source" / "mtr-zh-pilot.po"
    before, after = _intro_translation(project, "（测试修订）")
    _replace_po_translation(po_path, before, after)

    changes = plan_import(parse_po(po_path), list(iter_translation_units(project)))

    assert len(changes) == 1
    assert changes[0].unit_id == "mtr-introduction"
    assert changes[0].before == before
    assert changes[0].after == after


def test_plan_import_rejects_unknown_ids_and_changed_english(project):
    units = list(iter_translation_units(project))
    with pytest.raises(PipelineError, match="Unknown PO unit ID"):
        plan_import([PoEntry("unknown", "English", "中文")], units)
    with pytest.raises(PipelineError, match="English source differs"):
        plan_import([PoEntry("mtr-introduction", "Changed", "引言")], units)


def test_validate_po_pair_rejects_missing_entries_and_source_changes():
    reference = [
        PoEntry("first", "First", "旧译一"),
        PoEntry("second", "Second", "旧译二"),
    ]
    with pytest.raises(PipelineError, match="missing unit ID"):
        validate_po_pair(reference, [reference[0]])
    with pytest.raises(PipelineError, match="changed English source"):
        validate_po_pair(
            reference,
            [reference[0], PoEntry("second", "Changed", "新译二")],
        )


def test_po_collections_require_matching_files_and_unique_ids(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    for directory in (source_dir, target_dir):
        (directory / "one.po").write_text(
            'msgctxt "one"\nmsgid "One"\nmsgstr "一"\n', encoding="utf-8"
        )
    reference = parse_po_collection(source_dir)
    translated = parse_po_collection(target_dir)
    validate_po_collections(reference, translated)
    assert [entry.unit_id for entry in flatten_po_collection(translated)] == ["one"]

    (target_dir / "extra.po").write_text(
        'msgctxt "two"\nmsgid "Two"\nmsgstr "二"\n', encoding="utf-8"
    )
    with pytest.raises(PipelineError, match="unexpected file"):
        validate_po_collections(reference, parse_po_collection(target_dir))


def test_write_reports_are_preview_only(tmp_path):
    change = plan_import(
        [PoEntry("example", "English", "新译")],
        [TranslationUnit("example", "English", "旧译", "paragraph", "source.yaml")],
    )[0]
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    write_reports([change], json_path, markdown_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["status"] == "preview-only"
    assert payload["changeCount"] == 1
    assert "尚未修改正式 YAML" in markdown_path.read_text(encoding="utf-8")


def test_write_candidate_updates_isolated_tree_only(project, tmp_path):
    units = list(iter_translation_units(project))
    _before, after = _intro_translation(project, "（候选测试）")
    changes = plan_import(
        [PoEntry("mtr-introduction", "Introduction", after)],
        units,
    )
    original_path = MANIFEST.parent / "introduction.yaml"
    original_text = original_path.read_text(encoding="utf-8")
    candidate_dir = tmp_path / "candidate"

    candidate_manifest = write_candidate(project, changes, candidate_dir, SCHEMA)
    candidate_project = load_project(candidate_manifest, SCHEMA, ROOT)
    candidate_units = {unit.unit_id: unit for unit in iter_translation_units(candidate_project)}

    assert candidate_units["mtr-introduction"].zh == after
    assert original_path.read_text(encoding="utf-8") == original_text
    candidate_text = (candidate_dir / "introduction.yaml").read_text(encoding="utf-8")
    _assert_only_title_changed(original_text, candidate_text, after)
    with pytest.raises(PipelineError, match="already exists"):
        write_candidate(project, changes, candidate_dir, SCHEMA)


def test_patch_yaml_translations_preserves_surrounding_text_and_block_style():
    original = """chapter:
  id: example
  en: Example
  zh: 标题
  groups:
  - id: example-g001
    type: paragraph
    blocks:
    - id: example-b001
      en: English
      zh: |-
        旧译第一行

        旧译第二行
"""
    changes = [
        TranslationChange(
            "example-b001",
            "paragraph",
            "example.yaml",
            "English",
            "旧译第一行\n\n旧译第二行",
            "新译第一行\n\n新译第二行",
        )
    ]

    patched = patch_yaml_translations(original, changes)

    assert patched == original.replace("旧译第一行", "新译第一行").replace(
        "旧译第二行", "新译第二行"
    )
    assert "\n        \n" not in patched


def test_apply_changes_to_source_validates_and_updates_only_changed_file(project, tmp_path):
    source_copy = tmp_path / "mtr-source"
    shutil.copytree(MANIFEST.parent, source_copy)
    copied_manifest = source_copy / MANIFEST.name
    copied_project = load_project(copied_manifest, SCHEMA, ROOT)
    copied_units = list(iter_translation_units(copied_project))
    _before, after = _intro_translation(copied_project, "（正式回写测试）")
    changes = plan_import(
        [PoEntry("mtr-introduction", "Introduction", after)],
        copied_units,
    )
    introduction = source_copy / "introduction.yaml"
    untouched = source_copy / "chapter-01.yaml"
    introduction_before = introduction.read_text(encoding="utf-8")
    untouched_before = untouched.read_text(encoding="utf-8")

    apply_changes_to_source(copied_project, changes, SCHEMA)

    applied = load_project(copied_manifest, SCHEMA, ROOT)
    applied_units = {unit.unit_id: unit for unit in iter_translation_units(applied)}
    assert applied_units["mtr-introduction"].zh == after
    _assert_only_title_changed(
        introduction_before, introduction.read_text(encoding="utf-8"), after
    )
    assert untouched.read_text(encoding="utf-8") == untouched_before


def test_apply_cli_requires_exact_fresh_change_count(project, tmp_path, monkeypatch):
    source_copy = tmp_path / "mtr-source"
    shutil.copytree(MANIFEST.parent, source_copy)
    copied_manifest = source_copy / MANIFEST.name
    copied_project = load_project(copied_manifest, SCHEMA, ROOT)
    omegat_project = tmp_path / "omegat"
    export_pilot(copied_project, omegat_project, GLOSSARY)
    shutil.copytree(
        omegat_project / "source", omegat_project / "target", dirs_exist_ok=True
    )
    target = omegat_project / "target" / "mtr-zh-pilot.po"
    before, after = _intro_translation(copied_project, "（命令行回写测试）")
    _replace_po_translation(target, before, after)

    arguments = [
        "import_omegat.py",
        "--manifest",
        str(copied_manifest),
        "--schema",
        str(SCHEMA),
        "--project-root",
        str(ROOT),
        "--source-po",
        str(omegat_project / "source"),
        "--po",
        str(omegat_project / "target"),
        "--json-report",
        str(tmp_path / "preview.json"),
        "--markdown-report",
        str(tmp_path / "preview.md"),
        "--apply",
        "--expected-change-count",
        "2",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    assert main() == 1
    assert after not in (
        source_copy / "introduction.yaml"
    ).read_text(encoding="utf-8")

    arguments[-1] = "1"
    assert main() == 0
    applied = load_project(copied_manifest, SCHEMA, ROOT)
    applied_units = {unit.unit_id: unit for unit in iter_translation_units(applied)}
    assert applied_units["mtr-introduction"].zh == after
