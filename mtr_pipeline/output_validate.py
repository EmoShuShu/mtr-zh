from __future__ import annotations

import argparse
from pathlib import Path

from .paths import ROOT, current_manifest, resolve_arguments
from .core import PipelineError, validate_output_file, _read_yaml


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a published MTR JSON file")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--input", type=Path, default=Path("dist/rules.json"))
    parser.add_argument("--schema", type=Path, default=Path("schema/mtr-output.schema.json"))
    parser.add_argument("--schema-only", action="store_true", help="只检查结构，用于明确核验历史产物")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        resolve_arguments(args)
        document = validate_output_file(args.input, args.schema)
        if not args.schema_only:
            version = _read_yaml(current_manifest(args.project_root))["document"]["version"]
            if str(document["version"]) != str(version):
                raise PipelineError("输出版本与当前版本指针不一致")
    except PipelineError as exc:
        raise SystemExit(str(exc)) from exc
    content_count = len(document["intro"]["contents"])
    content_count += sum(
        len(section["contents"])
        for chapter in document["main"]
        for section in chapter["subrules"]
    )
    content_count += sum(len(appendix["contents"]) for appendix in document["appendices"])
    print(f"输出校验通过，共 {content_count} 个内容单元。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
