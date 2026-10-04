from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from .core import PipelineError, Project, SourceFile, load_project


@dataclass(frozen=True)
class TranslationUnit:
    unit_id: str
    en: str
    zh: str
    kind: str
    source_file: str


# A deliberately small set that exercises ordinary paragraphs, annotations,
# Markdown, tables, image alt text, and historically suspicious translations.
PILOT_UNIT_IDS = (
    "mtr-introduction",
    "mtr-introduction-b001",
    "mtr-introduction-b006",
    "mtr-introduction-b007",
    "mtr-1.1",
    "mtr-1.1-b002",
    "mtr-1.1-b002::extra:0",
    "mtr-1.1-b003",
    "mtr-1.1-b003::extra:0",
    "mtr-1.7",
    "mtr-1.7-b001",
    "mtr-2.3-b002",
    "mtr-2.3-b002::extra:0",
    "mtr-2.5",
    "mtr-2.5-b001",
    "mtr-2.5-b001::extra:0",
    "mtr-2.5-b002",
    "mtr-2.5-b003",
    "mtr-3.1-b002",
    "mtr-3.1-b003",
    "mtr-3.1-b004",
    "mtr-4.1",
    "mtr-4.1-b001",
    "mtr-4.1-b002",
    "mtr-appendix-b-b018",
    "mtr-appendix-c-b015",
    "mtr-appendix-c-b024",
    "mtr-appendix-f-b002",
)


def _translation_unit(
    unit_id: str,
    localized: dict[str, Any],
    kind: str,
    source_file: Path,
) -> TranslationUnit:
    return TranslationUnit(
        unit_id=unit_id,
        en=str(localized["en"]),
        zh=str(localized["zh"]),
        kind=kind,
        source_file=source_file.as_posix(),
    )


def iter_translation_units(project: Project) -> Iterable[TranslationUnit]:
    for source in project.sources:
        yield from iter_source_translation_units(source)


def iter_source_translation_units(source: SourceFile) -> Iterable[TranslationUnit]:
    chapter = source.data["chapter"]
    yield _translation_unit(chapter["id"], chapter, "chapter-title", source.path)
    for group in chapter.get("groups", []):
        yield from _iter_group_units(group, source.path)
    for section in chapter.get("sections", []):
        yield _translation_unit(section["id"], section, "section-title", source.path)
        for group in section["groups"]:
            yield from _iter_group_units(group, source.path)


def _iter_group_units(group: dict[str, Any], source_file: Path) -> Iterable[TranslationUnit]:
    group_type = group["type"]
    for block in group["blocks"]:
        if group_type == "image":
            yield _translation_unit(block["id"], block["alt"], "image-alt", source_file)
        else:
            yield _translation_unit(block["id"], block, group_type, source_file)
        for index, extra in enumerate(block.get("extras", [])):
            yield _translation_unit(
                f"{block['id']}::extra:{index}",
                extra,
                "annotation",
                source_file,
            )


def select_units(project: Project, unit_ids: Iterable[str]) -> list[TranslationUnit]:
    by_id = {unit.unit_id: unit for unit in iter_translation_units(project)}
    requested = list(unit_ids)
    missing = [unit_id for unit_id in requested if unit_id not in by_id]
    if missing:
        raise PipelineError("Unknown OmegaT unit ID(s): " + ", ".join(missing))
    return [by_id[unit_id] for unit_id in requested]


def _source_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _po_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def write_po(units: Iterable[TranslationUnit], output_path: Path) -> None:
    entries: list[str] = []
    for unit in units:
        entries.append(
            "\n".join(
                (
                    f"#. kind: {unit.kind}",
                    f"#. source-file: {unit.source_file}",
                    f"#. source-sha256: {_source_hash(unit.en)}",
                    "#. status: legacy-unreviewed",
                    f"msgctxt {_po_quote(unit.unit_id)}",
                    f"msgid {_po_quote(unit.en)}",
                    f"msgstr {_po_quote(unit.zh)}",
                )
            )
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(entries) + "\n", encoding="utf-8")


def write_omegat_project(output_path: Path, glossary_path: Path) -> None:
    try:
        configured_glossary_dir = os.path.relpath(
            glossary_path.parent.resolve(), output_path.parent.resolve()
        ).replace("\\", "/")
    except ValueError:
        configured_glossary_dir = glossary_path.parent.resolve().as_posix()
    root = ET.Element("omegat")
    project = ET.SubElement(root, "project", {"version": "1.0"})
    values = (
        ("source_dir", "source"),
        ("target_dir", "target"),
        ("tm_dir", "tm"),
        ("glossary_dir", configured_glossary_dir),
        ("glossary_file", glossary_path.name),
        ("dictionary_dir", "dictionary"),
        ("source_lang", "EN-US"),
        ("target_lang", "ZH-CN"),
        ("source_tok", "org.omegat.tokenizer.LuceneEnglishTokenizer"),
        ("target_tok", "org.omegat.tokenizer.LuceneSmartChineseTokenizer"),
        ("sentence_seg", "false"),
        ("support_default_translations", "true"),
        ("remove_tags", "false"),
        ("external_command", ""),
    )
    for name, value in values:
        node = ET.SubElement(project, name)
        node.text = value
    ET.SubElement(project, "repositories")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def write_omegat_filters(output_path: Path) -> None:
    root = ET.Element(
        "filters",
        {
            "removeTags": "false",
            "removeSpacesNonseg": "false",
            "preserveSpaces": "true",
            "ignoreFileContext": "false",
        },
    )
    filter_node = ET.SubElement(
        root,
        "filter",
        {
            "className": "org.omegat.filters2.po.PoFilter",
            "enabled": "true",
        },
    )
    ET.SubElement(
        filter_node,
        "files",
        {
            "sourceFilenameMask": "*.po",
            "targetFilenamePattern": "${filename}",
            "sourceEncoding": "UTF-8",
            "targetEncoding": "UTF-8",
        },
    )
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def write_project_readme(
    output_path: Path, *, unit_count: int, file_count: int, scope: str, glossary_path: Path
) -> None:
    heading = "往返测试" if scope == "pilot" else "完整审校项目"
    scope_text = "代表性" if scope == "pilot" else "全部"
    output_path.write_text(
        f"# MTR 中文修订 OmegaT {heading}\n\n"
        f"本项目包含 {file_count} 个 PO 文件、{unit_count} 个{scope_text}翻译单元，"
        "所有中文均为现有旧译，语义上属于待审校材料；OmegaT 会将其视为已有译文。\n\n"
        "## 操作步骤\n\n"
        "1. 在 OmegaT 中选择“项目 → 打开”，选择本目录。\n"
        "2. 确认编辑器中能看到英文原文和自动载入的现有中文译文。\n"
        "3. 使用 Ctrl+F 可跨全部 PO 搜索英文或中文；搜索结果可显示所属文件。\n"
        "4. 使用 Ctrl+Shift+G 可向仓库内的共享可写词汇表添加术语。\n"
        "5. 修改并保存所需中文单元。\n"
        "6. 选择“项目 → 创建已译文档”。\n"
        "7. 回到仓库根目录，双击“审校助手.cmd”，选择已完整审完的 PO。\n\n"
        "源 PO 使用英文 msgid 与现有中文 msgstr；OmegaT 会把两者分别显示为原文和可编辑译文。"
        "每个 msgctxt 都是稳定的 MTR 单元 ID，项目使用段落级分段，"
        "以保持每个 MTR block 与 PO 条目一一对应。注释中的 legacy-unreviewed "
        "表示现有中文仍是待审材料，不代表译文已经确认。\n\n"
        f"共享术语库：`{glossary_path.as_posix()}`。该文件受 Git 管理，"
        "OmegaT 界面新增的术语会直接写入这里。\n",
        encoding="utf-8",
    )


def _prepare_project_directories(output_dir: Path) -> None:
    for dirname in ("source", "target", "tm", "dictionary", "omegat"):
        (output_dir / dirname).mkdir(parents=True, exist_ok=True)


def export_pilot(
    project: Project, output_dir: Path, glossary_path: Path
) -> list[TranslationUnit]:
    if not glossary_path.is_file():
        raise PipelineError(f"Glossary file does not exist: {glossary_path}")
    units = select_units(project, PILOT_UNIT_IDS)
    _prepare_project_directories(output_dir)
    write_po(units, output_dir / "source" / "mtr-zh-pilot.po")
    write_omegat_project(output_dir / "omegat.project", glossary_path)
    write_omegat_filters(output_dir / "omegat" / "filters.xml")
    write_project_readme(
        output_dir / "README.md",
        unit_count=len(units),
        file_count=1,
        scope="pilot",
        glossary_path=glossary_path,
    )
    return units


def export_full(
    project: Project, output_dir: Path, glossary_path: Path
) -> list[TranslationUnit]:
    if not glossary_path.is_file():
        raise PipelineError(f"Glossary file does not exist: {glossary_path}")
    _prepare_project_directories(output_dir)
    units: list[TranslationUnit] = []
    for source in project.sources:
        source_units = list(iter_source_translation_units(source))
        units.extend(source_units)
        write_po(source_units, output_dir / "source" / f"{source.path.stem}.po")
    write_omegat_project(output_dir / "omegat.project", glossary_path)
    write_omegat_filters(output_dir / "omegat" / "filters.xml")
    write_project_readme(
        output_dir / "README.md",
        unit_count=len(units),
        file_count=len(project.sources),
        scope="full",
        glossary_path=glossary_path,
    )
    return units


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export an OmegaT PO revision project")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=("full", "pilot"), default="full")
    parser.add_argument(
        "--glossary",
        type=Path,
        default=Path("terminology/mtr-glossary.txt"),
        help="Repository-owned writable OmegaT glossary",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        project = load_project(args.manifest, args.schema, args.project_root)
        glossary_path = args.glossary
        if not glossary_path.is_absolute():
            glossary_path = args.project_root / glossary_path
        if args.scope == "pilot":
            units = export_pilot(project, args.output_dir, glossary_path)
        else:
            units = export_full(project, args.output_dir, glossary_path)
    except PipelineError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote OmegaT {args.scope} project with {len(units)} units to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
