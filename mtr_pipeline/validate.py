from __future__ import annotations

import argparse
from pathlib import Path

from .paths import project_arguments, resolve_arguments
from .release_gate import require_reviewed
from .core import PipelineError, load_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate editable MTR YAML")
    project_arguments(parser)
    parser.add_argument("--profile", choices=("candidate", "release"))
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--require-reviewed", action="store_true", help="正式发布前验证全文审校状态")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        resolve_arguments(args)
        release = args.require_reviewed or args.profile == "release" or (args.profile is None and not args.allow_incomplete)
        project = load_project(
            args.manifest,
            args.schema,
            args.project_root,
            require_complete=release,
        )
        if release:
            require_reviewed(project)
    except (OSError, ValueError, PipelineError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Validated {len(project.sources)} source file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

