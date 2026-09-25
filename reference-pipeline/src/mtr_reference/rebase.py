from __future__ import annotations

import copy
import difflib
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from .models import OfficialDocument, OfficialSection, OfficialUnit
from .project import TranslationProject, assemble_group


ID_RE = re.compile(r"-(?P<kind>[gb])(?P<number>\d+)$")
TOKEN_RE = re.compile(r"\s+|[\w]+(?:[’'][\w]+)*|[^\w\s]", re.UNICODE)


class RebaseError(RuntimeError):
    """Raised when an update cannot be inherited without losing content."""


@dataclass(frozen=True)
class AlignmentStep:
    operation: Literal["match", "missing-official", "old-only"]
    official: int | None
    old: int | None


@dataclass
class Finding:
    code: str
    severity: str
    section: str
    message: str
    content_id: str = ""
    old_en: str = ""
    new_en: str = ""


class _IdAllocator:
    def __init__(self, prefix: str, groups: list[dict[str, Any]]) -> None:
        self.prefix = prefix
        self.group = 0
        self.block = 0
        for group in groups:
            match = ID_RE.search(group["id"])
            if match and match.group("kind") == "g":
                self.group = max(self.group, int(match.group("number")))
            for block in group["blocks"]:
                match = ID_RE.search(block["id"])
                if match and match.group("kind") == "b":
                    self.block = max(self.block, int(match.group("number")))

    def group_id(self) -> str:
        self.group += 1
        return f"{self.prefix}-g{self.group:03d}"

    def block_id(self) -> str:
        self.block += 1
        return f"{self.prefix}-b{self.block:03d}"


def _normalize(value: str) -> str:
    value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value)
    value = value.replace("™", "").replace("®", "").replace("`", "")
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = value.replace("–", "-").replace("—", "-")
    return "".join(character.casefold() for character in value if character.isalnum())


def _similarity(unit: OfficialUnit, group: dict[str, Any]) -> float:
    left = _normalize(unit.text)
    right = _normalize(assemble_group(group, "en"))
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def _align(official: OfficialSection, groups: list[dict[str, Any]]) -> list[AlignmentStep]:
    """Pair ordered units and groups; scores are internal and never used as review output."""

    @lru_cache(maxsize=None)
    def solve(i: int, j: int) -> tuple[float, tuple[AlignmentStep, ...]]:
        if i == len(official.units) and j == len(groups):
            return 0.0, ()
        candidates: list[tuple[float, tuple[AlignmentStep, ...]]] = []
        if i < len(official.units):
            cost, tail = solve(i + 1, j)
            candidates.append((0.45 + cost, (AlignmentStep("missing-official", i, None),) + tail))
        if j < len(groups):
            cost, tail = solve(i, j + 1)
            candidates.append((0.45 + cost, (AlignmentStep("old-only", None, j),) + tail))
        if i < len(official.units) and j < len(groups):
            similarity = _similarity(official.units[i], groups[j])
            # Prefer an explicit removal plus insertion over inheriting a
            # translation across a weak textual resemblance. False negatives
            # create visible review work; false inheritance can hide an error.
            if similarity >= 0.50:
                type_penalty = 0.0
                if official.units[i].kind == "list" and groups[j]["type"] != "list":
                    type_penalty = 0.08
                if official.units[i].kind == "table" and groups[j]["type"] != "table":
                    type_penalty = 0.12
                cost, tail = solve(i + 1, j + 1)
                candidates.append(
                    (1.0 - similarity + type_penalty + cost, (AlignmentStep("match", i, j),) + tail)
                )
        return min(candidates, key=lambda candidate: candidate[0])

    return list(solve(0, 0)[1])


def _normalized_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(value):
        if not character.isalnum():
            continue
        for item in character.casefold():
            normalized.append(item)
            positions.append(index)
    return "".join(normalized), positions


def _map_boundary(
    opcodes: list[tuple[str, int, int, int, int]], boundary: int, target_length: int
) -> int:
    for _, a0, a1, b0, b1 in opcodes:
        if a0 <= boundary <= a1:
            if a1 == a0:
                return b0
            return round(b0 + ((boundary - a0) / (a1 - a0)) * (b1 - b0))
    return target_length


def _partition_official(official: str, old_parts: list[str]) -> tuple[list[str], list[str]]:
    """Project existing annotation-driven block boundaries onto new official text."""

    if len(old_parts) == 1:
        return [official], []
    if any(not part.strip() for part in old_parts):
        raise RebaseError("An empty English block cannot be automatically repartitioned")
    source_norm = "".join(_normalize(part) for part in old_parts)
    target_norm, target_positions = _normalized_map(official)
    opcodes = difflib.SequenceMatcher(None, source_norm, target_norm, autojunk=False).get_opcodes()
    boundaries: list[int] = []
    consumed = 0
    for part in old_parts[:-1]:
        consumed += len(_normalize(part))
        mapped = _map_boundary(opcodes, consumed, len(target_norm))
        if mapped <= 0:
            boundary = 0
        elif mapped >= len(target_positions):
            boundary = len(official)
        else:
            boundary = target_positions[mapped - 1] + 1
        while boundary < len(official) and official[boundary] in ".,;:!?)]}’”\"'":
            boundary += 1
        boundaries.append(boundary)
    pieces: list[str] = []
    delimiters: list[str] = []
    start = 0
    for boundary in boundaries:
        whitespace_end = boundary
        while whitespace_end < len(official) and official[whitespace_end].isspace():
            whitespace_end += 1
        pieces.append(official[start:boundary].strip())
        delimiters.append(official[boundary:whitespace_end] or " ")
        start = whitespace_end
    pieces.append(official[start:].strip())
    if any(not piece for piece in pieces):
        raise RebaseError(f"Automatic English partition produced an empty block: {official}")
    rebuilt = "".join(
        piece + (delimiters[index] if index < len(delimiters) else "")
        for index, piece in enumerate(pieces)
    )
    if rebuilt != official:
        raise RebaseError(
            "Automatic English partition failed exact reconstruction:\n"
            f"expected: {official}\nactual:   {rebuilt}"
        )
    return pieces, delimiters


def _table_rows(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip().startswith("|")]


def _table_key(row: str) -> str:
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    return _normalize(cells[0] if cells else row)


def _rebase_table(
    group: dict[str, Any],
    unit: OfficialUnit,
    allocator: _IdAllocator,
    section: str,
    findings: list[Finding],
) -> dict[str, Any]:
    result = copy.deepcopy(group)
    rows = _table_rows(unit.text)
    if len(rows) < 2 or not result["blocks"]:
        raise RebaseError(f"Malformed table in {section}")
    header = result["blocks"][0]
    header["en"] = "\n".join(rows[:2])
    available: dict[str, list[dict[str, Any]]] = {}
    for block in result["blocks"][1:]:
        old_rows = _table_rows(block["en"])
        if old_rows:
            available.setdefault(_table_key(old_rows[0]), []).append(block)
    blocks = [header]
    for row in rows[2:]:
        matches = available.get(_table_key(row), [])
        if matches:
            block = matches.pop(0)
            block["en"] = row
        else:
            block = {"id": allocator.block_id(), "en": row, "translation": ""}
            findings.append(
                Finding("new-table-row", "error", section, "New table row requires translation.", block["id"], new_en=row)
            )
        blocks.append(block)
    for remaining in available.values():
        for block in remaining:
            findings.append(
                Finding("removed-table-row", "warning", section, "Old table row is absent from the new PDF.", block["id"], old_en=block["en"])
            )
    result["blocks"] = blocks
    return result


def _rebase_match(
    group: dict[str, Any],
    unit: OfficialUnit,
    allocator: _IdAllocator,
    section: str,
    findings: list[Finding],
) -> dict[str, Any]:
    old_en = assemble_group(group, "en")
    if group["type"] == "table" and len(group["blocks"]) > 1 and len(_table_rows(unit.text)) >= 2:
        result = _rebase_table(group, unit, allocator, section, findings)
    else:
        result = copy.deepcopy(group)
        target = f"**{unit.text}**" if old_en.strip().startswith("**") and old_en.strip().endswith("**") else unit.text
        blocks = result["blocks"]
        if len(blocks) == 1:
            blocks[0]["en"] = target
            blocks[0].pop("joinAfter", None)
        else:
            parts, delimiters = _partition_official(target, [block["en"] for block in blocks])
            for index, (block, part) in enumerate(zip(blocks, parts, strict=True)):
                block["en"] = part
                if index < len(delimiters):
                    join = block.setdefault("joinAfter", {})
                    join["en"] = delimiters[index]
                    join.setdefault("translation", "")
                else:
                    block.pop("joinAfter", None)
    new_en = assemble_group(result, "en")
    if old_en != new_en:
        findings.append(
            Finding("official-text-changed", "warning", section, "Official English changed; inherited translation and annotation require review.", group["id"], old_en, new_en)
        )
    return result


def _new_group(
    unit: OfficialUnit, allocator: _IdAllocator, section: str, findings: list[Finding]
) -> dict[str, Any]:
    block = {"id": allocator.block_id(), "en": unit.text, "translation": ""}
    group = {"id": allocator.group_id(), "type": unit.kind, "blocks": [block]}
    findings.append(
        Finding("new-official-content", "error", section, "New official content requires translation.", block["id"], new_en=unit.text)
    )
    return group


def rebase_groups(
    old_groups: list[dict[str, Any]],
    official: OfficialSection,
    prefix: str,
    findings: list[Finding],
) -> list[dict[str, Any]]:
    """Rebase one section while preserving IDs, translations, annotations and images."""

    allocator = _IdAllocator(prefix, old_groups)
    text_groups: list[dict[str, Any]] = []
    images_by_anchor: dict[int, list[dict[str, Any]]] = {}
    for group in old_groups:
        if group["type"] == "image":
            images_by_anchor.setdefault(len(text_groups), []).append(copy.deepcopy(group))
        else:
            text_groups.append(group)
    steps = _align(official, text_groups)
    result: list[dict[str, Any]] = []
    emitted_image_anchors: set[int] = set()

    def append_images(anchor: int) -> None:
        if anchor not in emitted_image_anchors:
            result.extend(images_by_anchor.get(anchor, []))
            emitted_image_anchors.add(anchor)

    append_images(0)
    authoritative: list[dict[str, Any]] = []
    consumed = 0
    for step in steps:
        if step.operation == "missing-official":
            assert step.official is not None
            group = _new_group(official.units[step.official], allocator, official.key, findings)
            result.append(group)
            authoritative.append(group)
        elif step.operation == "old-only":
            assert step.old is not None
            group = text_groups[step.old]
            findings.append(
                Finding("removed-official-content", "warning", official.key, "Old content is absent from the new official PDF and was omitted.", group["id"], old_en=assemble_group(group, "en"))
            )
            consumed = max(consumed, step.old + 1)
        else:
            assert step.official is not None and step.old is not None
            group = _rebase_match(text_groups[step.old], official.units[step.official], allocator, official.key, findings)
            result.append(group)
            authoritative.append(group)
            consumed = max(consumed, step.old + 1)
        append_images(consumed)
    for anchor in sorted(index for index in images_by_anchor if index > consumed):
        append_images(anchor)
    if len(authoritative) != len(official.units):
        raise RebaseError(f"Official unit coverage mismatch in {official.key}")
    for index, (group, unit) in enumerate(zip(authoritative, official.units, strict=True), 1):
        actual = assemble_group(group, "en")
        if actual.startswith("**") and actual.endswith("**"):
            actual = actual[2:-2]
        matches = _normalize(actual) == _normalize(unit.text) if group["type"] == "table" else actual == unit.text
        if not matches:
            raise RebaseError(
                f"Authoritative English reconstruction failed in {official.key} unit {index}:\n"
                f"expected: {unit.text}\nactual:   {actual}"
            )
    return result


def _exact_changes(old: str, new: str) -> list[dict[str, str]]:
    old_tokens = TOKEN_RE.findall(old)
    new_tokens = TOKEN_RE.findall(new)
    changes: list[dict[str, str]] = []
    for operation, a0, a1, b0, b1 in difflib.SequenceMatcher(
        None, old_tokens, new_tokens, autojunk=False
    ).get_opcodes():
        if operation == "equal":
            changes.append({"op": "equal", "text": "".join(old_tokens[a0:a1])})
        elif operation == "delete":
            changes.append({"op": "delete", "text": "".join(old_tokens[a0:a1])})
        elif operation == "insert":
            changes.append({"op": "insert", "text": "".join(new_tokens[b0:b1])})
        else:
            changes.extend(
                [
                    {"op": "delete", "text": "".join(old_tokens[a0:a1])},
                    {"op": "insert", "text": "".join(new_tokens[b0:b1])},
                ]
            )
    return [change for change in changes if change["text"]]


def _render_changes(changes: list[dict[str, str]]) -> str:
    return "".join(
        f"[-{row['text']}-]" if row["op"] == "delete" else f"{{+{row['text']}+}}" if row["op"] == "insert" else row["text"]
        for row in changes
    )


def _write_report(
    json_path: Path,
    base_version: str,
    effective_date: str,
    pdf_url: str,
    pdf_sha256: str,
    findings: list[Finding],
) -> dict[str, Any]:
    summary = {severity: sum(row.severity == severity for row in findings) for severity in ("error", "warning", "info")}
    rows = []
    for finding in findings:
        row = asdict(finding)
        row["changes"] = _exact_changes(finding.old_en, finding.new_en) if finding.old_en != finding.new_en else []
        rows.append(row)
    report = {
        "baseVersion": base_version,
        "effectiveDate": effective_date,
        "officialPdfUrl": pdf_url,
        "officialPdfSha256": pdf_sha256,
        "summary": summary,
        "findings": rows,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Official MTR update review",
        "",
        f"- Base version: `{base_version}`",
        f"- New effective date: `{effective_date}`",
        f"- Official PDF: {pdf_url}",
        f"- Findings: {summary['error']} errors, {summary['warning']} warnings, {summary['info']} info",
        "",
        "Review every changed English passage and its inherited translation and annotation before publishing.",
        "",
    ]
    for severity in ("error", "warning", "info"):
        lines.extend([f"## {severity.title()} findings", ""])
        selected = [row for row in findings if row.severity == severity]
        if not selected:
            lines.append("- None.")
        for finding in selected:
            suffix = f" (`{finding.content_id}`)" if finding.content_id else ""
            lines.append(f"- `{finding.section}` **{finding.code}**{suffix}: {finding.message}")
            if finding.old_en != finding.new_en:
                lines.extend(["", "  Exact diff (`[-deleted-]`, `{+inserted+}`):", "", "```text", _render_changes(_exact_changes(finding.old_en, finding.new_en)), "```", ""])
        lines.append("")
    json_path.with_suffix(".md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report


def _source_index(project: TranslationProject) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    sections: dict[str, dict[str, Any]] = {}
    chapters: dict[int, dict[str, Any]] = {}
    for chapter in project.chapters:
        label = chapter["chapter"]
        if label == "Introduction":
            sections["introduction"] = chapter
        elif match := re.fullmatch(r"(\d+)\.", label):
            chapters[int(match.group(1))] = chapter
            for section in chapter["sections"]:
                sections[section["chapter"]] = section
        elif match := re.fullmatch(r"Appendix ([A-F])", label):
            sections[f"appendix-{match.group(1).lower()}"] = chapter
        else:
            raise RebaseError(f"Unsupported chapter label: {label}")
    return sections, chapters


def _dump_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100000),
        encoding="utf-8",
        newline="\n",
    )


def rebase_project(
    project: TranslationProject,
    official: OfficialDocument,
    *,
    effective_date: str,
    pdf_url: str,
    output_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Create a review candidate without modifying the currently reviewed source."""

    old_sections, numbered_chapters = _source_index(project)
    official_by_key = {section.key: section for section in official.sections}
    if len(official_by_key) != len(official.sections):
        raise RebaseError("Official PDF contains duplicate section keys")
    required = {"introduction", *(f"appendix-{letter}" for letter in "abcdef")}
    if required - set(old_sections):
        raise RebaseError("A full update requires Introduction and Appendices A-F in the base project")
    if set(range(1, 11)) - set(numbered_chapters):
        raise RebaseError("A full update requires numbered chapters 1-10 in the base project")
    if required - set(official_by_key):
        raise RebaseError("The official parser did not produce Introduction and Appendices A-F")

    findings: list[Finding] = []
    output_files: dict[str, dict[str, Any]] = {}
    for relative, original_chapter in zip(project.manifest["files"], project.chapters, strict=True):
        chapter = copy.deepcopy(original_chapter)
        label = chapter["chapter"]
        if label == "Introduction":
            section = official_by_key["introduction"]
            chapter["en"] = section.en
            chapter["groups"] = rebase_groups(chapter["groups"], section, chapter["id"], findings)
        elif match := re.fullmatch(r"(\d+)\.", label):
            number = int(match.group(1))
            old_by_key = {section["chapter"]: section for section in chapter["sections"]}
            keys = sorted(
                (key for key in official_by_key if re.fullmatch(rf"{number}\.\d+", key)),
                key=lambda key: int(key.split(".")[1]),
            )
            new_sections = []
            for key in keys:
                official_section = official_by_key[key]
                if key in old_by_key:
                    source = copy.deepcopy(old_by_key[key])
                    source["en"] = official_section.en
                    source["groups"] = rebase_groups(source["groups"], official_section, source["id"], findings)
                else:
                    allocator = _IdAllocator(f"mtr-{key}", [])
                    source = {
                        "id": f"mtr-{key}",
                        "chapter": key,
                        "en": official_section.en,
                        "translation": "",
                        "groups": [_new_group(unit, allocator, key, findings) for unit in official_section.units],
                    }
                    findings.append(Finding("new-section", "error", key, "New section heading requires translation.", source["id"], new_en=official_section.en))
                new_sections.append(source)
            for key in sorted(set(old_by_key) - set(keys)):
                findings.append(Finding("removed-section", "warning", key, "Old section is absent from the new official PDF and was omitted.", old_by_key[key]["id"]))
            chapter["sections"] = new_sections
        else:
            appendix = f"appendix-{label[-1].lower()}"
            section = official_by_key[appendix]
            chapter["en"] = section.en
            chapter["groups"] = rebase_groups(chapter["groups"], section, chapter["id"], findings)
        output_files[relative] = {"chapter": chapter}

    manifest = copy.deepcopy(project.manifest)
    manifest["document"]["version"] = effective_date.replace("-", "")
    manifest["document"]["effectiveDate"] = effective_date
    _dump_yaml(output_dir / "manifest.yaml", manifest)
    for relative, value in output_files.items():
        _dump_yaml(output_dir / relative, value)
    return _write_report(
        report_path,
        project.manifest["document"]["effectiveDate"],
        effective_date,
        pdf_url,
        official.source_sha256,
        findings,
    )
