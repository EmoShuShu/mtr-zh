from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable

from .core import PipelineError, load_project, write_outputs
from .omegat import export_full, iter_translation_units
from .omegat_import import (
    PoEntry,
    TranslationChange,
    apply_changes_to_source,
    flatten_po_collection,
    parse_po_collection,
    plan_import,
    validate_po_collections,
    write_candidate,
    write_reports,
)
from .review_status import (
    calculate_status,
    load_ledger,
    mark_reviewed,
    parse_omegat_notes,
    save_ledger,
    write_status_reports,
)
from .terminology import audit_terminology, parse_glossary, write_audit_reports


InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]
TestRunner = Callable[[Path], None]


@dataclass(frozen=True)
class WorkflowConfig:
    root: Path
    manifest: Path
    schema: Path
    source_po: Path
    target_po: Path
    glossary: Path
    ledger: Path
    preview_json: Path
    preview_markdown: Path
    terminology_json: Path
    terminology_markdown: Path
    status_json: Path
    status_markdown: Path
    output_schema: Path
    output_json: Path
    output_markdown: Path


def default_config(root: Path) -> WorkflowConfig:
    root = root.resolve()
    version_file = root / "src" / "mtr" / "current-version.txt"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PipelineError(f"无法读取当前版本文件 {version_file}: {exc}") from exc
    if not version:
        raise PipelineError(f"当前版本文件为空：{version_file}")
    omegat_root = root / "outputs" / "omegat-mtr-full"
    return WorkflowConfig(
        root=root,
        manifest=root / "src" / "mtr" / version / "manifest.yaml",
        schema=root / "schema" / "mtr-source.schema.json",
        source_po=omegat_root / "source",
        target_po=omegat_root / "target",
        glossary=root / "terminology" / "mtr-glossary.txt",
        ledger=root / "review" / f"mtr-{version}.json",
        preview_json=omegat_root / "import-preview.json",
        preview_markdown=omegat_root / "import-preview.md",
        terminology_json=root / "outputs" / "terminology-audit.json",
        terminology_markdown=root / "outputs" / "terminology-audit.md",
        status_json=root / "outputs" / "review-status.json",
        status_markdown=root / "outputs" / "review-status.md",
        output_schema=root / "schema" / "mtr-output.schema.json",
        output_json=root / "dist" / "rules.json",
        output_markdown=root / "dist" / "MTR.md",
    )


def parse_file_selection(answer: str, filenames: list[str]) -> list[str]:
    normalized = answer.strip()
    if normalized.casefold() in {"q", "quit", "退出"}:
        return []
    if normalized.casefold() in {"a", "all", "全部"}:
        return list(filenames)
    if not normalized:
        raise PipelineError("没有选择任何文件")
    selected: list[str] = []
    for part in normalized.replace("，", ",").split(","):
        token = part.strip()
        if not token.isdigit():
            raise PipelineError(f"无效编号：{token or '<空>'}")
        index = int(token)
        if index < 1 or index > len(filenames):
            raise PipelineError(f"编号超出范围：{index}")
        filename = filenames[index - 1]
        if filename not in selected:
            selected.append(filename)
    return selected


def changes_outside_selection(
    changes: list[TranslationChange],
    reference_collection: dict[str, list[PoEntry]],
    selected_files: list[str],
) -> list[TranslationChange]:
    file_by_id = {
        entry.unit_id: filename
        for filename, entries in reference_collection.items()
        for entry in entries
    }
    selected = set(selected_files)
    return [change for change in changes if file_by_id[change.unit_id] not in selected]


def run_pytest(root: Path) -> None:
    result = subprocess.run([sys.executable, "-m", "pytest"], cwd=root, check=False)
    if result.returncode:
        raise PipelineError(f"完整测试失败，退出码：{result.returncode}")


def _prompt_for_selection(
    filenames: list[str],
    suggested_files: list[str],
    input_fn: InputFunction,
    output: OutputFunction,
) -> list[str]:
    output("\n请选择已经逐条审完的 PO 文件（多个编号用逗号分隔）：")
    if suggested_files:
        output("  检测到尚未回写的修改：" + "、".join(suggested_files))
        output("  直接回车：选择以上文件")
    else:
        output("  未检测到尚未回写的译文修改；请选择已审文件。")
    for index, filename in enumerate(filenames, start=1):
        output(f"  {index:2}. {filename}")
    output("   a. 全部文件")
    output("   q. 取消")
    while True:
        answer = input_fn("\n请输入编号：")
        if not answer.strip() and suggested_files:
            return suggested_files
        try:
            selected = parse_file_selection(answer, filenames)
        except PipelineError as exc:
            output(f"输入错误：{exc}")
            continue
        return selected


def run_workflow(
    config: WorkflowConfig,
    *,
    input_fn: InputFunction = input,
    output: OutputFunction = print,
    test_runner: TestRunner = run_pytest,
) -> bool:
    project = load_project(config.manifest, config.schema, config.root)
    reference = parse_po_collection(config.source_po)
    if not config.target_po.is_dir() or not any(config.target_po.rglob("*.po")):
        raise PipelineError(
            "没有找到 OmegaT 已译文档。请先在 OmegaT 中选择“项目 → 创建已译文档”。"
        )
    translated = parse_po_collection(config.target_po)
    validate_po_collections(reference, translated)
    project_tmx = config.target_po.parent / "omegat" / "project_save.tmx"
    notes = parse_omegat_notes(project_tmx, translated) if project_tmx.is_file() else {}
    filenames = sorted(reference)
    changes = plan_import(
        flatten_po_collection(translated), list(iter_translation_units(project))
    )
    changed_ids = {change.unit_id for change in changes}
    suggested_files = [
        filename
        for filename in filenames
        if any(entry.unit_id in changed_ids for entry in reference[filename])
    ]

    output("MTR 中文审校助手")
    output("已找到 OmegaT 创建的译文文档。")
    selected_files = _prompt_for_selection(
        filenames, suggested_files, input_fn, output
    )
    if not selected_files:
        output("已取消；正式文件没有改变。")
        return False

    write_reports(changes, config.preview_json, config.preview_markdown)
    outside = changes_outside_selection(changes, reference, selected_files)
    if outside:
        affected = sorted({Path(change.source_file).stem + ".po" for change in outside})
        raise PipelineError(
            "检测到未选择文件中的译文修改："
            + "、".join(affected)
            + "。请先在 OmegaT 中处理这些临时修改，或把已审完的文件一并选择。"
        )

    selected_unit_count = sum(len(translated[name]) for name in selected_files)
    changed_count = len(changes)
    unchanged_count = selected_unit_count - changed_count
    temporary_candidate: tempfile.TemporaryDirectory[str] | None = None
    confirmation = ""
    try:
        if changes:
            temporary_candidate = tempfile.TemporaryDirectory(
                prefix="mtr-omegat-preview-"
            )
            candidate_dir = Path(temporary_candidate.name) / "candidate"
            write_candidate(project, changes, candidate_dir, config.schema)

        output("\n检查摘要")
        output("  已审文件：" + "、".join(selected_files))
        output(f"  已审单元：{selected_unit_count}")
        output(f"  实际修改：{changed_count}")
        output(f"  确认保留旧译：{unchanged_count}")
        output(f"  含批注单元：{len(notes)}")
        output(f"  差异报告：{config.preview_markdown}")
        if changes:
            output("  候选版本：已在系统临时目录中验证，将自动清理")
        else:
            output("  没有文字变更，将跳过 YAML 回写，但仍可记录为已审。")

        confirmation = input_fn(
            "\n确认以上文件已完整审校并继续？[y/N]："
        ).strip().casefold()
    finally:
        if temporary_candidate is not None:
            temporary_candidate.cleanup()

    if confirmation not in {"y", "yes"}:
        output("已取消；正式文件和审校记录没有改变。")
        return False

    fresh_project = load_project(config.manifest, config.schema, config.root)
    fresh_translated = parse_po_collection(config.target_po)
    validate_po_collections(reference, fresh_translated)
    fresh_changes = plan_import(
        flatten_po_collection(fresh_translated),
        list(iter_translation_units(fresh_project)),
    )
    if fresh_changes != changes:
        raise PipelineError(
            "预览后 OmegaT 译文或正式 YAML 又发生了变化；本次没有回写。"
            "请重新启动助手生成新预览。"
        )
    fresh_notes = (
        parse_omegat_notes(project_tmx, fresh_translated)
        if project_tmx.is_file()
        else {}
    )
    if fresh_notes != notes:
        raise PipelineError(
            "预览后 OmegaT 批注又发生了变化；本次没有回写。"
            "请重新启动助手生成新预览。"
        )
    translated = fresh_translated
    notes = fresh_notes

    if changes:
        apply_changes_to_source(fresh_project, changes, config.schema)
        output(f"\n已安全回写 {changed_count} 处译文。")
    else:
        output("\n无需回写译文。")

    output("正在运行完整测试……")
    test_runner(config.root)

    applied_project = load_project(config.manifest, config.schema, config.root)
    try:
        glossary_entries = parse_glossary(config.glossary)
        findings = audit_terminology(
            list(iter_translation_units(applied_project)), glossary_entries
        )
        write_audit_reports(
            glossary_entries,
            findings,
            config.terminology_json,
            config.terminology_markdown,
        )
        errors = sum(item.severity == "error" for item in findings)
        warnings = sum(item.severity == "warning" for item in findings)
        output(
            f"术语审计完成（仅供参考）：{errors} 个错误，"
            f"{warnings} 个需人工判断的警告。"
        )
    except (OSError, PipelineError) as exc:
        message = f"术语审计未完成（不阻塞）：{exc}"
        config.terminology_json.parent.mkdir(parents=True, exist_ok=True)
        config.terminology_json.write_text(
            json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        config.terminology_markdown.write_text(
            "# MTR 术语审计\n\n" + message + "\n",
            encoding="utf-8",
            newline="\n",
        )
        output(message)

    output("正在生成最终阅读文档……")
    write_outputs(
        applied_project,
        config.output_json,
        config.output_markdown,
        output_schema_path=config.output_schema,
    )
    output(f"已生成：{config.output_markdown}、{config.output_json}")

    document_version = str(applied_project.manifest["document"]["version"])
    ledger = load_ledger(config.ledger, document_version)
    reviewed_ids = mark_reviewed(
        applied_project,
        ledger,
        reference,
        translated,
        files=selected_files,
        id_prefixes=[],
        unit_ids=[],
        notes=notes,
    )
    save_ledger(config.ledger, ledger)
    statuses = calculate_status(list(iter_translation_units(applied_project)), ledger)
    write_status_reports(statuses, config.status_json, config.status_markdown)
    reviewed_total = sum(item.status.startswith("reviewed-") for item in statuses)
    stale_total = sum(item.status == "stale" for item in statuses)

    output("\n完成。")
    output(f"  本批标记已审：{len(reviewed_ids)} 个单元")
    output(f"  总进度：{reviewed_total}/{len(statuses)}，失效：{stale_total}")
    output(f"  进度报告：{config.status_markdown}")
    output("请用 git diff 检查并提交 YAML、术语库和审校记录；不要手工修改 dist。")
    return True


def prepare_omegat_project(
    config: WorkflowConfig, *, output: OutputFunction = print
) -> bool:
    project = load_project(config.manifest, config.schema, config.root)
    parse_glossary(config.glossary)
    omegat_root = config.source_po.parent
    project_file = omegat_root / "omegat.project"

    if not project_file.exists():
        if omegat_root.exists() and any(omegat_root.iterdir()):
            raise PipelineError(
                f"OmegaT 目录已存在但项目文件缺失：{omegat_root}。"
                "为避免覆盖可恢复的工作，请先人工检查该目录。"
            )
        units = export_full(project, omegat_root, config.glossary)
        output("OmegaT 审校项目已创建。")
        output(f"  项目：{project_file}")
        output(f"  文件：{len(project.sources)} 个 PO")
        output(f"  单元：{len(units)}")
        return True

    if not project_file.is_file():
        raise PipelineError(f"OmegaT 项目路径不是文件：{project_file}")
    reference = parse_po_collection(config.source_po)
    expected_files = {f"{source.path.stem}.po" for source in project.sources}
    if set(reference) != expected_files:
        missing = sorted(expected_files - set(reference))
        extra = sorted(set(reference) - expected_files)
        details = []
        if missing:
            details.append("缺少 " + "、".join(missing))
        if extra:
            details.append("多出 " + "、".join(extra))
        raise PipelineError("OmegaT 源 PO 结构不完整：" + "；".join(details))

    current_units = {unit.unit_id: unit for unit in iter_translation_units(project)}
    reference_entries = flatten_po_collection(reference)
    reference_ids = {entry.unit_id for entry in reference_entries}
    if reference_ids != set(current_units):
        raise PipelineError("OmegaT 源 PO 的单元 ID 与当前 MTR 版本不一致")
    for entry in reference_entries:
        if entry.source != current_units[entry.unit_id].en:
            raise PipelineError(f"OmegaT 英文原文与当前版本不一致：{entry.unit_id}")

    output("现有 OmegaT 审校项目结构正常；没有覆盖任何译文、批注或术语。")
    output(f"  项目：{project_file}")
    output(f"  文件：{len(reference)} 个 PO")
    output(f"  单元：{len(reference_entries)}")
    output("请在 OmegaT 中继续审校；完成后选择“项目 → 创建已译文档”。")
    return False


def show_review_status(
    config: WorkflowConfig, *, output: OutputFunction = print
) -> list:
    project = load_project(config.manifest, config.schema, config.root)
    document_version = str(project.manifest["document"]["version"])
    ledger = load_ledger(config.ledger, document_version)
    statuses = calculate_status(list(iter_translation_units(project)), ledger)
    write_status_reports(statuses, config.status_json, config.status_markdown)
    counts = {
        name: sum(item.status == name for item in statuses)
        for name in (
            "reviewed-unchanged",
            "reviewed-modified",
            "stale",
            "unreviewed",
        )
    }
    output("MTR 审校进度")
    output(f"  总单元：{len(statuses)}")
    output(f"  已审未改：{counts['reviewed-unchanged']}")
    output(f"  已审有改：{counts['reviewed-modified']}")
    output(f"  失效：{counts['stale']}")
    output(f"  未审：{counts['unreviewed']}")
    output(f"  详细报告：{config.status_markdown}")
    return statuses


def run_menu(
    config: WorkflowConfig,
    *,
    input_fn: InputFunction = input,
    output: OutputFunction = print,
    test_runner: TestRunner = run_pytest,
) -> int:
    output("MTR 中文翻译工作流")
    output("  1. 准备或继续 OmegaT 审校")
    output("  2. 完成审校并生成最终文档")
    output("  3. 查看审校进度")
    output("  q. 退出")
    while True:
        choice = input_fn("\n请选择：").strip().casefold()
        if choice == "1":
            prepare_omegat_project(config, output=output)
            return 0
        if choice == "2":
            return 0 if run_workflow(
                config,
                input_fn=input_fn,
                output=output,
                test_runner=test_runner,
            ) else 2
        if choice == "3":
            show_review_status(config, output=output)
            return 0
        if choice in {"q", "quit", "退出"}:
            output("已退出；没有修改任何文件。")
            return 0
        output("输入错误：请输入 1、2、3 或 q。")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="交互式完成 MTR OmegaT 审校回写流程")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="仓库根目录；通过审校助手.cmd 启动时无需指定",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return run_menu(default_config(args.project_root))
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except (OSError, PipelineError) as exc:
        print(f"\n审校助手停止：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
