from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator, FormatChecker


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RELATIVE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?!data:|https?://)[^)]+\)")
VERSION_NOTES_PATH = Path("src/mtr/version-notes.md")
OUTPUT_SCHEMA_PATH = Path("schema/mtr-output.schema.json")
OUTPUT_IMAGE_RE = re.compile(
    r"^!\[(?P<alt>[^\]\r\n]+)\]\(data:image/png;base64,"
    r"(?P<payload>[A-Za-z0-9+/]+={0,2})\)$"
)
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")


class PipelineError(RuntimeError):
    """Raised when source content cannot be validated or built safely."""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class Project:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    sources: tuple[SourceFile, ...]


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PipelineError(f"Unable to read YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"YAML root must be an object: {path}")
    return value


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Unable to read JSON Schema {path}: {exc}") from exc
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"JSON root must be an object: {path}")
    return value


def validate_document(document: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    details = []
    for error in errors[:8]:
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        details.append(f"{path}: {error.message}")
    if len(errors) > 8:
        details.append(f"... and {len(errors) - 8} more error(s)")
    raise PipelineError(f"Schema validation failed for {label}:\n" + "\n".join(details))


def _iter_output_contents(document: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for index, content in enumerate(document["intro"]["contents"]):
        yield f"intro.contents.{index}", content
    for chapter_index, chapter in enumerate(document["main"]):
        for section_index, section in enumerate(chapter["subrules"]):
            for content_index, content in enumerate(section["contents"]):
                yield (
                    f"main.{chapter_index}.subrules.{section_index}.contents.{content_index}",
                    content,
                )
    for appendix_index, appendix in enumerate(document["appendices"]):
        for content_index, content in enumerate(appendix["contents"]):
            yield f"appendices.{appendix_index}.contents.{content_index}", content


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped[1:-1])]


def _validate_output_markdown(value: str, label: str) -> bytes | None:
    if value.startswith("!["):
        match = OUTPUT_IMAGE_RE.fullmatch(value)
        if match is None:
            raise PipelineError(f"Invalid inline PNG Markdown in {label}")
        try:
            raw = base64.b64decode(match.group("payload"), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PipelineError(f"Invalid Base64 image payload in {label}") from exc
        if not raw.startswith(PNG_SIGNATURE):
            raise PipelineError(f"Inline image is not a PNG in {label}")
        return raw

    lines = value.splitlines()
    if not lines or not lines[0].lstrip().startswith("|"):
        return None
    if len(lines) < 3:
        raise PipelineError(f"Incomplete Markdown table in {label}: expected a header and data row")
    rows = [_table_cells(line) for line in lines]
    if any(row is None for row in rows):
        raise PipelineError(f"Incomplete Markdown table in {label}: rows must be contiguous")
    assert all(row is not None for row in rows)
    cell_count = len(rows[0])
    if cell_count == 0 or any(len(row) != cell_count for row in rows):
        raise PipelineError(f"Invalid Markdown table column count in {label}")
    if not all(TABLE_SEPARATOR_RE.fullmatch(cell) for cell in rows[1]):
        raise PipelineError(f"Invalid Markdown table separator in {label}")
    return None


def validate_built_output(document: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    validate_document(document, schema, label)

    main_chapters = [chapter["chapter"] for chapter in document["main"]]
    expected_main = [f"{number}." for number in range(1, 11)]
    if main_chapters != expected_main:
        raise PipelineError(f"Unexpected main chapter order in {label}: {main_chapters}")
    appendix_chapters = [appendix["chapter"] for appendix in document["appendices"]]
    expected_appendices = [f"Appendix {letter}" for letter in "ABCDEF"]
    if appendix_chapters != expected_appendices:
        raise PipelineError(f"Unexpected appendix order in {label}: {appendix_chapters}")

    seen_ids: set[str] = set()
    duplicates: set[str] = set()
    for location, content in _iter_output_contents(document):
        content_id = content["id"]
        if content_id in seen_ids:
            duplicates.add(content_id)
        seen_ids.add(content_id)

        en_image = _validate_output_markdown(content["en"], f"{location}.en")
        zh_image = _validate_output_markdown(content["zh"], f"{location}.zh")
        if (en_image is None) != (zh_image is None):
            raise PipelineError(f"Image languages do not match in {location}")
        if en_image is not None and en_image != zh_image:
            raise PipelineError(f"Image payloads do not match in {location}")

    if duplicates:
        raise PipelineError("Duplicate published content IDs: " + ", ".join(sorted(duplicates)))

    toc = document["intro"]["contents"][1]["zh"]
    for chapter in document["main"]:
        route = f"/mtr/{chapter['chapter'].rstrip('.')}"
        if f"]({route})" not in toc:
            raise PipelineError(f"Missing table-of-contents route in {label}: {route}")
        for section in chapter["subrules"]:
            section_route = f"{route}#{section['chapter']}"
            if f"]({section_route})" not in toc:
                raise PipelineError(f"Missing table-of-contents route in {label}: {section_route}")
    for appendix in document["appendices"]:
        route = f"/mtr/{appendix['chapter'].lower().replace(' ', '-')}"
        if f"]({route})" not in toc:
            raise PipelineError(f"Missing table-of-contents route in {label}: {route}")


def validate_output_file(json_path: Path | str, schema_path: Path | str) -> dict[str, Any]:
    json_path = Path(json_path).resolve()
    schema_path = Path(schema_path).resolve()
    document = _read_json(json_path)
    schema = _read_schema(schema_path)
    validate_built_output(document, schema, str(json_path))
    return document


def _ensure_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PipelineError(f"{label} escapes project root: {path}") from exc
    return resolved


def _iter_groups(chapter: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from chapter.get("groups", [])
    for section in chapter.get("sections", []):
        yield from section["groups"]


def _iter_ids(chapter: dict[str, Any]) -> Iterable[str]:
    yield chapter["id"]
    for group in chapter.get("groups", []):
        yield group["id"]
        for block in group["blocks"]:
            yield block["id"]
    for section in chapter.get("sections", []):
        yield section["id"]
        for group in section["groups"]:
            yield group["id"]
            for block in group["blocks"]:
                yield block["id"]


def _validate_content(project_root: Path, sources: Iterable[SourceFile], require_complete: bool) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for source in sources:
        chapter = source.data["chapter"]
        for content_id in _iter_ids(chapter):
            if content_id in seen:
                duplicates.add(content_id)
            seen.add(content_id)

        for group in _iter_groups(chapter):
            for block in group["blocks"]:
                if group["type"] == "image":
                    asset = _ensure_within(project_root / block["asset"], project_root, "Asset path")
                    if not asset.is_file():
                        raise PipelineError(f"Missing image asset for {block['id']}: {asset}")
                    if asset.read_bytes()[: len(PNG_SIGNATURE)] != PNG_SIGNATURE:
                        raise PipelineError(f"Asset is not a PNG for {block['id']}: {asset}")
                    if require_complete and (not block["alt"]["en"].strip() or not block["alt"]["zh"].strip()):
                        raise PipelineError(f"Image alt text is incomplete: {block['id']}")
                    continue

                if require_complete and (not block["en"].strip() or not block["zh"].strip()):
                    raise PipelineError(f"Translation is incomplete: {block['id']}")
                for extra in block.get("extras", []):
                    if require_complete and (not extra["en"].strip() or not extra["zh"].strip()):
                        raise PipelineError(f"Annotation is incomplete: {block['id']}")

    if duplicates:
        raise PipelineError("Duplicate content IDs: " + ", ".join(sorted(duplicates)))


def load_project(
    manifest_path: Path | str,
    schema_path: Path | str,
    project_root: Path | str,
    *,
    require_complete: bool = True,
) -> Project:
    manifest_path = Path(manifest_path).resolve()
    schema_path = Path(schema_path).resolve()
    project_root = Path(project_root).resolve()
    schema = _read_schema(schema_path)
    manifest = _read_yaml(manifest_path)
    validate_document(manifest, schema, str(manifest_path))

    sources: list[SourceFile] = []
    for relative_name in manifest["files"]:
        source_path = _ensure_within(manifest_path.parent / relative_name, manifest_path.parent, "Manifest file")
        source_data = _read_yaml(source_path)
        validate_document(source_data, schema, str(source_path))
        sources.append(SourceFile(path=source_path, data=source_data))

    _validate_content(project_root, sources, require_complete)
    return Project(
        root=project_root,
        manifest_path=manifest_path,
        manifest=manifest,
        sources=tuple(sources),
    )


def assemble_group(group: dict[str, Any], language: str) -> str:
    if group["type"] == "image":
        return ""
    defaults = {
        "paragraph": {"en": " ", "zh": ""},
        "list": {"en": "\n", "zh": "\n"},
        "table": {"en": "\n", "zh": "\n"},
        "markdown": {"en": "\n", "zh": "\n"},
    }
    blocks = group["blocks"]
    parts: list[str] = []
    for index, block in enumerate(blocks):
        parts.append(block[language])
        if index == len(blocks) - 1:
            continue
        override = block.get("joinAfter", {}).get(language)
        parts.append(defaults[group["type"]][language] if override is None else override)
    return "".join(parts)


def _image_markdown(project: Project, block: dict[str, Any], language: str) -> str:
    asset = _ensure_within(project.root / block["asset"], project.root, "Asset path")
    raw = asset.read_bytes()
    if raw[: len(PNG_SIGNATURE)] != PNG_SIGNATURE:
        raise PipelineError(f"Asset is not a PNG: {asset}")
    payload = base64.b64encode(raw).decode("ascii")
    return f"![{block['alt'][language]}](data:image/png;base64,{payload})"


def _render_block(project: Project, group: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    if group["type"] == "image":
        result: dict[str, Any] = {
            "id": block["id"],
            "en": _image_markdown(project, block, "en"),
            "zh": _image_markdown(project, block, "zh"),
        }
    else:
        result = {"id": block["id"], "en": block["en"], "zh": block["zh"]}
    if block.get("extras"):
        result["extras"] = block["extras"]
    return result


def _render_group(project: Project, group: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = group["blocks"]
    if group["type"] != "table" or len(blocks) == 1:
        return [_render_block(project, group, block) for block in blocks]

    annotated = [block["id"] for block in blocks if block.get("extras")]
    if annotated:
        raise PipelineError(
            f"Multi-block table annotations cannot be published without losing row association: "
            f"{group['id']} ({', '.join(annotated)})"
        )
    return [
        {
            "id": blocks[0]["id"],
            "en": assemble_group(group, "en"),
            "zh": assemble_group(group, "zh"),
        }
    ]


def _flatten_groups(project: Project, groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        rendered
        for group in groups
        for rendered in _render_group(project, group)
    ]


def _build_chapter(project: Project, chapter: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "chapter": chapter["chapter"],
        "en": chapter["en"],
        "zh": chapter["zh"],
    }
    if chapter.get("groups"):
        result["contents"] = _flatten_groups(project, chapter["groups"])
    if chapter.get("sections"):
        result["subrules"] = []
        for section in chapter["sections"]:
            result["subrules"].append(
                {
                    "chapter": section["chapter"],
                    "en": section["en"],
                    "zh": section["zh"],
                    "contents": _flatten_groups(project, section["groups"]),
                }
            )
    return result


def _quote_markdown(value: str) -> str:
    return "\n".join(">" if line == "" else f">{line}" for line in value.splitlines())


def _markdown_for_rendered(rendered: dict[str, Any]) -> list[str]:
    lines = [rendered["en"], "", rendered["zh"]]
    for extra in rendered.get("extras", []):
        lines.extend(["", _quote_markdown(extra["en"]), ">", _quote_markdown(extra["zh"])])
    return lines


def _markdown_for_group(project: Project, group: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for rendered in _render_group(project, group):
        lines.extend(_markdown_for_rendered(rendered))
        lines.append("")
    return lines


def _read_version_notes(project: Project) -> str:
    path = _ensure_within(project.root / VERSION_NOTES_PATH, project.root, "Version notes path")
    try:
        value = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
    except OSError as exc:
        raise PipelineError(f"Unable to read shared version notes {path}: {exc}") from exc
    if not value.startswith("# 版本说明\n"):
        raise PipelineError(f"Shared version notes must start with '# 版本说明': {path}")
    if re.search(r"(?m)^# 目录\s*$", value):
        raise PipelineError(f"Shared version notes must not contain the generated table of contents: {path}")
    return value


def _chapter_heading(chapter: dict[str, Any]) -> str:
    if chapter["id"] == "mtr-introduction":
        return f"{chapter['en']} {chapter['zh']}"
    if chapter["id"].startswith("mtr-appendix-"):
        return f"{chapter['chapter']}—{chapter['en']} {chapter['zh']}"
    return f"MTR {chapter['chapter']} {chapter['en']} {chapter['zh']}"


def _section_heading(section: dict[str, Any]) -> str:
    return f"MTR {section['chapter']} {section['en']} {section['zh']}"


def _markdown_anchor(value: str) -> str:
    normalized = "".join(
        character
        for character in value.casefold()
        if character.isalnum() or character in {" ", "-", "_"}
    )
    return re.sub(r"\s", "-", normalized).strip("-")


def _markdown_toc(project: Project) -> str:
    lines = ["# 目录", "", "- [版本说明](#版本说明)", "- [目录](#目录)"]
    for source in project.sources:
        chapter = source.data["chapter"]
        heading = _chapter_heading(chapter)
        lines.append(f"- [{heading}](#{_markdown_anchor(heading)})")
        for section in chapter.get("sections", []):
            section_heading = _section_heading(section)
            lines.append(f"  - [{section_heading}](#{_markdown_anchor(section_heading)})")
    return "\n".join(lines)


def _json_route(chapter: dict[str, Any]) -> str:
    if chapter["id"] == "mtr-introduction":
        return "/mtr"
    if chapter["id"].startswith("mtr-appendix-"):
        return f"/mtr/{chapter['id'].removeprefix('mtr-')}"
    return f"/mtr/{chapter['chapter'].rstrip('.')}"


def _json_toc(project: Project) -> str:
    lines = ["# 目录", "", "- [版本说明](/mtr#版本说明)", "- [目录](/mtr#目录)"]
    for source in project.sources:
        chapter = source.data["chapter"]
        route = _json_route(chapter)
        lines.append(f"- [{_chapter_heading(chapter)}]({route})")
        for section in chapter.get("sections", []):
            lines.append(
                f"  - [{_section_heading(section)}]({route}#{section['chapter']})"
            )
    return "\n".join(lines)


def _build_markdown(project: Project, version_notes: str) -> str:
    title = project.manifest["document"]["title"]
    lines = [version_notes, "", _markdown_toc(project), "", f"# {title['en']} {title['zh']}", ""]
    for source in project.sources:
        chapter = source.data["chapter"]
        lines.extend([f"# {_chapter_heading(chapter)}", ""])

        for group in chapter.get("groups", []):
            lines.extend(_markdown_for_group(project, group))

        for section in chapter.get("sections", []):
            lines.extend([f"## {_section_heading(section)}", ""])
            for group in section["groups"]:
                lines.extend(_markdown_for_group(project, group))

    markdown = "\n".join(lines).rstrip() + "\n"
    if RELATIVE_IMAGE_RE.search(markdown):
        raise PipelineError("Generated Markdown contains a relative image reference")
    return markdown


def _resolve_output_schema(project: Project, schema_path: Path | str | None) -> Path:
    candidate = OUTPUT_SCHEMA_PATH if schema_path is None else Path(schema_path)
    if not candidate.is_absolute():
        candidate = project.root / candidate
    return _ensure_within(candidate, project.root, "Output schema path")


def build_outputs(
    project: Project,
    *,
    output_schema_path: Path | str | None = None,
    validate_output: bool = True,
) -> tuple[str, str]:
    version_notes = _read_version_notes(project)
    main: list[dict[str, Any]] = []
    appendices: list[dict[str, Any]] = []
    intro_contents: list[dict[str, Any]] = []

    for source in project.sources:
        chapter = source.data["chapter"]
        built = _build_chapter(project, chapter)
        if chapter["id"] == "mtr-introduction":
            intro_contents.extend(built.get("contents", []))
        elif chapter["id"].startswith("mtr-appendix-"):
            appendices.append(built)
        else:
            main.append(built)

    output = {
        "version": project.manifest["document"]["version"],
        "intro": {
            "contents": [
                {"id": "mtr-version-notes", "en": "", "zh": version_notes},
                {"id": "mtr-table-of-contents", "en": "", "zh": _json_toc(project)},
                *intro_contents,
            ]
        },
        "main": main,
        "appendices": appendices,
    }
    if validate_output:
        schema_path = _resolve_output_schema(project, output_schema_path)
        validate_built_output(output, _read_schema(schema_path), "generated MTR output")
    json_text = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if '"groups"' in json_text:
        raise PipelineError("Generated JSON unexpectedly contains intermediate groups")
    return json_text, _build_markdown(project, version_notes)


def write_outputs(
    project: Project,
    json_path: Path | str,
    markdown_path: Path | str,
    *,
    output_schema_path: Path | str | None = None,
) -> None:
    json_text, markdown_text = build_outputs(project, output_schema_path=output_schema_path)
    json_path = Path(json_path)
    markdown_path = Path(markdown_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json_text, encoding="utf-8", newline="\n")
    markdown_path.write_text(markdown_text, encoding="utf-8", newline="\n")

