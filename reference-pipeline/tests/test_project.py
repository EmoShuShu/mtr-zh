from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mtr_reference.project import ProjectError, build_project, load_project


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCHEMA = ROOT / "schemas/mtr-source.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas/mtr-output.schema.json"
EXAMPLE = ROOT / "examples/de-DE/manifest.yaml"


def test_german_example_validates_and_builds_without_zh_fields(tmp_path: Path):
    project = load_project(EXAMPLE, SOURCE_SCHEMA, require_complete=True)
    output = build_project(
        project,
        OUTPUT_SCHEMA,
        tmp_path / "mtr.json",
        tmp_path / "MTR.md",
    )
    serialized = json.dumps(output, ensure_ascii=False)
    assert '"targetLanguage": "de-DE"' in serialized
    assert '"translation"' in serialized
    assert '"zh"' not in serialized
    assert "Turniergrundlagen" in (tmp_path / "MTR.md").read_text(encoding="utf-8")
    table = output["chapters"][0]["sections"][0]["contents"][2]
    assert table["type"] == "table"
    assert "| Rated |" in table["en"]


def test_complete_validation_rejects_blank_translation(tmp_path: Path):
    manifest = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    chapter = yaml.safe_load((EXAMPLE.parent / "chapter-01.yaml").read_text(encoding="utf-8"))
    chapter["chapter"]["sections"][0]["groups"][0]["blocks"][0]["translation"] = ""
    (tmp_path / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "chapter-01.yaml").write_text(
        yaml.safe_dump(chapter, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="Missing translation"):
        load_project(tmp_path / "manifest.yaml", SOURCE_SCHEMA, require_complete=True)
