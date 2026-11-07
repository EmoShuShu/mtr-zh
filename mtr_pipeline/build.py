from __future__ import annotations
import argparse
from pathlib import Path
from .core import PipelineError, load_project, write_outputs
from .paths import project_arguments, resolve_arguments
from .release_gate import require_reviewed


def _parser():
    parser = argparse.ArgumentParser(description="生成 MTR 候选或全文审校后的正式文件")
    project_arguments(parser)
    parser.add_argument("--output-schema", type=Path, default=Path("schema/mtr-output.schema.json"))
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    parser.add_argument("--profile", choices=("candidate", "release"))
    parser.add_argument("--output-dir", "--output", dest="output_dir", type=Path)
    parser.add_argument("--rehearsal", action="store_true", help="按正式门槛生成到 outputs，保留旧 dist")
    parser.add_argument("--keep-existing", action="store_true", help="已有候选或演练目录时停止，保留冻结证据")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    return parser


def write_candidate_outputs(project, destination, schema, *, candidate=True, keep_existing=False):
    import tempfile
    from .candidate_publication import publish_candidate
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mtr-candidate-", dir=destination.parent) as name:
        staged = Path(name) / "files"
        write_outputs(project, staged / "rules.json", staged / "MTR.md", output_schema_path=schema, candidate=candidate)
        return publish_candidate(staged, destination, destination.parent / "build-history", keep_existing=keep_existing)


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        resolve_arguments(args)
        legacy_outputs = args.json_out is not None or args.markdown_out is not None
        profile = args.profile or ("release" if args.rehearsal or not legacy_outputs else "candidate")
        if args.rehearsal and profile != "release":
            raise PipelineError("生成演练仅适用于 release")
        directory = args.output_dir or args.project_root / ("outputs/release-rehearsal" if args.rehearsal else "outputs/candidate" if profile == "candidate" else "dist")
        if not legacy_outputs:
            if profile == "release" and not args.rehearsal and args.output_dir is not None:
                raise PipelineError("正式构建固定写入 dist；自定义输出仅限候选或演练")
            if profile == "candidate" or args.rehearsal:
                base = (args.project_root / "outputs").resolve()
                destination = directory.resolve()
                if not base.is_relative_to(args.project_root) or not destination.is_relative_to(base) or destination == base:
                    raise PipelineError("候选或演练只能写入本项目 outputs 下的独立目录")
        json_out = args.json_out or directory / "rules.json"
        markdown_out = args.markdown_out or directory / "MTR.md"
        formal = any(p.resolve().is_relative_to((args.project_root / "dist").resolve()) for p in (json_out, markdown_out))
        if (args.profile == "candidate" or args.rehearsal) and formal:
            raise PipelineError("候选文件不能写入 dist")
        if args.keep_existing and (formal or (profile == "release" and not args.rehearsal)):
            raise PipelineError("--keep-existing 仅用于候选或演练")
        release = formal or profile == "release"
        project = load_project(args.manifest, args.schema, args.project_root, require_complete=release)
        if release:
            require_reviewed(project)
        publication = None
        if not legacy_outputs and (profile == "candidate" or args.rehearsal):
            publication = write_candidate_outputs(project, directory, args.output_schema, candidate=not release, keep_existing=args.keep_existing)
        else:
            if args.keep_existing and any(p.exists() for p in (json_out, markdown_out)):
                raise PipelineError("输出已存在；已选择保留现有文件")
            write_outputs(project, json_out, markdown_out, output_schema_path=args.output_schema, candidate=not release)
        if args.json:
            from .maintenance_report import print_json
            print_json({"document": "MTR", "version": str(project.manifest["document"]["version"]),
                        "profile": "release" if release else "candidate", "files": [str(json_out), str(markdown_out)],
                        "publication": publication})
        else:
            print(f"已生成或验证：{json_out}、{markdown_out}")
            if publication and publication.get("archived"): print("旧候选已保留：" + publication["archived"])
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, PipelineError) as error:
        print(f"生成已停止：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
