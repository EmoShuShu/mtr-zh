from __future__ import annotations

import argparse
from pathlib import Path

from .paths import ROOT, resolve_arguments
from .core import PipelineError, validate_output_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a published MTR JSON file")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--input", type=Path, default=Path("dist/rules.json"))
    parser.add_argument("--schema", type=Path, default=Path("schema/mtr-output.schema.json"))
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        resolve_arguments(args)
        document = validate_output_file(args.input, args.schema)
    except PipelineError as exc:
        raise SystemExit(str(exc)) from exc
    content_count = len(document["intro"]["contents"])
    content_count += sum(
        len(section["contents"])
        for chapter in document["main"]
        for section in chapter["subrules"]
    )
    content_count += sum(len(appendix["contents"]) for appendix in document["appendices"])
    print(f"Validated published MTR JSON with {content_count} content item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
