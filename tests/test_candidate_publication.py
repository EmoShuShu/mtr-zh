"""The same repeat/retain/recover behavior runs independently in both repositories."""
import importlib
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'mtr_pipeline' if (ROOT / 'mtr_pipeline').is_dir() else 'ipg_pipeline'
module = importlib.import_module(PACKAGE + '.candidate_publication')


def tree(path):
    return {p.relative_to(path): p.read_bytes() for p in path.rglob('*') if p.is_file()}


def stage(root, value):
    result = root / 'staged'; result.mkdir(exist_ok=True)
    for name in ('rules.json', 'document.md'): (result / name).write_bytes(value)
    return result


def test_repeat_keeps_timestamps_and_changes_archive_complete_evidence(tmp_path):
    target, history = tmp_path / 'candidate', tmp_path / 'history'
    module.publish_candidate(stage(tmp_path, b'old'), target, history)
    timestamps = {p.name: p.stat().st_mtime_ns for p in target.iterdir()}
    assert module.publish_candidate(stage(tmp_path, b'old'), target, history)['status'] == 'unchanged'
    assert {p.name: p.stat().st_mtime_ns for p in target.iterdir()} == timestamps
    result = module.publish_candidate(stage(tmp_path, b'new'), target, history)
    assert set(tree(Path(result['archived'])).values()) == {b'old'}
    assert set(tree(target).values()) == {b'new'}
    with pytest.raises(ValueError, match='已存在'):
        module.publish_candidate(stage(tmp_path, b'newest'), target, history, keep_existing=True)
    assert set(tree(target).values()) == {b'new'}


@pytest.mark.parametrize('fault', ['rename-interrupt', 'replace-error', 'replace-interrupt'])
def test_failed_refresh_preserves_old_evidence(tmp_path, monkeypatch, fault):
    target, history = tmp_path / 'candidate', tmp_path / 'history'
    module.publish_candidate(stage(tmp_path, b'old'), target, history)
    before = tree(target)
    staged = stage(tmp_path, b'new')
    if fault == 'rename-interrupt':
        original = Path.rename
        def rename(path, other):
            result = original(path, other)
            if path == target: raise KeyboardInterrupt()
            return result
        monkeypatch.setattr(Path, 'rename', rename)
    else:
        def replace(*args):
            if fault == 'replace-error': raise OSError('disk full')
            raise KeyboardInterrupt()
        monkeypatch.setattr(module.os, 'replace', replace)
    with pytest.raises((OSError, KeyboardInterrupt)):
        module.publish_candidate(staged, target, history)
    assert tree(target) == before
    assert not list(tmp_path.glob('*.lock'))


def test_unknown_files_and_lock_never_get_overwritten(tmp_path):
    target, history = tmp_path / 'candidate', tmp_path / 'history'
    module.publish_candidate(stage(tmp_path, b'old'), target, history)
    (target / 'user.txt').write_bytes(b'USER')
    before = tree(target)
    with pytest.raises(ValueError, match='未知'):
        module.publish_candidate(stage(tmp_path, b'new'), target, history)
    assert tree(target) == before
    lock = tmp_path / '.maintenance-build.lock'; lock.write_bytes(b'owner')
    with pytest.raises(FileExistsError):
        module.publish_candidate(stage(tmp_path, b'new'), target, history)
    assert lock.read_bytes() == b'owner'


def test_archive_cannot_be_nested_in_target(tmp_path):
    target = tmp_path / 'candidate'
    with pytest.raises(ValueError, match='归档目录'):
        module.publish_candidate(stage(tmp_path, b'new'), target, target / 'history')
    assert not target.exists()


def test_cleanup_failure_does_not_roll_back_successful_publication(tmp_path, monkeypatch):
    target, history = tmp_path / 'candidate', tmp_path / 'history'
    original = Path.unlink
    def fail(path, *args, **kwargs):
        if path.name == '.maintenance-build.lock': raise OSError('cleanup failed')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail)
    result = module.publish_candidate(stage(tmp_path, b'new'), target, history)
    assert result['status'] == 'updated' and 'lockCleanupWarning' in result
    assert set(tree(target).values()) == {b'new'}


def test_failed_restore_retains_complete_archive_outside_staging(tmp_path, monkeypatch):
    target, history = tmp_path / 'candidate', tmp_path / 'history'
    module.publish_candidate(stage(tmp_path, b'old'), target, history)
    staged = stage(tmp_path, b'new')
    original = Path.rename
    def rename(path, other):
        if other == target: raise OSError('restore failed')
        return original(path, other)
    monkeypatch.setattr(Path, 'rename', rename)
    monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('publish failed')))
    with pytest.raises(ValueError, match='人工恢复'):
        module.publish_candidate(staged, target, history)
    assert set(tree(next(history.iterdir())).values()) == {b'old'}
    assert not target.exists()
