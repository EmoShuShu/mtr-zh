from __future__ import annotations

import base64
import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from mtr_pipeline.core import (
    PipelineError,
    _chapter_heading,
    _flatten_groups,
    assemble_group,
    build_outputs,
    load_project,
    validate_built_output,
    validate_document,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "mtr" / "examples" / "manifest.yaml"
FULL_MANIFEST = ROOT / "src" / "mtr" / "2026-02-27" / "manifest.yaml"
SCHEMA = ROOT / "schema" / "mtr-source.schema.json"
OUTPUT_SCHEMA = ROOT / "schema" / "mtr-output.schema.json"
CURRENT_VERSION = ROOT / "src" / "mtr" / "current-version.txt"


@pytest.fixture(scope="module")
def project():
    return load_project(MANIFEST, SCHEMA, ROOT)


@pytest.fixture(scope="module")
def full_project():
    return load_project(FULL_MANIFEST, SCHEMA, ROOT)


def _all_contents(output: dict):
    for chapter in output["main"]:
        yield from chapter.get("contents", [])
        for section in chapter.get("subrules", []):
            yield from section.get("contents", [])
    for appendix in output["appendices"]:
        yield from appendix.get("contents", [])


def test_representative_sources_validate(project):
    assert len(project.sources) == 4


def test_current_version_pointer_identifies_release_manifest():
    version = CURRENT_VERSION.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", version)
    manifest_path = ROOT / "src" / "mtr" / version / "manifest.yaml"
    assert manifest_path.is_file()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["document"]["effectiveDate"] == version
    assert manifest["document"]["version"] == version.replace("-", "")


def test_paragraph_group_reconstructs_official_text(project):
    chapter_one = project.sources[0].data["chapter"]
    group = chapter_one["sections"][0]["groups"][1]
    assert assemble_group(group, "en") == (
        "There are two major tournament formats: Limited and Constructed. "
        "Each has rules specific to its format. In Limited tournaments, all product for play is provided "
        "during the tournament. In Constructed tournaments, players compete using decks prepared beforehand."
    )
    assert assemble_group(group, "zh").startswith("主要的赛制有两种——限制赛和构组赛。每个赛制")


def test_build_flattens_groups_and_preserves_extras(project):
    json_text, _ = build_outputs(project, validate_output=False)
    output = json.loads(json_text)
    assert '"groups"' not in json_text
    blocks = list(_all_contents(output))
    annotated = next(block for block in blocks if block["id"] == "mtr-1.1-b002")
    assert len(annotated["extras"]) == 1
    assert "\n\n" in annotated["extras"][0]["en"]


def test_shared_version_notes_and_generated_toc_lead_both_outputs(project):
    json_text, markdown_text = build_outputs(project, validate_output=False)
    output = json.loads(json_text)
    version_notes, toc = output["intro"]["contents"][:2]

    assert version_notes["id"] == "mtr-version-notes"
    assert version_notes["en"] == ""
    assert version_notes["zh"].startswith("# 版本说明\n")
    assert toc["id"] == "mtr-table-of-contents"
    assert toc["en"] == ""
    assert "[MTR 1.1 Tournament Types 比赛种类](/mtr/1#1.1)" in toc["zh"]
    assert "[Appendix B—Time Limits 时间限制](/mtr/appendix-b)" in toc["zh"]

    assert markdown_text.startswith("# 版本说明\n")
    assert markdown_text.index("# 目录\n") > markdown_text.index("# 版本说明\n")
    assert markdown_text.index("# Magic: The Gathering Tournament Rules 万智牌比赛规则\n") > markdown_text.index("# 目录\n")
    assert "[MTR 1.1 Tournament Types 比赛种类](#mtr-11-tournament-types-比赛种类)" in markdown_text


def test_markdown_headings_use_english_then_chinese_without_duplicate_intro(project):
    _, markdown_text = build_outputs(project, validate_output=False)
    assert "# MTR 1. Tournament Fundamentals 比赛基本要素\n" in markdown_text
    assert "## MTR 1.1 Tournament Types 比赛种类\n" in markdown_text
    assert "# Appendix B—Time Limits 时间限制\n" in markdown_text
    assert "# MTR 1. 比赛基本要素 Tournament Fundamentals\n" not in markdown_text
    assert _chapter_heading(
        {
            "id": "mtr-introduction",
            "chapter": "Introduction",
            "en": "Introduction",
            "zh": "引言",
        }
    ) == "Introduction 引言"


def test_images_are_inline_base64_and_round_trip(project):
    json_text, markdown_text = build_outputs(project, validate_output=False)
    output = json.loads(json_text)
    expected_assets = {
        "mtr-10.4-b002": "playoff-bracket-8-seeding.png",
        "mtr-10.4-b004": "playoff-bracket-4.png",
        "mtr-10.4-b006": "draft-pod-seating.png",
        "mtr-10.4-b008": "playoff-bracket-8-seating.png",
    }
    blocks = {item["id"]: item for item in _all_contents(output)}
    for block_id, filename in expected_assets.items():
        expected = (ROOT / "assets" / "diagrams" / filename).read_bytes()
        for language in ("en", "zh"):
            match = re.search(
                r"data:image/png;base64,([A-Za-z0-9+/=]+)\)",
                blocks[block_id][language],
            )
            assert match is not None
            assert "\n" not in match.group(1)
            assert base64.b64decode(match.group(1), validate=True) == expected
    assert "assets/diagrams" not in markdown_text


def test_multiblock_table_is_reassembled_for_json_and_markdown(full_project):
    json_text, markdown_text = build_outputs(full_project)
    output = json.loads(json_text)
    appendix = next(item for item in output["appendices"] if item["chapter"] == "Appendix F")
    table = next(item for item in appendix["contents"] if item["id"] == "mtr-appendix-f-b002")

    assert "| --- | --- |\n| Eternal Weekend | Competitive |" in table["en"]
    assert "| --- | --- |\n| 永恒周末 | 竞争 |" in table["zh"]
    assert not any(item["id"] == "mtr-appendix-f-b003" for item in appendix["contents"])
    assert "| --- | --- |\n| Eternal Weekend | Competitive |" in markdown_text
    assert "| --- | --- |\n| 永恒周末 | 竞争 |" in markdown_text


def test_multiblock_table_with_row_annotation_is_rejected(full_project):
    source = next(item for item in full_project.sources if item.path.name == "appendix-f.yaml")
    group = copy.deepcopy(source.data["chapter"]["groups"][1])
    group["blocks"][1]["extras"] = [{"en": "Row note", "zh": "行注解"}]

    with pytest.raises(PipelineError, match="cannot be published without losing row association"):
        _flatten_groups(full_project, [group])


def test_output_schema_rejects_empty_normal_translation(full_project):
    output = json.loads(build_outputs(full_project)[0])
    output["main"][0]["subrules"][0]["contents"][0]["zh"] = ""
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))

    with pytest.raises(PipelineError, match="Schema validation failed"):
        validate_built_output(output, schema, "invalid output")


def test_output_validation_rejects_duplicate_published_id(full_project):
    output = json.loads(build_outputs(full_project)[0])
    contents = output["main"][0]["subrules"][0]["contents"]
    contents[1]["id"] = contents[0]["id"]
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))

    with pytest.raises(PipelineError, match="Duplicate published content IDs"):
        validate_built_output(output, schema, "invalid output")


def test_output_validation_rejects_incomplete_markdown_table(full_project):
    output = json.loads(build_outputs(full_project)[0])
    appendix = next(item for item in output["appendices"] if item["chapter"] == "Appendix F")
    table = next(item for item in appendix["contents"] if item["id"] == "mtr-appendix-f-b002")
    table["en"] = "| Program | Rules Enforcement Level |\n| --- | --- |"
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))

    with pytest.raises(PipelineError, match="Incomplete Markdown table"):
        validate_built_output(output, schema, "invalid output")


def test_output_validation_rejects_non_png_data_uri(full_project):
    output = json.loads(build_outputs(full_project)[0])
    image = next(
        content
        for chapter in output["main"]
        for section in chapter["subrules"]
        for content in section["contents"]
        if content["en"].startswith("![")
    )
    image["en"] = "![diagram](data:image/png;base64,QUJDRA==)"
    image["zh"] = "![示意图](data:image/png;base64,QUJDRA==)"
    schema = json.loads(OUTPUT_SCHEMA.read_text(encoding="utf-8"))

    with pytest.raises(PipelineError, match="Inline image is not a PNG"):
        validate_built_output(output, schema, "invalid output")


def test_build_is_deterministic(project):
    assert build_outputs(project, validate_output=False) == build_outputs(
        project, validate_output=False
    )


def test_schema_rejects_more_than_one_extra(project):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    invalid = copy.deepcopy(project.sources[0].data)
    block = invalid["chapter"]["sections"][0]["groups"][1]["blocks"][0]
    block["extras"].append(copy.deepcopy(block["extras"][0]))
    with pytest.raises(PipelineError, match="Schema validation failed"):
        validate_document(invalid, schema, "invalid extras")


def test_duplicate_ids_are_rejected(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest = MANIFEST.read_text(encoding="utf-8").replace(
        "  - chapter-10.yaml\n  - appendix-b.yaml\n  - appendix-c.yaml\n", ""
    )
    (source_dir / "manifest.yaml").write_text(manifest, encoding="utf-8")
    chapter = (ROOT / "src" / "mtr" / "examples" / "chapter-01.yaml").read_text(encoding="utf-8")
    chapter = chapter.replace("mtr-1.1-b003", "mtr-1.1-b002")
    (source_dir / "chapter-01.yaml").write_text(chapter, encoding="utf-8")
    with pytest.raises(PipelineError, match="Duplicate content IDs"):
        load_project(source_dir / "manifest.yaml", SCHEMA, ROOT)
