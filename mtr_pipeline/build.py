from __future__ import annotations

import argparse
from pathlib import Path

from .core import PipelineError, load_project, write_outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deterministic MTR JSON and Markdown")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        project = load_project(args.manifest, args.schema, args.project_root)
        write_outputs(project, args.json_out, args.markdown_out)
    except PipelineError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote {args.json_out}")
    print(f"Wrote {args.markdown_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

