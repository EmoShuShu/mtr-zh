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
             "create_snapshot", "check_official_update", "validate", "build", "validate_output", "promote_official_update"]
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


def test_shared_commands_explain_root_and_modes_from_another_directory(tmp_path):
    import subprocess
    import sys
    for script in ("review_workflow", "export_omegat", "review_status", "audit_terminology",
                   "validate", "build", "validate_output", "promote_official_update"):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / f"{script}.py"), "--help"],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "--project-root" in result.stdout
        if script in {"validate", "build"}: assert "--profile" in result.stdout
        if script == "export_omegat": assert "--new-version" in result.stdout
        if script == "review_workflow":
            assert all(flag in result.stdout for flag in ("--candidate-only", "--check-release", "--build-release"))


def test_explicit_relative_output_input_uses_selected_repository(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_output.py"),
                             "--project-root", str(ROOT), "--input", "dist/rules.json"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_batch_recovery_preserves_old_files_and_user_owned_work(tmp_path):
    import importlib
    package = "mtr_pipeline" if (ROOT / "mtr_pipeline").is_dir() else "ipg_pipeline"
    restore = importlib.import_module(package + ".transaction").restore_on_failure
    old, new, user = (tmp_path / name for name in ("old.json", "new.json", "user.tmx"))
    old.write_bytes(b"old output"); user.write_bytes(b"old user edit")
    import pytest
    with pytest.raises(KeyboardInterrupt):
        with restore([old, new]):
            old.write_bytes(b"partial"); new.write_bytes(b"partial"); user.write_bytes(b"new user edit")
            raise KeyboardInterrupt()
    assert old.read_bytes() == b"old output" and not new.exists()
    assert user.read_bytes() == b"new user edit"
