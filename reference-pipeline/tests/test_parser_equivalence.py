from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from mtr_reference.pdf_parser import parse_official_pdf


REFERENCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = REFERENCE_ROOT.parent
OFFICIAL_PDF = (
    REPOSITORY_ROOT
    / "snapshots/2026-02-27/a627fb8c8568/official/MTR_EN.pdf"
)


@pytest.mark.skipif(not OFFICIAL_PDF.is_file(), reason="repository snapshot is unavailable")
def test_reference_parser_matches_proven_production_parser():
    sys.path.insert(0, str(REPOSITORY_ROOT))
    try:
        from mtr_pipeline.legacy_import import parse_official_pdf as parse_existing

        existing = parse_existing(OFFICIAL_PDF)
    finally:
        sys.path.remove(str(REPOSITORY_ROOT))

    reference = parse_official_pdf(OFFICIAL_PDF)
    official_schema = json.loads(
        (REFERENCE_ROOT / "schemas/mtr-official.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(official_schema).validate(reference.as_dict())
    assert len(reference.sections) == 94
    assert sum(len(section.units) for section in reference.sections) == 947
    assert [section.as_dict() for section in reference.sections] == [
        {
            "key": section.key,
            "en": section.en,
            "units": [
                {"kind": unit.kind, "text": unit.text, "pages": list(unit.pages)}
                for unit in section.units
            ],
        }
        for section in existing
    ]
