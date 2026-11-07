from dataclasses import replace
import json
from pathlib import Path
import shutil
import pytest
import yaml
from mtr_pipeline import output_validate, omegat, review_workflow, review_status, terminology
from mtr_pipeline.core import PipelineError, load_project
from test_review_workflow import _workflow_config, ROOT, SCHEMA, GLOSSARY


def files(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_output_rejects_other_version_but_supports_explicit_historical_schema_check(tmp_path):
    data = json.loads((ROOT / 'dist/rules.json').read_text()); data['version'] = '20000101'
    output = tmp_path / 'rules.json'; output.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match='当前版本'):
        output_validate.main(['--input', str(output), '--project-root', str(ROOT)])
    assert output_validate.main(['--input', str(output), '--project-root', str(ROOT), '--schema-only']) == 0


@pytest.mark.parametrize('legacy', [False, True])
def test_new_date_with_same_english_requires_handoff(tmp_path, legacy):
    root = tmp_path / 'repo'
    for name in ('src', 'assets', 'schema', 'terminology'):
        shutil.copytree(ROOT / name, root / name)
    config = review_workflow.default_config(root)
    review_workflow.prepare_omegat_project(config, output=lambda _: None)
    project = config.source_po.parent
    if legacy: (project / 'omegat/maintenance-baseline.json').unlink()
    before = files(project)
    new = root / 'src/mtr/2026-03-01'; shutil.copytree(config.manifest.parent, new)
    manifest = yaml.safe_load((new / 'manifest.yaml').read_text())
    manifest['document'].update(version='20260301', effectiveDate='2026-03-01')
    (new / 'manifest.yaml').write_text(yaml.safe_dump(manifest, allow_unicode=True))
    (root / 'src/mtr/current-version.txt').write_text('2026-03-01\n')
    config = review_workflow.default_config(root)
    prompts = []
    assert review_workflow.prepare_new_version(config, input_fn=lambda question: prompts.append(question) or 'n', output=lambda _: None) is None
    assert prompts and files(project) == before
    assert review_workflow.prepare_new_version(config, input_fn=lambda _: 'y', output=lambda _: None)
    assert files(next((root / 'outputs/omegat-archive').iterdir())) == before
    assert json.loads((project / 'omegat/maintenance-baseline.json').read_text())['effectiveDate'] == '2026-03-01'


@pytest.mark.parametrize('field', ['manifest', 'ledger', 'version-notes'])
def test_confirmation_input_changes_stop_before_writeback(tmp_path, field):
    config, _, _ = _workflow_config(tmp_path)
    if field == 'version-notes':
        root = tmp_path / 'root'
        shutil.copytree(ROOT / 'assets', root / 'assets')
        shutil.copytree(ROOT / 'schema', root / 'schema')
        (root / 'src/mtr').mkdir(parents=True)
        shutil.copyfile(ROOT / 'src/mtr/version-notes.md', root / 'src/mtr/version-notes.md')
        config = replace(config, root=root, output_schema=root / 'schema/mtr-output.schema.json')
        changed = root / 'src/mtr/version-notes.md'
    else: changed = getattr(config, field)
    sources = files(config.manifest.parent)
    answers = iter(['a', 'y'])
    def answer(_):
        result = next(answers)
        if result == 'y': changed.write_bytes((changed.read_bytes() if changed.exists() else b'') + b'\n# USER EDIT\n')
        return result
    with pytest.raises(PipelineError, match='预览后'):
        review_workflow.run_workflow(config, input_fn=answer, output=lambda _:None,
                                    test_runner=lambda _:pytest.fail('must stop before testing or writing'))
    assert changed.read_bytes().endswith(b'# USER EDIT\n')
    assert all((config.manifest.parent / name).read_bytes() == data for name,data in sources.items() if config.manifest.parent / name != changed)
    assert not config.output_json.exists()


def test_advisory_terminology_has_json_and_explicit_strict_mode(tmp_path, capsys):
    glossary = tmp_path / 'bad.txt'; glossary.write_text('bad row')
    args = ['--project-root',str(ROOT),'--glossary',str(glossary),'--json-report',str(tmp_path/'report.json'),
            '--markdown-report',str(tmp_path/'report.md'),'--json']
    assert terminology.main(args) == 0
    assert json.loads(capsys.readouterr().out)['nonBlocking']
    assert terminology.main(args + ['--fail-on-errors']) == 1


@pytest.mark.parametrize('interrupt', [False, True])
def test_same_pdf_new_baseline_keeps_frozen_snapshot_and_regenerates(tmp_path, monkeypatch, interrupt):
    from test_promotion import snapshot
    from mtr_pipeline import official_update as update
    root = tmp_path / 'repo'
    for name in ('src', 'schema', 'assets'):
        shutil.copytree(ROOT / name, root / name)
    (root / 'mtr_pipeline').mkdir()
    for name in ('official_update.py','legacy_import.py'):
        shutil.copyfile(ROOT / 'mtr_pipeline' / name, root / 'mtr_pipeline' / name)
    frozen = snapshot(root)
    before = files(frozen)
    pdf = frozen / 'official/MTR_EN.pdf'
    release = update.OfficialRelease(update.DEFAULT_RULES_PAGE, update.EXPECTED_TITLE,
                                    '2026-03-01', 'https://media.wizards.com/test.pdf', update.sha256_file(pdf))
    kwargs = dict(official_pdf=pdf, release=release, base_manifest=root/'src/mtr/2026-02-27/manifest.yaml',
                  schema=root/'schema/mtr-source.schema.json', project_root=root, snapshot_root=root/'snapshots')
    assert update.create_update_snapshot(**kwargs)[:2] == (frozen, False)
    baseline = kwargs['base_manifest']; baseline.write_text(baseline.read_text() + '\n')
    def parse(_):
        if interrupt: raise KeyboardInterrupt()
        return []
    monkeypatch.setattr(update, 'parse_official_pdf', parse)
    def rebase(project, sections, release, output, report):
        shutil.copytree(project.manifest_path.parent, output)
        return {'summary': {'error': 0, 'warning': 0, 'info': 0}}
    monkeypatch.setattr(update, 'rebase_project', rebase)
    if interrupt:
        with pytest.raises(KeyboardInterrupt): update.create_update_snapshot(**kwargs)
        assert not list((root/'snapshots/regenerated').rglob('*.yaml'))
        assert not list((root/'snapshots').rglob('*.staging-*'))
    else:
        created, fresh, record = update.create_update_snapshot(**kwargs)
        assert fresh and created.is_relative_to(root/'snapshots/regenerated')
        assert record['snapshotId'] == created.relative_to(root/'snapshots').as_posix()
        update.verify_snapshot(created, release.pdf_sha256)
        evidence = files(created)
        assert update.create_update_snapshot(**kwargs)[:2] == (created, False)
        assert files(created) == evidence
    assert files(frozen) == before


def test_writeback_refuses_old_project_identity_even_when_english_is_same(tmp_path):
    config, _, _ = _workflow_config(tmp_path)
    identity = config.source_po.parent / 'omegat/maintenance-baseline.json'
    identity.write_text(json.dumps({'documentVersion': '20000101', 'effectiveDate': '2000-01-01'}))
    before = files(config.manifest.parent)
    with pytest.raises(omegat.EngineeringMismatch, match='其他版本'):
        review_workflow.run_workflow(config, input_fn=lambda _: pytest.fail('must stop before prompting'),
                                    test_runner=lambda _:pytest.fail('must not write'))
    assert files(config.manifest.parent) == before
