import copy
import hashlib
import json
from pathlib import Path
import shutil
import pytest
import yaml
from mtr_pipeline import promotion
from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.official_update import _tree_digest
from mtr_pipeline.snapshot import _artifact_rows, SnapshotError
from mtr_pipeline.omegat import iter_translation_units
from mtr_pipeline.review_status import load_ledger, calculate_status

ROOT = Path(__file__).resolve().parents[1]


def protected(root):
    return {p.relative_to(root): p.read_bytes() for name in ('src', 'review', 'dist', 'snapshots', 'outputs')
            for p in (root / name).rglob('*') if p.is_file()}


@pytest.fixture
def repo(tmp_path):
    for name in ('src', 'schema', 'assets', 'review'):
        shutil.copytree(ROOT / name, tmp_path / name)
    for name in ('dist/rules.json', 'dist/MTR.md', 'outputs/omegat-mtr-full/omegat/project_save.tmx'):
        p = tmp_path / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'USER OLD FILE')
    return tmp_path


def snapshot(root, date='2026-03-01'):
    base = root / 'src/mtr/2026-02-27'
    pdf = b'%PDF synthetic evidence'
    digest = hashlib.sha256(pdf).hexdigest()
    folder = root / 'snapshots' / date / digest[:12]
    shutil.copytree(base, folder / 'inputs/base')
    shutil.copytree(base, folder / 'candidate')
    shutil.copytree(root / 'assets', folder / 'assets')
    manifest = folder / 'candidate/manifest.yaml'
    data = yaml.safe_load(manifest.read_text())
    data['document'].update(effectiveDate=date, version=date.replace('-', ''))
    manifest.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    (folder / 'official').mkdir(); (folder / 'official/MTR_EN.pdf').write_bytes(pdf)
    record = {'snapshotVersion': 1, 'document': {'effectiveDate': date},
              'official': {'path': 'official/MTR_EN.pdf', 'sha256': digest, 'url': 'https://media.wizards.com/test.pdf'},
              'inputs': {'base': {'version': '2026-02-27', 'treeSha256': _tree_digest(base)}},
              'status': {'findings': {}}, 'artifacts': _artifact_rows(folder)}
    (folder / 'snapshot.yaml').write_text(yaml.safe_dump(record, sort_keys=False))
    return folder


@pytest.mark.parametrize('date', ['2026-03-01', '2026-02-27'])
def test_preview_cancel_and_promotion_preserve_data_and_require_new_review(repo, date):
    source = snapshot(repo, date)
    before = protected(repo)
    assert promotion.promote(repo, source)['status'] == 'preview'
    assert promotion.promote(repo, source, confirm=lambda _: False)['status'] == 'cancelled'
    assert protected(repo) == before
    result = promotion.promote(repo, source, confirm=lambda _: True)
    assert result['status'] == 'promoted-for-review'
    mutable = {Path('src/mtr/current-version.txt'), Path('src/mtr/official-source.yaml')}
    if date == '2026-02-27':
        mutable |= {p for p in before if p.is_relative_to('src/mtr/2026-02-27')}
        mutable.add(Path('review/mtr-2026-02-27.json'))
        history = list((repo / 'review/history').glob('*.json'))
        assert len(history) == 1 and history[0].read_bytes() == before[Path('review/mtr-2026-02-27.json')]
    assert all((repo / p).read_bytes() == value for p, value in before.items() if p not in mutable)
    project = load_project(repo / f'src/mtr/{date}/manifest.yaml', repo / 'schema/mtr-source.schema.json', repo)
    ledger = load_ledger(repo / f'review/mtr-{date}.json', date.replace('-', ''))
    previous = json.loads(before[Path('review/mtr-2026-02-27.json')])
    assert ledger['units'] == {key: {**value, 'needsReview': True} for key, value in previous['units'].items()}
    assert {item.status for item in calculate_status(list(iter_translation_units(project)), ledger)} == {'stale'}
    with pytest.raises(PipelineError): promotion.promote(repo, source)


@pytest.mark.parametrize('fault', ['interrupt', 'validation', 'tamper', 'ledger-change'])
def test_failed_promotion_and_changed_inputs_preserve_existing_data(repo, monkeypatch, fault):
    source = snapshot(repo)
    before = protected(repo)
    if fault == 'tamper':
        (source / 'candidate/manifest.yaml').write_text('tampered: true')
        with pytest.raises(SnapshotError): promotion.promote(repo, source, confirm=lambda _: True)
        assert {p: v for p, v in protected(repo).items() if not p.is_relative_to('snapshots')} == {p: v for p, v in before.items() if not p.is_relative_to('snapshots')}
    elif fault == 'ledger-change':
        def confirm(_):
            path = repo / 'review/mtr-2026-02-27.json'
            data = json.loads(path.read_text()); next(iter(data['units'].values()))['note'] = 'CONCURRENT USER NOTE'
            path.write_text(json.dumps(data))
            return True
        with pytest.raises(PipelineError, match='确认期间'): promotion.promote(repo, source, confirm=confirm)
        assert (repo / 'src/mtr/current-version.txt').read_bytes() == before[Path('src/mtr/current-version.txt')]
        assert 'CONCURRENT USER NOTE' in (repo / 'review/mtr-2026-02-27.json').read_text()
    else:
        original = promotion.load_project
        def fail(manifest, *args, **kwargs):
            if manifest == repo / 'src/mtr/2026-03-01/manifest.yaml':
                if fault == 'interrupt': raise KeyboardInterrupt()
                raise PipelineError('final validation failed')
            return original(manifest, *args, **kwargs)
        monkeypatch.setattr(promotion, 'load_project', fail)
        with pytest.raises((PipelineError, KeyboardInterrupt)): promotion.promote(repo, source, confirm=lambda _: True)
        assert protected(repo) == before
