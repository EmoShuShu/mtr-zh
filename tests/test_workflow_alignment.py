"""Behavioral regression coverage using isolated source/engineering/output copies."""
import json
from pathlib import Path
import shutil
import pytest
from mtr_pipeline import publication, review_workflow, build, official_update, promotion
from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.omegat import export_full, iter_translation_units, EngineeringMismatch
from mtr_pipeline.omegat_import import parse_po_collection
from mtr_pipeline.review_status import empty_ledger, mark_reviewed, save_ledger
from test_review_workflow import _workflow_config, ROOT, SCHEMA, GLOSSARY


def files(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("interrupt", [False, True])
def test_output_pair_failure_restores_both_files(tmp_path, monkeypatch, interrupt):
    first, second = tmp_path / "rules.json", tmp_path / "MTR.md"
    first.write_bytes(b"OLD JSON"); second.write_bytes(b"OLD MARKDOWN")
    original = publication.os.replace
    def fail(source, target):
        if target == second:
            if interrupt: raise KeyboardInterrupt()
            raise OSError("disk full")
        original(source, target)
    monkeypatch.setattr(publication.os, "replace", fail)
    with pytest.raises((OSError, KeyboardInterrupt)):
        publication.publish_files({first: b"NEW JSON", second: b"NEW MARKDOWN"})
    assert files(tmp_path) == {Path("rules.json"): b"OLD JSON", Path("MTR.md"): b"OLD MARKDOWN"}


def test_unchanged_output_keeps_timestamp_and_existing_lock_blocks_write(tmp_path):
    path = tmp_path / "rules.json"; path.write_bytes(b"existing")
    timestamp = path.stat().st_mtime_ns
    publication.publish_files({path: b"existing"})
    assert path.stat().st_mtime_ns == timestamp
    lock = tmp_path / ".mtr-output.lock"; lock.write_bytes(b"owner")
    with pytest.raises(FileExistsError):
        publication.publish_files({path: b"new"})
    assert path.read_bytes() == b"existing" and lock.read_bytes() == b"owner"


def test_partial_batch_preserves_published_files(tmp_path):
    config, _, _ = _workflow_config(tmp_path)
    config.output_json.parent.mkdir(); config.output_json.write_bytes(b"OLD JSON"); config.output_markdown.write_bytes(b"OLD MD")
    answers = iter(["a", "yes"])
    assert review_workflow.run_workflow(config, input_fn=lambda _: next(answers), output=lambda _: None, test_runner=lambda _: None)
    assert config.output_json.read_bytes() == b"OLD JSON" and config.output_markdown.read_bytes() == b"OLD MD"
    assert (tmp_path / "current-candidate/rules.json").is_file()


@pytest.mark.parametrize("candidate_only", [False, True])
def test_all_reviewed_can_build_final_or_explicit_candidate(tmp_path, candidate_only):
    config, manifest, _ = _workflow_config(tmp_path)
    project = load_project(manifest, SCHEMA, ROOT)
    full = tmp_path / "full"
    export_full(project, full, GLOSSARY)
    po = parse_po_collection(full / "source")
    ledger = empty_ledger(str(project.manifest["document"]["version"]))
    mark_reviewed(project, ledger, po, po, files=list(po), id_prefixes=[], unit_ids=[])
    save_ledger(config.ledger, ledger)
    answers = iter(["a", "y"])
    assert review_workflow.run_workflow(config, input_fn=lambda _: next(answers), output=lambda _: None,
                                       test_runner=lambda _: None, candidate_only=candidate_only)
    assert config.output_json.exists() is not candidate_only
    assert (tmp_path / "current-candidate/rules.json").exists() is candidate_only


@pytest.mark.parametrize("answer", ["n", "y", "interrupt", "changed-source"])
def test_archive_preserves_user_engineering_and_recovers_failure(tmp_path, monkeypatch, answer):
    config, manifest, _ = _workflow_config(tmp_path)
    old = config.source_po.parent
    before = files(old)  # Pilot engineering differs from the full current project.
    def confirm(_):
        if answer == "changed-source":
            source = manifest.parent / "introduction.yaml"
            source.write_text(source.read_text() + "\n# concurrent user edit\n")
        return "n" if answer == "n" else "y"
    if answer == "interrupt":
        original = review_workflow.prepare_omegat_project
        def failing(config, **kwargs):
            if not old.exists():
                old.mkdir(); (old / "partial").write_text("partial")
                raise KeyboardInterrupt()
            return original(config, **kwargs)
        monkeypatch.setattr(review_workflow, "prepare_omegat_project", failing)
    if answer in {"interrupt", "changed-source"}:
        with pytest.raises((KeyboardInterrupt, PipelineError)):
            review_workflow.prepare_new_version(config, input_fn=confirm, output=lambda _: None)
    else:
        result = review_workflow.prepare_new_version(config, input_fn=confirm, output=lambda _: None)
        assert result is (None if answer == "n" else True)
    if answer == "y":
        archives = list((old.parent / "omegat-archive").iterdir())
        assert len(archives) == 1 and files(archives[0]) == before
        assert review_workflow.prepare_omegat_project(config, output=lambda _: None) is False
    else:
        assert files(old) == before


def test_standalone_export_preserves_all_existing_user_files(tmp_path):
    project = load_project(ROOT / "src/mtr/2026-02-27/manifest.yaml", SCHEMA, ROOT)
    export_full(project, tmp_path, GLOSSARY)
    (tmp_path / "omegat/project_save.tmx").write_bytes(b"user memory")
    (tmp_path / "target/work.po").write_bytes(b"user target")
    before = files(tmp_path)
    export_full(project, tmp_path, GLOSSARY)
    assert files(tmp_path) == before
    source = tmp_path / "source/introduction.po"
    source.write_text(source.read_text().replace('msgid "Introduction"', 'msgid "Old Introduction"'))
    before = files(tmp_path)
    with pytest.raises(EngineeringMismatch): export_full(project, tmp_path, GLOSSARY)
    assert files(tmp_path) == before


@pytest.mark.parametrize("page_date,pdf_date,accepted", [("2026-01-01", "2026-03-01", True), ("2026-03-01", "2026-01-01", False)])
def test_official_check_uses_pdf_date_and_does_not_switch_source(tmp_path, monkeypatch, page_date, pdf_date, accepted):
    from dataclasses import replace
    shutil.copytree(ROOT / "src", tmp_path / "src")
    old = official_update.load_source_state(tmp_path / "src/mtr/official-source.yaml")
    page = replace(old, effective_date=page_date, pdf_sha256="")
    pdf = replace(page, effective_date=pdf_date, pdf_sha256="a" * 64)
    monkeypatch.setattr(official_update, "discover_official_release", lambda *a, **k: page)
    monkeypatch.setattr(official_update, "download_official_pdf", lambda *a, **k: pdf)
    calls = []
    def snapshot(**kwargs):
        calls.append(kwargs)
        return tmp_path / "snapshots/new", True, {"status": {"findings": {}}}
    monkeypatch.setattr(official_update, "create_update_snapshot", snapshot)
    before = files(tmp_path / "src")
    kwargs = dict(page_url=old.page_url, source_state_path=Path("src/mtr/official-source.yaml"), current_version_path=Path("src/mtr/current-version.txt"), schema=Path("schema/mtr-source.schema.json"), project_root=tmp_path, snapshot_root=Path("snapshots"), result_path=Path("tmp/result.json"))
    if accepted:
        result = official_update.check_for_update(**kwargs)
        assert result["promotion"] == "pending-confirmation" and result["currentReleaseUnchanged"]
        assert len(calls) == 1
    else:
        with pytest.raises(official_update.OfficialUpdateError, match="downgrade"):
            official_update.check_for_update(**kwargs)
        assert not calls
    assert files(tmp_path / "src") == before


def test_default_formal_build_blocks_unreviewed_without_touching_dist(tmp_path):
    for name in ("src", "schema", "assets"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (tmp_path / "dist").mkdir(); (tmp_path / "dist/rules.json").write_bytes(b"OLD")
    assert build.main(["--project-root", str(tmp_path)]) == 1
    assert files(tmp_path / "dist") == {Path("rules.json"): b"OLD"}
    assert build.main(["--project-root", str(tmp_path), "--profile", "candidate"]) == 0
    assert (tmp_path / "outputs/candidate/MTR.md").is_file()
    assert build.main(["--project-root", str(tmp_path), "--profile", "candidate", "--output-dir", "dist"]) == 1



def test_partial_review_can_save_when_another_unit_is_untranslated(tmp_path):
    config, manifest, _ = _workflow_config(tmp_path)
    project = load_project(manifest, SCHEMA, ROOT)
    from mtr_pipeline.omegat_import import TranslationChange, apply_changes_to_source
    unit = next(u for u in iter_translation_units(project) if u.unit_id.startswith("mtr-1.1-") and u.kind == "paragraph")
    change = TranslationChange(unit.unit_id, unit.kind, unit.source_file, unit.en, unit.zh, "")
    apply_changes_to_source(project, [change], SCHEMA)
    answers = iter(["a", "y"])
    assert review_workflow.run_workflow(config, input_fn=lambda _: next(answers), output=lambda _: None, test_runner=lambda _: None)
    assert not config.output_json.exists()
    assert (tmp_path / "current-candidate/rules.json").exists()
    assert "【待翻译】" in (tmp_path / "current-candidate/rules.json").read_text()
    after = load_project(manifest, SCHEMA, ROOT, require_complete=False)
    assert next(u.zh for u in iter_translation_units(after) if u.unit_id == unit.unit_id) == ""
    assert "【待翻译】" not in config.ledger.read_text()


def test_source_only_progress_defaults_work_outside_repository(tmp_path, monkeypatch):
    from mtr_pipeline.review_status import main
    root = tmp_path / "repo"
    for name in ("src", "schema", "assets"):
        shutil.copytree(ROOT / name, root / name)
    before = files(root / "src")
    monkeypatch.chdir(tmp_path)
    assert main(["--project-root", str(root)]) == 0
    assert main(["--project-root", str(root), "status"]) == 0
    assert (root / "outputs/review-status.json").exists()
    assert not (root / "review").exists() and not (root / "outputs/omegat-mtr-full").exists()
    assert files(root / "src") == before



def test_interrupt_immediately_after_archiving_restores_old_engineering(tmp_path, monkeypatch):
    config, _, _ = _workflow_config(tmp_path)
    old = config.source_po.parent
    before = files(old)
    original = Path.rename
    def rename(path, destination):
        result = original(path, destination)
        if path == old:
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        review_workflow.prepare_new_version(config, input_fn=lambda _: "y", output=lambda _: None)
    assert files(old) == before
