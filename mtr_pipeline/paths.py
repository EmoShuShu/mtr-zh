"""Common maintenance defaults; explicit relative paths use the chosen root."""
from pathlib import Path
import re
from .core import PipelineError

ROOT = Path(__file__).resolve().parents[1]


def current_manifest(root):
    version = (root / "src/mtr/current-version.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
        raise PipelineError("当前 MTR 版本指针无效")
    path = (root / "src/mtr" / version / "manifest.yaml").resolve()
    if not path.is_relative_to(root / "src/mtr"):
        raise PipelineError("当前 MTR 版本路径越界")
    return path


def project_arguments(parser):
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--schema", type=Path, default=Path("schema/mtr-source.schema.json"))


def resolve_arguments(args):
    args.project_root = args.project_root.resolve()
    for key, value in vars(args).items():
        if isinstance(value, Path) and key != "project_root":
            setattr(args, key, args.project_root / value)
    if hasattr(args, "manifest") and args.manifest is None:
        args.manifest = current_manifest(args.project_root)
    return args
