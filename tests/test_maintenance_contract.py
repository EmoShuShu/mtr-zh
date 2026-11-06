"""Shared user-facing maintenance contract, checked independently in both repos."""
from pathlib import Path
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_official_checks_follow_the_paired_schedule_and_retention():
    workflow = yaml.load((ROOT / ".github/workflows/official-update.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "0 6 * * *", "timezone": "Asia/Shanghai"}]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"]["pull-requests"] == "write"
    uploads = [step for job in workflow["jobs"].values() for step in job["steps"]
               if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert any(step["if"] == "always()" and step["with"]["retention-days"] == "30" for step in uploads)


def test_common_daily_entrypoints_and_python_environment():
    names = ["review_workflow", "export_omegat", "review_status", "audit_terminology",
             "create_snapshot", "check_official_update", "validate", "build", "validate_output"]
    assert all((ROOT / "scripts" / f"{name}.py").is_file() for name in names)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert any(dep.startswith("pytest") for dep in project["project"]["optional-dependencies"]["dev"])
    for name in ("official-update", "validate", "release"):
        workflow = yaml.load((ROOT / f".github/workflows/{name}.yml").read_text(), Loader=yaml.BaseLoader)
        steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
        setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
        assert setup["with"]["python-version"] == "3.13"
        assert any('.[dev]' in step.get("run", "") for step in steps)
    launcher = (ROOT / "审校助手.cmd").read_text()
    assert "scripts\\review_workflow.py" in launcher
    assert 'set "PYTHONUTF8=1"' in launcher
    assert "exit /b %REVIEW_RESULT%" in launcher
