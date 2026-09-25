from __future__ import annotations

import base64
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator, FormatChecker


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ProjectError(RuntimeError):
    """Raised when a translation project is invalid or incomplete."""


@dataclass(frozen=True)
class TranslationProject:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    chapters: list[dict[str, Any]]


def _read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProjectError(f"Expected a YAML object: {path}")
    return value


def _read_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(value: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        details = []
        for error in errors[:8]:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            details.append(f"{location}: {error.message}")
        raise ProjectError(f"Schema validation failed for {label}:\n" + "\n".join(details))


def _within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectError(f"{label} escapes the project root: {path}") from exc
    return resolved


def _iter_groups(chapter: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from chapter.get("groups", [])
    for section in chapter.get("sections", []):
        yield from section["groups"]


def load_project(
    manifest_path: Path,
    source_schema_path: Path,
    *,
    require_complete: bool,
) -> TranslationProject:
    manifest_path = manifest_path.resolve()
    root = manifest_path.parent
    schema = _read_schema(source_schema_path.resolve())
    manifest = _read_yaml(manifest_path)
    _validate(manifest, schema, str(manifest_path))
    chapters = []
    seen_ids: set[str] = set()
    for relative in manifest["files"]:
        source_path = _within(root / relative, root, "Source file")
        chapter_file = _read_yaml(source_path)
        _validate(chapter_file, schema, str(source_path))
        chapter = chapter_file["chapter"]
        chapters.append(chapter)
        ids = [chapter["id"]]
        ids.extend(section["id"] for section in chapter.get("sections", []))
        for group in _iter_groups(chapter):
            ids.append(group["id"])
            ids.extend(block["id"] for block in group["blocks"])
            for block in group["blocks"]:
                if group["type"] == "image":
                    asset = _within(root / block["asset"], root, "Image asset")
                    if not asset.is_file() or asset.read_bytes()[:8] != PNG_SIGNATURE:
                        raise ProjectError(f"Invalid PNG asset: {asset}")
                    if require_complete and not block["alt"]["translation"].strip():
                        raise ProjectError(f"Missing translated image alt text: {block['id']}")
                else:
                    if require_complete and not block["translation"].strip():
                        raise ProjectError(f"Missing translation: {block['id']}")
                for extra in block.get("extras", []):
                    if require_complete and not extra["translation"].strip():
                        raise ProjectError(f"Missing annotation translation: {block['id']}")
        duplicates = seen_ids.intersection(ids)
        if duplicates:
            raise ProjectError("Duplicate IDs: " + ", ".join(sorted(duplicates)))
        seen_ids.update(ids)
        if require_complete:
            values = [chapter["translation"]]
            values.extend(section["translation"] for section in chapter.get("sections", []))
            if any(not value.strip() for value in values):
                raise ProjectError(f"Missing translated heading in {source_path}")
    return TranslationProject(root, manifest_path, manifest, chapters)


def assemble_group(group: dict[str, Any], language: str) -> str:
    defaults = {
        "paragraph": {"en": " ", "translation": ""},
        "list": {"en": "\n", "translation": "\n"},
        "table": {"en": "\n", "translation": "\n"},
        "markdown": {"en": "\n", "translation": "\n"},
    }
    parts: list[str] = []
    blocks = group["blocks"]
    for index, block in enumerate(blocks):
        parts.append(block[language])
        if index == len(blocks) - 1:
            continue
        override = block.get("joinAfter", {}).get(language)
        parts.append(defaults[group["type"]][language] if override is None else override)
    return "".join(parts)


def _image_markdown(project: TranslationProject, block: dict[str, Any], language: str) -> str:
    asset = _within(project.root / block["asset"], project.root, "Image asset")
    payload = base64.b64encode(asset.read_bytes()).decode("ascii")
    return f"![{block['alt'][language]}](data:image/png;base64,{payload})"


def _render_group(project: TranslationProject, group: dict[str, Any]) -> list[dict[str, Any]]:
    if group["type"] == "image":
        return [
            {
                "id": block["id"],
                "type": "image",
                "en": _image_markdown(project, block, "en"),
                "translation": _image_markdown(project, block, "translation"),
                **({"extras": copy.deepcopy(block["extras"])} if block.get("extras") else {}),
            }
            for block in group["blocks"]
        ]
    if group["type"] == "table":
        extras = [extra for block in group["blocks"] for extra in block.get("extras", [])]
        return [
            {
                "id": group["blocks"][0]["id"],
                "type": "table",
                "en": assemble_group(group, "en"),
                "translation": assemble_group(group, "translation"),
                **({"extras": copy.deepcopy(extras)} if extras else {}),
            }
        ]
    rendered = []
    for block in group["blocks"]:
        row = {
            "id": block["id"],
            "type": group["type"],
            "en": block["en"],
            "translation": block["translation"],
        }
        if block.get("extras"):
            row["extras"] = copy.deepcopy(block["extras"])
        rendered.append(row)
    return rendered


def _render_contents(project: TranslationProject, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [content for group in groups for content in _render_group(project, group)]


def _render_chapter(project: TranslationProject, chapter: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: chapter[key] for key in ("id", "chapter", "en", "translation")
    }
    if "groups" in chapter:
        result["contents"] = _render_contents(project, chapter["groups"])
    else:
        result["sections"] = [
            {
                "id": section["id"],
                "chapter": section["chapter"],
                "en": section["en"],
                "translation": section["translation"],
                "contents": _render_contents(project, section["groups"]),
            }
            for section in chapter["sections"]
        ]
    return result


def _quote(value: str) -> str:
    return "\n".join("> " + line if line else ">" for line in value.splitlines())


def _content_markdown(content: dict[str, Any]) -> list[str]:
    lines = [content["en"], "", content["translation"]]
    for extra in content.get("extras", []):
        lines.extend(["", _quote(extra["en"]), ">", _quote(extra["translation"])])
    return lines


def build_project(
    project: TranslationProject,
    output_schema_path: Path,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    output = {
        "formatVersion": 1,
        "document": copy.deepcopy(project.manifest["document"]),
        "chapters": [_render_chapter(project, chapter) for chapter in project.chapters],
    }
    _validate(output, _read_schema(output_schema_path.resolve()), "generated output")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    title = output["document"]["title"]
    lines = [f"# {title['en']} — {title['translation']}", ""]
    for chapter in output["chapters"]:
        lines.extend(
            [f"# {chapter['chapter']} {chapter['en']} — {chapter['translation']}", ""]
        )
        if "contents" in chapter:
            for content in chapter["contents"]:
                lines.extend([*_content_markdown(content), ""])
        else:
            for section in chapter["sections"]:
                lines.extend(
                    [
                        f"## {section['chapter']} {section['en']} — {section['translation']}",
                        "",
                    ]
                )
                for content in section["contents"]:
                    lines.extend([*_content_markdown(content), ""])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return output
