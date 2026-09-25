from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diff import diff_documents, write_diff
from .official_io import read_official_json, write_official_json, write_official_markdown
from .official_source import check_for_update
from .pdf_parser import parse_official_pdf
from .project import build_project, load_project


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA = ROOT / "schemas/mtr-source.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas/mtr-output.schema.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtr-reference",
        description="Parse and maintain a bilingual Magic Tournament Rules project",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse = subparsers.add_parser("parse", help="Parse an official English MTR PDF")
    parse.add_argument("pdf", type=Path)
    parse.add_argument("--json-out", type=Path, required=True)
    parse.add_argument("--markdown-out", type=Path, required=True)

    diff = subparsers.add_parser("diff", help="Compare two parsed official JSON files")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    diff.add_argument("--json-out", type=Path, required=True)
    diff.add_argument("--markdown-out", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="Validate translation YAML")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--allow-incomplete", action="store_true")
    validate.add_argument("--schema", type=Path, default=SOURCE_SCHEMA)

    build = subparsers.add_parser("build", help="Build generic bilingual JSON and Markdown")
    build.add_argument("manifest", type=Path)
    build.add_argument("--schema", type=Path, default=SOURCE_SCHEMA)
    build.add_argument("--output-schema", type=Path, default=OUTPUT_SCHEMA)
    build.add_argument("--json-out", type=Path, required=True)
    build.add_argument("--markdown-out", type=Path, required=True)

    check = subparsers.add_parser("check", help="Check the official WPN page for an update")
    check.add_argument("--state", type=Path, required=True)
    check.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "parse":
        document = parse_official_pdf(args.pdf)
        write_official_json(document, args.json_out)
        write_official_markdown(document, args.markdown_out)
        print(
            f"Parsed {len(document.sections)} sections and "
            f"{sum(len(section.units) for section in document.sections)} units"
        )
    elif args.command == "diff":
        changes = diff_documents(read_official_json(args.old), read_official_json(args.new))
        write_diff(changes, args.json_out, args.markdown_out)
        print(f"Wrote {len(changes)} exact change(s)")
    elif args.command == "validate":
        project = load_project(
            args.manifest,
            args.schema,
            require_complete=not args.allow_incomplete,
        )
        print(f"Validated {len(project.chapters)} chapter file(s)")
    elif args.command == "build":
        project = load_project(args.manifest, args.schema, require_complete=True)
        build_project(
            project,
            args.output_schema,
            args.json_out,
            args.markdown_out,
        )
        print(f"Wrote {args.json_out} and {args.markdown_out}")
    else:
        result = check_for_update(args.state, args.output_dir)
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
