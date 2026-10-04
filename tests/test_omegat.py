from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.omegat import (
    PILOT_UNIT_IDS,
    export_full,
    export_pilot,
    iter_translation_units,
    select_units,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "mtr" / "2026-02-27" / "manifest.yaml"
SCHEMA = ROOT / "schema" / "mtr-source.schema.json"
GLOSSARY = ROOT / "terminology" / "mtr-glossary.txt"


@pytest.fixture(scope="module")
def project():
    return load_project(MANIFEST, SCHEMA, ROOT)


def test_translation_units_include_titles_blocks_annotations_and_image_alt(project):
    units = {unit.unit_id: unit for unit in iter_translation_units(project)}

    assert units["mtr-1.1"].kind == "section-title"
    assert units["mtr-1.1-b002"].kind == "paragraph"
    assert units["mtr-1.1-b002::extra:0"].kind == "annotation"
    assert units["mtr-appendix-c-b024"].kind == "image-alt"
    assert "match-win fractions" in units["mtr-appendix-c-b024"].en


def test_select_units_preserves_requested_order_and_rejects_unknown_ids(project):
    selected = select_units(project, ["mtr-2.5", "mtr-introduction-b006"])
    assert [unit.unit_id for unit in selected] == ["mtr-2.5", "mtr-introduction-b006"]

    with pytest.raises(PipelineError, match="Unknown OmegaT unit ID"):
        select_units(project, ["not-a-real-id"])


def test_export_pilot_writes_omegat_project_po_and_glossary(project, tmp_path):
    units = export_pilot(project, tmp_path, GLOSSARY)

    assert len(units) == len(PILOT_UNIT_IDS)
    assert (tmp_path / "omegat.project").is_file()
    assert (tmp_path / "omegat" / "filters.xml").is_file()
    assert (tmp_path / "README.md").is_file()

    po_text = (tmp_path / "source" / "mtr-zh-pilot.po").read_text(encoding="utf-8")
    assert po_text.count("\nmsgctxt ") == len(PILOT_UNIT_IDS)
    for unit_id in PILOT_UNIT_IDS:
        assert f'msgctxt "{unit_id}"' in po_text
    assert 'msgid "Introduction"' in po_text
    introduction = next(unit for unit in units if unit.unit_id == "mtr-introduction")
    assert f"msgstr {json.dumps(introduction.zh, ensure_ascii=False)}" in po_text
    assert "#. status: legacy-unreviewed" in po_text
    assert "Cards remaining in pack | Time allotted |\\n" in po_text

    project_tree = ET.parse(tmp_path / "omegat.project")
    assert project_tree.findtext("./project/sentence_seg") == "false"
    assert project_tree.findtext("./project/source_lang") == "EN-US"
    assert project_tree.findtext("./project/target_lang") == "ZH-CN"
    assert (
        project_tree.findtext("./project/target_tok")
        == "org.omegat.tokenizer.LuceneSmartChineseTokenizer"
    )
    glossary_dir = project_tree.findtext("./project/glossary_dir")
    configured_glossary_dir = Path(glossary_dir)
    if not configured_glossary_dir.is_absolute():
        configured_glossary_dir = tmp_path / configured_glossary_dir
    assert configured_glossary_dir.resolve() == GLOSSARY.parent.resolve()
    assert project_tree.findtext("./project/glossary_file") == GLOSSARY.name

    filters_tree = ET.parse(tmp_path / "omegat" / "filters.xml")
    filter_node = filters_tree.find("./filter")
    assert filter_node is not None
    assert filter_node.attrib["className"] == "org.omegat.filters2.po.PoFilter"
    assert filter_node.attrib["enabled"] == "true"
    assert filter_node.find("files").attrib["sourceFilenameMask"] == "*.po"

def test_export_full_writes_one_po_per_manifest_file(project, tmp_path):
    units = export_full(project, tmp_path, GLOSSARY)

    po_files = sorted((tmp_path / "source").glob("*.po"))
    assert len(po_files) == len(project.sources) == 17
    assert len(units) == 1453
    assert {path.stem for path in po_files} == {
        source.path.stem for source in project.sources
    }
    assert sum(
        path.read_text(encoding="utf-8").count("\nmsgctxt ") for path in po_files
    ) == len(units)
