from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from mtr_pipeline.legacy_import import pair_section, split_sections, tokenize_legacy_markdown


ROOT = Path(__file__).resolve().parents[1]


def test_full_migration_snapshots_preserve_every_legacy_annotation_exactly_once():
    candidate_dirs = sorted((ROOT / "snapshots").glob("*/*/candidate"))
    assert candidate_dirs

    for candidate_dir in candidate_dirs:
        snapshot_dir = candidate_dir.parent
        legacy_text = (snapshot_dir / "inputs/legacy.md").read_text(encoding="utf-8")
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

        generated_annotations: Counter[tuple[str, str]] = Counter()
        for path in sorted(candidate_dir.glob("*.yaml")):
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
        assert generated_annotations == legacy_annotations, snapshot_dir.relative_to(ROOT)
