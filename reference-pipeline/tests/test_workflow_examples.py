from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_examples_are_valid_yaml_and_language_neutral():
    for path in (ROOT / "workflow-examples").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert yaml.load(text, Loader=yaml.BaseLoader)
        assert "zh" not in text.lower()
        assert "chinese" not in text.lower()
