from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .core import PipelineError, Project, load_project
from .omegat import TranslationUnit, iter_translation_units


_FIELD_RE = re.compile(r"^(msgctxt|msgid|msgstr)\s+(\".*\")$")


@dataclass(frozen=True)
class PoEntry:
    unit_id: str
    source: str
    target: str


@dataclass(frozen=True)
class TranslationChange:
    unit_id: str
    kind: str
    source_file: str
    source: str
    before: str
    after: str


def _decode_po_string(value: str, *, path: Path, line_number: int) -> str:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PipelineError(
            f"Invalid PO string in {path} at line {line_number}: {exc.msg}"
        ) from exc
    if not isinstance(decoded, str):
        raise PipelineError(f"PO value is not a string in {path} at line {line_number}")
    return decoded


def parse_po(path: Path) -> list[PoEntry]:
    entries: list[PoEntry] = []
    fields: dict[str, str] = {}
    active_field: str | None = None

    def finish_entry(line_number: int) -> None:
        nonlocal fields, active_field
        if not fields:
            return
        missing = [name for name in ("msgctxt", "msgid", "msgstr") if name not in fields]
        if missing:
            raise PipelineError(
                f"Incomplete PO entry ending at line {line_number} in {path}: "
                + ", ".join(missing)
            )
        entries.append(PoEntry(fields["msgctxt"], fields["msgid"], fields["msgstr"]))
        fields = {}
        active_field = None

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            finish_entry(line_number)
            continue
        if line.startswith("#"):
            continue
        if match := _FIELD_RE.fullmatch(line):
            active_field = match.group(1)
            if active_field in fields:
                raise PipelineError(
                    f"Duplicate {active_field} in {path} at line {line_number}"
                )
            fields[active_field] = _decode_po_string(
                match.group(2), path=path, line_number=line_number
            )
            continue
        if line.startswith('"') and line.endswith('"') and active_field is not None:
            fields[active_field] += _decode_po_string(
                line, path=path, line_number=line_number
            )
            continue
        raise PipelineError(f"Unsupported PO syntax in {path} at line {line_number}: {line}")
    finish_entry(len(lines) + 1)

    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in entries:
        if entry.unit_id in seen:
            duplicates.append(entry.unit_id)
        seen.add(entry.unit_id)
    if duplicates:
        raise PipelineError("Duplicate PO unit ID(s): " + ", ".join(sorted(set(duplicates))))
    if not entries:
        raise PipelineError(f"No translation entries found in {path}")
    return entries


def parse_po_collection(path: Path) -> dict[str, list[PoEntry]]:
    if path.is_file():
        return {path.name: parse_po(path)}
    if not path.is_dir():
        raise PipelineError(f"PO path does not exist: {path}")
    po_paths = sorted(item for item in path.rglob("*.po") if item.is_file())
    if not po_paths:
        raise PipelineError(f"No PO files found in {path}")
    return {item.relative_to(path).as_posix(): parse_po(item) for item in po_paths}


def flatten_po_collection(collection: dict[str, list[PoEntry]]) -> list[PoEntry]:
    entries = [entry for file_entries in collection.values() for entry in file_entries]
    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in entries:
        if entry.unit_id in seen:
            duplicates.append(entry.unit_id)
        seen.add(entry.unit_id)
    if duplicates:
        raise PipelineError(
            "Duplicate PO unit ID(s) across files: " + ", ".join(sorted(set(duplicates)))
        )
    return entries


def validate_po_collections(
    reference: dict[str, list[PoEntry]], translated: dict[str, list[PoEntry]]
) -> None:
    missing_files = [name for name in reference if name not in translated]
    unexpected_files = [name for name in translated if name not in reference]
    if missing_files:
        raise PipelineError("Translated PO directory is missing file(s): " + ", ".join(missing_files))
    if unexpected_files:
        raise PipelineError(
            "Translated PO directory contains unexpected file(s): "
            + ", ".join(unexpected_files)
        )
    for name in reference:
        validate_po_pair(reference[name], translated[name])
    flatten_po_collection(reference)
    flatten_po_collection(translated)


def validate_po_pair(reference: list[PoEntry], translated: list[PoEntry]) -> None:
    reference_by_id = {entry.unit_id: entry for entry in reference}
    translated_by_id = {entry.unit_id: entry for entry in translated}
    missing = [entry.unit_id for entry in reference if entry.unit_id not in translated_by_id]
    unexpected = [entry.unit_id for entry in translated if entry.unit_id not in reference_by_id]
    if missing:
        raise PipelineError("Translated PO is missing unit ID(s): " + ", ".join(missing))
    if unexpected:
        raise PipelineError(
            "Translated PO contains unexpected unit ID(s): " + ", ".join(unexpected)
        )
    changed_sources = [
        entry.unit_id
        for entry in translated
        if entry.source != reference_by_id[entry.unit_id].source
    ]
    if changed_sources:
        raise PipelineError(
            "Translated PO changed English source for unit(s): " + ", ".join(changed_sources)
        )


def plan_import(entries: list[PoEntry], units: list[TranslationUnit]) -> list[TranslationChange]:
    by_id = {unit.unit_id: unit for unit in units}
    unknown = [entry.unit_id for entry in entries if entry.unit_id not in by_id]
    if unknown:
        raise PipelineError("Unknown PO unit ID(s): " + ", ".join(unknown))

    source_mismatches = [
        entry.unit_id for entry in entries if entry.source != by_id[entry.unit_id].en
    ]
    if source_mismatches:
        raise PipelineError(
            "PO English source differs from the current project for unit(s): "
            + ", ".join(source_mismatches)
        )

    changes: list[TranslationChange] = []
    for entry in entries:
        unit = by_id[entry.unit_id]
        if entry.target == unit.zh:
            continue
        changes.append(
            TranslationChange(
                unit_id=unit.unit_id,
                kind=unit.kind,
                source_file=unit.source_file,
                source=unit.en,
                before=unit.zh,
                after=entry.target,
            )
        )
    return changes


def write_reports(
    changes: list[TranslationChange], json_path: Path, markdown_path: Path
) -> None:
    payload = {
        "status": "preview-only",
        "changeCount": len(changes),
        "changes": [asdict(change) for change in changes],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    lines = [
        "# OmegaT 回写预览",
        "",
        "此报告仅供审阅；尚未修改正式 YAML。",
        "",
        f"检测到 {len(changes)} 处中文译文变更。",
    ]
    for change in changes:
        lines.extend(
            (
                "",
                f"## `{change.unit_id}`",
                "",
                f"- 类型：`{change.kind}`",
                f"- 来源：`{change.source_file}`",
                "",
                "**修改前**",
                "",
                change.before,
                "",
                "**修改后**",
                "",
                change.after,
            )
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def _node_mapping(node: Node, *, label: str) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise PipelineError(f"Expected YAML mapping for {label}")
    result: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode):
            raise PipelineError(f"Expected scalar YAML key for {label}")
        result[key.value] = value
    return result


def _node_sequence(node: Node, *, label: str) -> list[Node]:
    if not isinstance(node, SequenceNode):
        raise PipelineError(f"Expected YAML sequence for {label}")
    return list(node.value)


def _node_scalar(node: Node, *, label: str) -> ScalarNode:
    if not isinstance(node, ScalarNode):
        raise PipelineError(f"Expected YAML scalar for {label}")
    return node


def _localized_zh_nodes(root: Node) -> Iterable[tuple[str, ScalarNode]]:
    chapter = _node_mapping(_node_mapping(root, label="root")["chapter"], label="chapter")
    chapter_id = _node_scalar(chapter["id"], label="chapter id").value
    yield chapter_id, _node_scalar(chapter["zh"], label=f"{chapter_id} zh")
    if "groups" in chapter:
        for group in _node_sequence(chapter["groups"], label="chapter groups"):
            yield from _group_zh_nodes(group)
    if "sections" in chapter:
        for section_node in _node_sequence(chapter["sections"], label="sections"):
            section = _node_mapping(section_node, label="section")
            section_id = _node_scalar(section["id"], label="section id").value
            yield section_id, _node_scalar(section["zh"], label=f"{section_id} zh")
            for group in _node_sequence(section["groups"], label=f"{section_id} groups"):
                yield from _group_zh_nodes(group)


def _group_zh_nodes(group_node: Node) -> Iterable[tuple[str, ScalarNode]]:
    group = _node_mapping(group_node, label="group")
    group_type = _node_scalar(group["type"], label="group type").value
    for block_node in _node_sequence(group["blocks"], label="group blocks"):
        block = _node_mapping(block_node, label="block")
        block_id = _node_scalar(block["id"], label="block id").value
        localized = (
            _node_mapping(block["alt"], label=f"{block_id} alt")
            if group_type == "image"
            else block
        )
        yield block_id, _node_scalar(localized["zh"], label=f"{block_id} zh")
        if "extras" in block:
            for index, extra_node in enumerate(
                _node_sequence(block["extras"], label=f"{block_id} extras")
            ):
                extra = _node_mapping(extra_node, label=f"{block_id} extra {index}")
                unit_id = f"{block_id}::extra:{index}"
                yield unit_id, _node_scalar(extra["zh"], label=f"{unit_id} zh")


def _encode_yaml_scalar(value: str, original: ScalarNode) -> str:
    if original.style in ("|", ">") or "\n" in value:
        indent = " " * max(original.start_mark.column - 2, 0)
        indicator = "|" if value.endswith("\n") else "|-"
        content = value[:-1] if value.endswith("\n") else value
        return indicator + "\n" + "\n".join(
            indent + line if line else "" for line in content.split("\n")
        )
    return json.dumps(value, ensure_ascii=False)


def patch_yaml_translations(text: str, changes: list[TranslationChange]) -> str:
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise PipelineError(f"Unable to parse YAML for minimal-diff update: {exc}") from exc
    if root is None:
        raise PipelineError("Unable to patch an empty YAML document")
    nodes = dict(_localized_zh_nodes(root))
    replacements: list[tuple[int, int, str]] = []
    for change in changes:
        node = nodes.get(change.unit_id)
        if node is None:
            raise PipelineError(f"Unable to locate YAML target for {change.unit_id}")
        if node.value != change.before:
            raise PipelineError(f"YAML changed after preview for {change.unit_id}")
        original_scalar = text[node.start_mark.index : node.end_mark.index]
        replacement = _encode_yaml_scalar(change.after, node)
        if original_scalar.endswith("\r\n") and not replacement.endswith("\r\n"):
            replacement += "\r\n"
        elif original_scalar.endswith("\n") and not replacement.endswith("\n"):
            replacement += "\n"
        replacements.append((node.start_mark.index, node.end_mark.index, replacement))
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _changes_by_file(
    project: Project, changes: list[TranslationChange]
) -> dict[Path, list[TranslationChange]]:
    by_file: dict[Path, list[TranslationChange]] = {}
    for change in changes:
        source_path = Path(change.source_file).resolve()
        by_file.setdefault(source_path, []).append(change)
    known_files = {source.path.resolve() for source in project.sources}
    unknown_files = sorted(str(path) for path in by_file if path not in known_files)
    if unknown_files:
        raise PipelineError("Import refers to unknown source file(s): " + ", ".join(unknown_files))
    return by_file


def write_candidate(
    project: Project,
    changes: list[TranslationChange],
    output_dir: Path,
    schema_path: Path,
) -> Path:
    source_dir = project.manifest_path.parent.resolve()
    destination = output_dir.resolve()
    if destination == source_dir or source_dir in destination.parents:
        raise PipelineError("Candidate directory must be outside the formal source directory")
    if destination.exists():
        raise PipelineError(f"Candidate directory already exists: {destination}")

    by_file = _changes_by_file(project, changes)

    shutil.copytree(source_dir, destination)
    for source in project.sources:
        source_path = source.path.resolve()
        file_changes = by_file.get(source_path)
        if not file_changes:
            continue
        relative_path = source_path.relative_to(source_dir)
        candidate_path = destination / relative_path
        candidate_path.write_text(
            patch_yaml_translations(
                source.path.read_text(encoding="utf-8"), file_changes
            ),
            encoding="utf-8",
            newline="\n",
        )

    candidate_manifest = destination / project.manifest_path.name
    load_project(candidate_manifest, schema_path, project.root)
    return candidate_manifest


def apply_changes_to_source(
    project: Project,
    changes: list[TranslationChange],
    schema_path: Path,
) -> None:
    by_file = _changes_by_file(project, changes)
    if not by_file:
        return
    with tempfile.TemporaryDirectory(prefix="mtr-omegat-apply-") as temporary:
        candidate_dir = Path(temporary) / "candidate"
        candidate_manifest = write_candidate(project, changes, candidate_dir, schema_path)
        candidate_root = candidate_manifest.parent
        source_root = project.manifest_path.parent.resolve()
        originals: dict[Path, str] = {}
        try:
            for source_path in by_file:
                relative_path = source_path.relative_to(source_root)
                originals[source_path] = source_path.read_text(encoding="utf-8")
                source_path.write_text(
                    (candidate_root / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                    newline="\n",
                )
            load_project(project.manifest_path, schema_path, project.root)
        except Exception:
            for source_path, original in originals.items():
                source_path.write_text(original, encoding="utf-8", newline="\n")
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an OmegaT PO and preview YAML changes")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-po", type=Path, required=True)
    parser.add_argument("--po", type=Path, required=True)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    destination_group = parser.add_mutually_exclusive_group()
    destination_group.add_argument(
        "--candidate-dir",
        type=Path,
        help="Write changes to a new isolated YAML tree; never modifies the manifest source directory",
    )
    destination_group.add_argument(
        "--apply",
        action="store_true",
        help="Apply minimal-diff changes to formal YAML after temporary validation",
    )
    parser.add_argument(
        "--expected-change-count",
        type=int,
        help="Required with --apply; must exactly match the freshly calculated change count",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        project = load_project(args.manifest, args.schema, args.project_root)
        reference_collection = parse_po_collection(args.source_po)
        translated_collection = parse_po_collection(args.po)
        validate_po_collections(reference_collection, translated_collection)
        entries = flatten_po_collection(translated_collection)
        changes = plan_import(entries, list(iter_translation_units(project)))
        write_reports(changes, args.json_report, args.markdown_report)
        candidate_manifest = None
        if args.candidate_dir is not None:
            candidate_manifest = write_candidate(
                project, changes, args.candidate_dir, args.schema
            )
        if args.apply:
            if args.expected_change_count is None:
                raise PipelineError("--apply requires --expected-change-count")
            if args.expected_change_count != len(changes):
                raise PipelineError(
                    f"Expected {args.expected_change_count} change(s), but found {len(changes)}"
                )
            apply_changes_to_source(project, changes, args.schema)
    except (OSError, PipelineError) as exc:
        print(f"error: {exc}")
        return 1
    source_state = "changes applied" if args.apply else "source YAML unchanged"
    print(
        f"Validated {len(entries)} PO entries; found {len(changes)} change(s); "
        f"{source_state}"
    )
    if candidate_manifest is not None:
        print(f"Wrote validated candidate manifest: {candidate_manifest}")
    if args.apply:
        print(f"Applied {len(changes)} minimal-diff change(s) to formal YAML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
