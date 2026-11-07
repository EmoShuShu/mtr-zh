"""Publish a set of generated files with cooperative locks and batch recovery."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile

from .transaction import restore_on_failure


def publish_files(files: dict[Path, bytes]) -> None:
    if any(Path(path).is_symlink() for path in files):
        raise ValueError("输出目标不能是链接")
    files = {Path(path).parent.resolve() / Path(path).name: data for path, data in files.items()}
    parents = sorted({path.parent for path in files}, key=str)
    locks = []
    try:
        for parent in parents:
            parent.mkdir(parents=True, exist_ok=True)
            lock = parent / ".mtr-output.lock"
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            locks.append(lock)
        with ExitStack() as stack:
            staged = {}
            for path, content in files.items():
                if path.is_symlink() or path.exists() and not path.is_file():
                    raise ValueError(f"输出目标不是普通文件：{path}")
                if path.exists() and path.read_bytes() == content:
                    continue
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".mtr-output-", dir=path.parent)))
                staged[path] = directory / path.name
                staged[path].write_bytes(content)
            with restore_on_failure(staged):
                for path, source in staged.items():
                    os.replace(source, path)
    finally:
        for lock in reversed(locks):
            lock.unlink(missing_ok=True)
