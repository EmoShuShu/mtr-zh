from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from mtr_pipeline.legacy_import import pair_section, split_sections, tokenize_legacy_markdown


ROOT = Path(__file__).resolve().parents[1]


def test_full_migrations_preserve_every_legacy_annotation_exactly_once():
    legacy_text = (ROOT / "AMTR_2025.md").read_text(encoding="utf-8")
    sections, section_issues = split_sections(tokenize_legacy_markdown(legacy_text))
    assert not [issue for issue in section_issues if issue.severity == "error"]

    legacy_annotations: Counter[tuple[str, str]] = Counter()
    for section in sections:
        pairs, _ = pair_section(section)
        legacy_annotations.update(
            (pair.annotation_en, pair.annotation_zh)
            for pair in pairs
            if pair.annotation_en or pair.annotation_zh
        )

    assert sum(legacy_annotations.values()) == 310
    for version in ("2025-11-10", "2026-02-27"):
        generated_annotations: Counter[tuple[str, str]] = Counter()
        for path in sorted((ROOT / "src/mtr" / version).glob("*.yaml")):
            if path.name == "manifest.yaml":
                continue
            chapter = yaml.safe_load(path.read_text(encoding="utf-8"))["chapter"]
            groups = list(chapter.get("groups", []))
            for section in chapter.get("sections", []):
                groups.extend(section["groups"])
            generated_annotations.update(
                (extra["en"], extra["zh"])
                for group in groups
                for block in group["blocks"]
                for extra in block.get("extras", [])
            )
        assert generated_annotations == legacy_annotations, version
