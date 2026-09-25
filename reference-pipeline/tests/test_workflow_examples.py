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


def test_official_update_example_runs_full_daily_review_flow():
    text = (ROOT / "workflow-examples/official-update.yml").read_text(encoding="utf-8")
    assert 'cron: "0 6 * * *"' in text
    assert 'timezone: "Asia/Shanghai"' in text
    assert "mtr-reference prepare-update" in text
    assert "gh pr create" in text
    assert "--draft" in text
