"""Restore a review batch after an exception, including cancellation."""
from contextlib import contextmanager
from pathlib import Path
import tempfile


@contextmanager
def restore_on_failure(paths):
    originals = {}
    for path in dict.fromkeys(map(Path, paths)):
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError(f"事务目标不是普通文件：{path}")
        originals[path] = path.read_bytes() if path.exists() else None
    try:
        yield
    except BaseException as failure:
        errors = []
        for path, content in originals.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                elif not path.exists() or path.read_bytes() != content:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            except OSError as error:
                errors.append((path, error))
        if errors:
            recovery = Path(tempfile.mkdtemp(prefix="review-recovery-"))
            for index, (path, content) in enumerate(originals.items()):
                (recovery / f"{index}.path").write_text(str(path), encoding="utf-8")
                if content is not None:
                    (recovery / f"{index}.original").write_bytes(content)
            raise RuntimeError(f"恢复未完成，原始文件备份：{recovery}；错误：{errors}") from failure
        raise
