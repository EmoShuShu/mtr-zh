from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.omegat import export_full, export_pilot, iter_translation_units
from mtr_pipeline.omegat_import import PoEntry, TranslationChange
from mtr_pipeline.review_workflow import (
    WorkflowConfig,
    changes_outside_selection,
    parse_file_selection,
    prepare_omegat_project,
    run_menu,
    run_workflow,
    show_review_status,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "mtr" / "2026-02-27" / "manifest.yaml"
SCHEMA = ROOT / "schema" / "mtr-source.schema.json"
GLOSSARY = ROOT / "terminology" / "mtr-glossary.txt"


def test_parse_file_selection_accepts_multiple_numbers_and_cancel():
    filenames = ["one.po", "two.po", "three.po"]

    assert parse_file_selection("3, 1，3", filenames) == ["three.po", "one.po"]
    assert parse_file_selection("a", filenames) == filenames
    assert parse_file_selection("q", filenames) == []
    with pytest.raises(PipelineError, match="无效编号"):
        parse_file_selection("one", filenames)
    with pytest.raises(PipelineError, match="超出范围"):
        parse_file_selection("4", filenames)


def test_changes_outside_selection_detects_unreviewed_file_edits():
    reference = {
        "one.po": [PoEntry("one", "One", "一")],
        "two.po": [PoEntry("two", "Two", "二")],
    }
    changes = [
        TranslationChange("one", "paragraph", "one.yaml", "One", "一", "壹"),
        TranslationChange("two", "paragraph", "two.yaml", "Two", "二", "贰"),
    ]

    outside = changes_outside_selection(changes, reference, ["one.po"])

    assert [change.unit_id for change in outside] == ["two"]


def _workflow_config(tmp_path: Path) -> tuple[WorkflowConfig, Path, str]:
    source_copy = tmp_path / "mtr-source"
    shutil.copytree(MANIFEST.parent, source_copy)
    copied_manifest = source_copy / MANIFEST.name
    copied_project = load_project(copied_manifest, SCHEMA, ROOT)
    omegat = tmp_path / "omegat"
    export_pilot(copied_project, omegat, GLOSSARY)
    shutil.copytree(omegat / "source", omegat / "target", dirs_exist_ok=True)
    target = omegat / "target" / "mtr-zh-pilot.po"
    before = next(
        unit.zh for unit in iter_translation_units(copied_project)
        if unit.unit_id == "mtr-introduction"
    )
    after = before + "（向导测试）"
    old = f"msgstr {json.dumps(before, ensure_ascii=False)}"
    new = f"msgstr {json.dumps(after, ensure_ascii=False)}"
    target_text = target.read_text(encoding="utf-8")
    assert old in target_text
    target.write_text(target_text.replace(old, new, 1), encoding="utf-8")
    (omegat / "omegat" / "project_save.tmx").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.1"><body><tu>
<note>向导测试批注</note>
<tuv lang="en-US"><seg>Introduction</seg></tuv>
<tuv lang="zh-CN"><seg>{after}</seg></tuv>
</tu></body></tmx>
""",
        encoding="utf-8",
    )
    config = WorkflowConfig(
        root=ROOT,
        manifest=copied_manifest,
        schema=SCHEMA,
        source_po=omegat / "source",
        target_po=omegat / "target",
        glossary=GLOSSARY,
        ledger=tmp_path / "review.json",
        preview_json=tmp_path / "preview.json",
        preview_markdown=tmp_path / "preview.md",
        terminology_json=tmp_path / "terminology.json",
        terminology_markdown=tmp_path / "terminology.md",
        status_json=tmp_path / "status.json",
        status_markdown=tmp_path / "status.md",
        output_schema=ROOT / "schema" / "mtr-output.schema.json",
        output_json=tmp_path / "dist" / "rules.json",
        output_markdown=tmp_path / "dist" / "MTR.md",
    )
    return config, copied_manifest, after


def test_workflow_applies_checks_marks_and_reports(tmp_path):
    config, copied_manifest, expected = _workflow_config(tmp_path)
    answers = iter(["", "y"])
    output: list[str] = []
    test_runs: list[Path] = []

    completed = run_workflow(
        config,
        input_fn=lambda _prompt: next(answers),
        output=output.append,
        test_runner=test_runs.append,
    )

    applied = load_project(copied_manifest, SCHEMA, ROOT)
    units = {unit.unit_id: unit for unit in iter_translation_units(applied)}
    ledger = json.loads(config.ledger.read_text(encoding="utf-8"))
    assert completed is True
    assert units["mtr-introduction"].zh == expected
    assert test_runs == [ROOT]
    assert len(ledger["units"]) == 28
    assert ledger["units"]["mtr-introduction"]["outcome"] == "modified"
    assert ledger["units"]["mtr-introduction"]["note"] == "向导测试批注"
    assert config.preview_markdown.is_file()
    assert config.terminology_markdown.is_file()
    assert config.status_markdown.is_file()
    assert config.output_json.is_file()
    assert config.output_markdown.is_file()
    version_notes = (ROOT / "src" / "mtr" / "version-notes.md").read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n").replace("\r", "\n").strip()
    built_json = json.loads(config.output_json.read_text(encoding="utf-8"))
    assert built_json["intro"]["contents"][0] == {
        "id": "mtr-version-notes",
        "en": "",
        "zh": version_notes,
    }
    assert config.output_markdown.read_text(encoding="utf-8").startswith(
        version_notes + "\n\n# 目录\n"
    )
    assert "Magic: The Gathering Tournament Rules 万智牌比赛规则](#" not in (
        config.output_markdown.read_text(encoding="utf-8")
    )
    assert any("检测到尚未回写的修改" in line for line in output)
    assert any("总进度：28/1453" in line for line in output)
    assert any("系统临时目录中验证" in line for line in output)


def test_prepare_existing_project_validates_without_overwriting(tmp_path):
    config, copied_manifest, _expected = _workflow_config(tmp_path)
    omegat = config.source_po.parent
    shutil.rmtree(omegat)
    project = load_project(copied_manifest, SCHEMA, ROOT)
    export_full(project, omegat, GLOSSARY)
    shutil.copytree(omegat / "source", omegat / "target", dirs_exist_ok=True)
    target = config.target_po / "introduction.po"
    before = target.read_text(encoding="utf-8")
    output: list[str] = []

    created = prepare_omegat_project(config, output=output.append)

    assert created is False
    assert target.read_text(encoding="utf-8") == before
    assert any("没有覆盖任何译文" in line for line in output)


def test_show_review_status_writes_reports(tmp_path):
    config, _copied_manifest, _expected = _workflow_config(tmp_path)
    output: list[str] = []

    statuses = show_review_status(config, output=output.append)

    assert len(statuses) == 1453
    assert config.status_json.is_file()
    assert config.status_markdown.is_file()
    assert any("未审：1453" in line for line in output)


def test_menu_routes_to_status(tmp_path):
    config, _copied_manifest, _expected = _workflow_config(tmp_path)
    answers = iter(["x", "3"])
    output: list[str] = []

    result = run_menu(
        config,
        input_fn=lambda _prompt: next(answers),
        output=output.append,
        test_runner=lambda _root: None,
    )

    assert result == 0
    assert any("输入错误" in line for line in output)
    assert any("MTR 审校进度" in line for line in output)


def test_workflow_cancel_leaves_formal_yaml_and_ledger_unchanged(tmp_path):
    config, copied_manifest, _expected = _workflow_config(tmp_path)
    original = (copied_manifest.parent / "introduction.yaml").read_text(encoding="utf-8")
    answers = iter(["1", "n"])

    completed = run_workflow(
        config,
        input_fn=lambda _prompt: next(answers),
        output=lambda _message: None,
        test_runner=lambda _root: pytest.fail("tests must not run after cancellation"),
    )

    assert completed is False
    assert (copied_manifest.parent / "introduction.yaml").read_text(
        encoding="utf-8"
    ) == original
    assert not config.ledger.exists()


def test_workflow_rejects_translation_changes_after_preview(tmp_path):
    config, copied_manifest, expected = _workflow_config(tmp_path)
    original = (copied_manifest.parent / "introduction.yaml").read_text(encoding="utf-8")
    answers = iter(["1", "y"])

    def answer(prompt: str) -> str:
        response = next(answers)
        if "确认以上文件" in prompt:
            target = config.target_po / "mtr-zh-pilot.po"
            text = target.read_text(encoding="utf-8")
            assert expected in text
            target.write_text(
                text.replace(expected, expected + "（确认期间又修改）", 1),
                encoding="utf-8",
            )
        return response

    with pytest.raises(PipelineError, match="预览后 OmegaT 译文"):
        run_workflow(
            config,
            input_fn=answer,
            output=lambda _message: None,
            test_runner=lambda _root: pytest.fail("stale preview must not run tests"),
        )

    assert (copied_manifest.parent / "introduction.yaml").read_text(
        encoding="utf-8"
    ) == original
    assert not config.ledger.exists()


def test_workflow_treats_invalid_glossary_as_advisory(tmp_path):
    config, copied_manifest, _expected = _workflow_config(tmp_path)
    invalid_glossary = tmp_path / "invalid-glossary.txt"
    invalid_glossary.write_text(
        "Example\t首选译法\nexample\t另一译法\n", encoding="utf-8"
    )
    config = replace(config, glossary=invalid_glossary)
    answers = iter(["", "y"])
    output: list[str] = []

    completed = run_workflow(
        config,
        input_fn=lambda _prompt: next(answers),
        output=output.append,
        test_runner=lambda _root: None,
    )

    assert completed is True
    assert config.ledger.is_file()
    assert json.loads(config.terminology_json.read_text(encoding="utf-8"))["status"] == "failed"
    assert any("术语审计未完成（不阻塞）" in line for line in output)


def test_workflow_treats_terminology_findings_as_advisory(tmp_path):
    config, _copied_manifest, expected = _workflow_config(tmp_path)
    glossary = tmp_path / "glossary-with-error.txt"
    glossary.write_text(
        f"Introduction\t不相关译法\t禁用译法：{expected}\n", encoding="utf-8"
    )
    config = replace(config, glossary=glossary)
    answers = iter(["", "y"])
    output: list[str] = []

    completed = run_workflow(
        config,
        input_fn=lambda _prompt: next(answers),
        output=output.append,
        test_runner=lambda _root: None,
    )

    report = json.loads(config.terminology_json.read_text(encoding="utf-8"))
    assert completed is True
    assert report["errorCount"] == 1
    assert any("术语审计完成（仅供参考）：1 个错误" in line for line in output)
