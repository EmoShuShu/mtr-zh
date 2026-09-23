from __future__ import annotations

import argparse
from pathlib import Path

from .core import PipelineError, validate_output_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a published MTR JSON file")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
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
