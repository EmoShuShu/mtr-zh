"""Publish disposable build evidence while retaining previous complete generations."""
import os
from pathlib import Path
import shutil
import uuid


def publish_candidate(staged, destination, history_root, *, keep_existing=False):
    staged, destination, history_root = map(Path, (staged, destination, history_root))
    if history_root.resolve().is_relative_to(destination.resolve()):
        raise ValueError("归档目录不能位于候选目录内；请指定其他输出目录")
    expected = {p.name for p in staged.iterdir()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / '.maintenance-build.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    publication = {}
    try:
        os.close(descriptor)
        if destination.is_symlink() or destination.is_junction():
            raise ValueError('候选目录不能是链接')
        if destination.exists():
            if keep_existing:
                raise ValueError('输出目录已存在；已选择保留现有证据，请指定新目录')
            if (not destination.is_dir() or {p.name for p in destination.iterdir()} != expected
                    or any(not p.is_file() or p.is_symlink() for p in destination.iterdir())):
                raise ValueError('输出目录含未知文件或结构；不会覆盖，请指定新目录')
            if all((destination / p.name).read_bytes() == p.read_bytes() for p in staged.iterdir()):
                publication['status'] = 'unchanged'
                return publication
        archive = history_root / (destination.name + '-' + uuid.uuid4().hex)
        try:
            if destination.exists():
                archive.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(archive)
            os.replace(staged, destination)
        except BaseException:
            if archive.exists():
                try:
                    if destination.exists():
                        shutil.rmtree(destination)
                    archive.rename(destination)
                except OSError as error:
                    raise ValueError(f"旧候选完整保存在 {archive}，请人工恢复。") from error
            elif destination.exists() and not staged.exists():
                shutil.rmtree(destination)
            raise
        publication.update(status='updated', archived=str(archive) if archive.exists() else None)
        return publication
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError as error:
            # Publication has committed; do not make callers roll back source only.
            publication['lockCleanupWarning'] = str(error)
