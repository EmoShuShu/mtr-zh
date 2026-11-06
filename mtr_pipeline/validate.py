from __future__ import annotations

import argparse
from pathlib import Path

from .release_gate import require_reviewed
from .core import PipelineError, load_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate editable MTR YAML")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--require-reviewed", action="store_true", help="正式发布前验证全文审校状态")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        project = load_project(
            args.manifest,
            args.schema,
            args.project_root,
            require_complete=not args.allow_incomplete,
        )
        if args.require_reviewed:
            require_reviewed(project)
    except PipelineError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Validated {len(project.sources)} source file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

