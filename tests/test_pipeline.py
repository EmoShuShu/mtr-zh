from __future__ import annotations

import base64
import copy
import json
import re
from pathlib import Path

import pytest

from mtr_pipeline.core import (
    PipelineError,
    assemble_group,
    build_outputs,
    load_project,
    validate_document,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "mtr" / "examples" / "manifest.yaml"
SCHEMA = ROOT / "schema" / "mtr-source.schema.json"


@pytest.fixture(scope="module")
def project():
    return load_project(MANIFEST, SCHEMA, ROOT)


def _all_contents(output: dict):
    for chapter in output["main"]:
        yield from chapter.get("contents", [])
        for section in chapter.get("subrules", []):
            yield from section.get("contents", [])
    for appendix in output["appendices"]:
        yield from appendix.get("contents", [])


def test_representative_sources_validate(project):
    assert len(project.sources) == 4


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
    json_text, _ = build_outputs(project)
    output = json.loads(json_text)
    assert '"groups"' not in json_text
    blocks = list(_all_contents(output))
    annotated = next(block for block in blocks if block["id"] == "mtr-1.1-b002")
    assert len(annotated["extras"]) == 1
    assert "\n\n" in annotated["extras"][0]["en"]


def test_images_are_inline_base64_and_round_trip(project):
    json_text, markdown_text = build_outputs(project)
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


def test_build_is_deterministic(project):
    assert build_outputs(project) == build_outputs(project)


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
