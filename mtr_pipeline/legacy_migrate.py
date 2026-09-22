from __future__ import annotations

import argparse
import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from mtr_pipeline.core import assemble_group
from mtr_pipeline.legacy_import import (
    AlignmentStep,
    LegacyImportError,
    LegacyPair,
    LegacySection,
    OfficialSection,
    OfficialUnit,
    _match_normalize,
    align_section,
    pair_section,
    parse_official_pdf,
    sha256_file,
    split_sections,
    tokenize_legacy_markdown,
)


CHAPTER_TITLES = {
    1: "Tournament Fundamentals",
    2: "Tournament Mechanics",
    3: "Tournament Rules",
    4: "Communication",
    5: "Tournament Violations",
    6: "Constructed Tournament Rules",
    7: "Limited Tournament Rules",
    8: "Team Tournament Rules",
    9: "Two-Headed Giant Tournament Rules",
    10: "Sanctioning Rules",
}

APPENDIX_C_LINE_TO_OFFICIAL = {
    4312: 0,
    4314: 1,
    4316: 2,
    4318: 3,
    4320: 4,
    4322: 5,
    4324: 6,
    4326: 7,
    4328: 8,
    4330: 10,
    4332: 11,
    4334: 12,
    4336: 13,
    4338: 14,
    4344: 15,
    4346: 16,
    4350: 17,
    4352: 18,
    4357: 19,
    4359: 20,
    4361: 21,
    4363: 22,
    4377: 23,
    4387: 24,
    4389: 25,
    4391: 26,
    4393: 27,
    4395: 28,
}


class LiteralString(str):
    pass


class SourceDumper(yaml.SafeDumper):
    pass


def _represent_literal(dumper: SourceDumper, value: LiteralString):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style="|")


SourceDumper.add_representer(LiteralString, _represent_literal)


@dataclass
class MigrationFinding:
    code: str
    severity: str
    section: str
    message: str
    source_lines: list[int]
    official_text: str = ""


@dataclass
class Counters:
    group: int = 0
    block: int = 0

    def group_id(self, prefix: str) -> str:
        self.group += 1
        return f"{prefix}-g{self.group:03d}"

    def block_id(self, prefix: str) -> str:
        self.block += 1
        return f"{prefix}-b{self.block:03d}"


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LegacyImportError(f"Migration override root must be an object: {path}")
    return value


def _source_lines(pair: LegacyPair) -> list[int]:
    lines: list[int] = []
    for span in pair.spans:
        lines.extend(range(span.start, span.end + 1))
    return sorted(set(lines))


def _literal(value: str) -> str:
    if "\n" in value or len(value) >= 80:
        return LiteralString(value)
    return value


def _ensure_list(value: str) -> str:
    # The legacy document uses both ``* `` and ``*\t`` list markers.  Emit one
    # canonical Markdown marker so a tab-indented source item cannot become
    # ``* *\t...`` in the generated YAML.
    if re.match(r"^\*\s+", value):
        return re.sub(r"^\*\s+", "* ", value, count=1)
    return f"* {value}"


def _normalized_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(value):
        if not character.isalnum():
            continue
        folded = character.casefold()
        for item in folded:
            normalized.append(item)
            positions.append(index)
    return "".join(normalized), positions


def _map_boundary(opcodes: list[tuple[str, int, int, int, int]], boundary: int, target_length: int) -> int:
    for _, a0, a1, b0, b1 in opcodes:
        if a0 <= boundary <= a1:
            if a1 == a0:
                return b0
            ratio = (boundary - a0) / (a1 - a0)
            return round(b0 + ratio * (b1 - b0))
    return target_length


def _partition_official(
    official: str,
    legacy_parts: list[str],
    override_parts: list[str] | None,
) -> tuple[list[str], list[str]]:
    if len(legacy_parts) == 1:
        return [official], []
    if override_parts is not None:
        rebuilt = " ".join(part.strip() for part in override_parts)
        if rebuilt != official:
            raise LegacyImportError(
                "Manual English split does not reconstruct the official paragraph:\n"
                f"expected: {official}\nactual:   {rebuilt}"
            )
        return [part.strip() for part in override_parts], [" "] * (len(override_parts) - 1)
    if any(not part.strip() for part in legacy_parts):
        raise LegacyImportError("An empty legacy English part requires an explicit bodyEnglishSplits override")

    source_norm = "".join(_match_normalize(part) for part in legacy_parts)
    target_norm, target_positions = _normalized_map(official)
    matcher = difflib.SequenceMatcher(None, source_norm, target_norm, autojunk=False)
    opcodes = matcher.get_opcodes()
    boundaries: list[int] = []
    consumed = 0
    for part in legacy_parts[:-1]:
        consumed += len(_match_normalize(part))
        normalized_boundary = _map_boundary(opcodes, consumed, len(target_norm))
        if normalized_boundary <= 0:
            character_boundary = 0
        elif normalized_boundary >= len(target_positions):
            character_boundary = len(official)
        else:
            character_boundary = target_positions[normalized_boundary - 1] + 1
        while character_boundary < len(official) and official[character_boundary] in ".,;:!?)]}’”\"'":
            character_boundary += 1
        boundaries.append(character_boundary)

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
        raise LegacyImportError(f"Automatic English partition produced an empty block: {official}")
    rebuilt = "".join(
        piece + (delimiters[index] if index < len(delimiters) else "")
        for index, piece in enumerate(pieces)
    )
    if rebuilt != official:
        raise LegacyImportError(
            "Automatic English partition failed exact reconstruction:\n"
            f"expected: {official}\nactual:   {rebuilt}"
        )
    return pieces, delimiters


def _split_overrides(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    value = overrides.get("bodyEnglishSplits", [])
    if not isinstance(value, list):
        raise LegacyImportError("bodyEnglishSplits must be a list")
    return value


def _find_split_override(
    overrides: dict[str, Any],
    section: str,
    official_text: str,
) -> list[str] | None:
    for row in _split_overrides(overrides):
        if row.get("section") == section and official_text.startswith(row.get("officialStartsWith", "")):
            parts = row.get("parts")
            if not isinstance(parts, list) or not all(isinstance(part, str) for part in parts):
                raise LegacyImportError(f"Invalid English split override for {section}")
            return parts
    return None


def _should_not_inherit(
    overrides: dict[str, Any],
    section: str,
    official_text: str,
) -> bool:
    rows = overrides.get("bodyNoInheritance", [])
    if not isinstance(rows, list):
        raise LegacyImportError("bodyNoInheritance must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise LegacyImportError("bodyNoInheritance entries must be objects")
        prefix = row.get("officialStartsWith")
        if not isinstance(prefix, str) or not prefix:
            raise LegacyImportError("bodyNoInheritance requires a non-empty officialStartsWith")
        if row.get("section") == section and official_text.startswith(prefix):
            return True
    return False


def _make_block(
    counters: Counters,
    prefix: str,
    en: str,
    zh: str,
    pair: LegacyPair | None = None,
    join_after: str | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "id": counters.block_id(prefix),
        "en": _literal(en),
        "zh": _literal(zh),
    }
    if pair is not None and (pair.annotation_en or pair.annotation_zh):
        block["extras"] = [
            {
                "en": _literal(pair.annotation_en),
                "zh": _literal(pair.annotation_zh),
            }
        ]
    if join_after is not None:
        block["joinAfter"] = {"en": join_after, "zh": ""}
    return block


def _make_group(
    counters: Counters,
    prefix: str,
    kind: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": counters.group_id(prefix),
        "type": kind,
        "blocks": blocks,
    }


def _make_image_group(
    counters: Counters,
    prefix: str,
    pair: LegacyPair,
    assets: dict[str, Any],
) -> dict[str, Any]:
    config = assets.get(pair.asset)
    if not isinstance(config, dict):
        raise LegacyImportError(f"No stable asset mapping for {pair.asset}")
    return {
        "id": counters.group_id(prefix),
        "type": "image",
        "blocks": [
            {
                "id": counters.block_id(prefix),
                "asset": config["path"],
                "alt": config["alt"],
            }
        ],
    }


def _section_titles(section: LegacySection, official: OfficialSection) -> tuple[str, str]:
    title = section.heading.text.lstrip("# ")
    title = re.sub(r"^MTR\s+\d+\.\d+\s+", "", title)
    if title.startswith(official.en):
        zh = title[len(official.en) :].strip()
    else:
        han = re.search(r"[\u3400-\u9fff]", title)
        if han is None:
            raise LegacyImportError(f"Cannot split section title: {section.heading.text}")
        zh = title[han.start() :].strip()
    if not zh:
        raise LegacyImportError(f"Missing Chinese section title: {section.heading.text}")
    return official.en, zh


def _body_section(
    official: OfficialSection,
    legacy: LegacySection,
    overrides: dict[str, Any],
    findings: list[MigrationFinding],
) -> dict[str, Any]:
    prefix = f"mtr-{official.key}"
    counters = Counters()
    pairs, _ = pair_section(legacy)
    content_pairs = [pair for pair in pairs if pair.kind != "image"]
    images_by_anchor: dict[int, list[LegacyPair]] = {}
    count = 0
    for pair in pairs:
        if pair.kind == "image":
            images_by_anchor.setdefault(count, []).append(pair)
        else:
            count += 1

    groups: list[dict[str, Any]] = []
    for image in images_by_anchor.get(0, []):
        groups.append(_make_image_group(counters, prefix, image, overrides["assets"]))

    raw_steps = align_section(official, pairs)
    forced: dict[int, tuple[int, ...]] = {}
    claimed_legacy: set[int] = set()
    for official_index, unit in enumerate(official.units):
        manual_parts = _find_split_override(overrides, official.key, unit.text)
        if manual_parts is None:
            continue
        matched = next(
            (
                step
                for step in raw_steps
                if step.operation == "match" and step.official == (official_index,)
            ),
            None,
        )
        if matched is None or not matched.legacy:
            raise LegacyImportError(f"Manual split target is not aligned in {official.key}")
        start = min(matched.legacy)
        indexes = tuple(range(start, start + len(manual_parts)))
        if indexes[-1] >= len(content_pairs):
            raise LegacyImportError(f"Manual split exceeds legacy blocks in {official.key}")
        forced[official_index] = indexes
        claimed_legacy.update(indexes)

    steps: list[AlignmentStep] = []
    emitted_forced: set[int] = set()
    for step in raw_steps:
        if step.operation == "match" and step.official[0] in forced:
            official_index = step.official[0]
            if official_index not in emitted_forced:
                steps.append(
                    AlignmentStep(
                        "match",
                        step.official,
                        forced[official_index],
                        1.0,
                    )
                )
                emitted_forced.add(official_index)
            continue
        remaining = tuple(index for index in step.legacy if index not in claimed_legacy)
        if step.operation == "match" and not remaining:
            steps.append(AlignmentStep("missing-official", step.official, (), 0.0))
        elif step.operation == "legacy-only" and not remaining:
            continue
        elif remaining != step.legacy:
            steps.append(AlignmentStep(step.operation, step.official, remaining, step.similarity))
        else:
            steps.append(step)
    consumed = 0
    for step in steps:
        if step.operation == "legacy-only":
            pair = content_pairs[step.legacy[0]]
            findings.append(
                MigrationFinding(
                    "legacy-only",
                    "warning",
                    official.key,
                    "Legacy content is not present in the official PDF and was not emitted.",
                    _source_lines(pair),
                )
            )
            consumed = max(consumed, step.legacy[0] + 1)
        elif step.operation == "missing-official":
            unit = official.units[step.official[0]]
            block = _make_block(counters, prefix, unit.text, "")
            groups.append(_make_group(counters, prefix, unit.kind, [block]))
            findings.append(
                MigrationFinding(
                    "missing-translation",
                    "error",
                    official.key,
                    "Official content has no legacy translation.",
                    [],
                    unit.text,
                )
            )
        else:
            unit = official.units[step.official[0]]
            selected = [content_pairs[index] for index in step.legacy]
            if _should_not_inherit(overrides, official.key, unit.text):
                groups.append(
                    _make_group(
                        counters,
                        prefix,
                        unit.kind,
                        [_make_block(counters, prefix, unit.text, "")],
                    )
                )
                findings.append(
                    MigrationFinding(
                        "missing-translation",
                        "error",
                        official.key,
                        "Official content was explicitly prevented from inheriting an unrelated legacy translation.",
                        [],
                        unit.text,
                    )
                )
                for pair in selected:
                    findings.append(
                        MigrationFinding(
                            "legacy-only",
                            "warning",
                            official.key,
                            "Legacy content was explicitly rejected as an unrelated alignment and was not emitted.",
                            _source_lines(pair),
                        )
                    )
                consumed = max(consumed, max(step.legacy) + 1)
                for image in images_by_anchor.get(consumed, []):
                    groups.append(_make_image_group(counters, prefix, image, overrides["assets"]))
                continue
            override_parts = _find_split_override(overrides, official.key, unit.text)
            if override_parts is not None and len(override_parts) != len(selected):
                raise LegacyImportError(
                    f"English split override for {official.key} has {len(override_parts)} parts, "
                    f"but alignment selected {len(selected)} legacy blocks"
                )
            if len(selected) == 1 and selected[0].kind == "table" and step.similarity >= 0.98:
                english_parts, delimiters = [selected[0].en], []
                group_kind = "table"
            else:
                english_parts, delimiters = _partition_official(
                    unit.text,
                    [pair.en for pair in selected],
                    override_parts,
                )
                group_kind = unit.kind
                if len(selected) == 1 and selected[0].kind in {"bold", "subheading"}:
                    group_kind = "markdown"
                    english_parts[0] = f"**{english_parts[0]}**"

            blocks: list[dict[str, Any]] = []
            for index, (pair, en) in enumerate(zip(selected, english_parts, strict=True)):
                zh = pair.zh
                if group_kind == "list":
                    if index == 0:
                        en = _ensure_list(en)
                        zh = _ensure_list(zh) if zh else ""
                elif group_kind == "markdown" and pair.kind == "list":
                    number = re.match(r"^(\d+\.)\s*", en)
                    if number and zh:
                        zh = f"{number.group(1)} {zh}"
                join_after = delimiters[index] if index < len(delimiters) else None
                blocks.append(_make_block(counters, prefix, en, zh, pair, join_after))
            groups.append(_make_group(counters, prefix, group_kind, blocks))
            if step.similarity < 0.995:
                findings.append(
                    MigrationFinding(
                        "source-changed",
                        "warning" if step.similarity >= 0.80 else "error",
                        official.key,
                        f"Official English differs from the legacy English (similarity {step.similarity:.3f}); "
                        "the old Chinese translation was retained for review.",
                        sorted({line for pair in selected for line in _source_lines(pair)}),
                        unit.text,
                    )
                )
            consumed = max(consumed, max(step.legacy) + 1)

        for image in images_by_anchor.get(consumed, []):
            groups.append(_make_image_group(counters, prefix, image, overrides["assets"]))

    en_title, zh_title = _section_titles(legacy, official)
    emitted = [group for group in groups if group["type"] != "image"]
    if len(emitted) != len(official.units):
        raise LegacyImportError(
            f"Official unit coverage mismatch in {official.key}: "
            f"official={len(official.units)}, emitted={len(emitted)}"
        )
    for index, (group, unit) in enumerate(zip(emitted, official.units, strict=True)):
        assembled = assemble_group(group, "en")
        if group["type"] == "table":
            matches = _match_normalize(assembled) == _match_normalize(unit.text)
        else:
            matches = assembled == unit.text
        if not matches:
            raise LegacyImportError(
                f"Authoritative English reconstruction failed in {official.key} unit {index + 1}:\n"
                f"expected: {unit.text}\nactual:   {assembled}"
            )
    return {
        "id": prefix,
        "chapter": official.key,
        "en": en_title,
        "zh": zh_title,
        "groups": groups,
    }


def _chapter_zh(section: LegacySection, number: int) -> str:
    value = re.sub(rf"^#\s+MTR\s+{number}\.\s+", "", section.heading.text).strip()
    english = CHAPTER_TITLES[number]
    if value.endswith(english):
        value = value[: -len(english)].strip()
    if not value:
        raise LegacyImportError(f"Cannot determine Chinese chapter title for chapter {number}")
    return value


def _positional_groups(
    official: OfficialSection,
    pairs: list[LegacyPair],
    prefix: str,
    overrides: dict[str, Any],
    findings: list[MigrationFinding],
) -> list[dict[str, Any]]:
    content = [pair for pair in pairs if pair.kind != "image"]
    if len(content) != len(official.units):
        raise LegacyImportError(
            f"Positional mapping count mismatch in {official.key}: "
            f"official={len(official.units)}, legacy={len(content)}"
        )
    counters = Counters()
    groups: list[dict[str, Any]] = []
    for unit, pair in zip(official.units, content, strict=True):
        kind = unit.kind
        en = unit.text
        zh = pair.zh
        if pair.kind in {"bold", "subheading"}:
            kind = "markdown"
            en = f"**{en}**"
        if kind == "list":
            en = _ensure_list(en)
            zh = _ensure_list(zh) if zh else ""
        if official.key == "appendix-e" and pair.spans[0].start == 4433:
            zh = overrides["appendixE"]["correctedChineseTable"]
        block = _make_block(counters, prefix, en, zh, pair)
        groups.append(_make_group(counters, prefix, kind, [block]))
    return groups


def _appendix_a_groups(
    official: OfficialSection,
    pairs: list[LegacyPair],
    prefix: str,
    findings: list[MigrationFinding],
) -> list[dict[str, Any]]:
    content = [pair for pair in pairs if pair.kind != "image"]
    if len(content) < len(official.units):
        raise LegacyImportError("Appendix A does not contain enough legacy translation blocks")
    mapped = content[: len(official.units)]
    counters = Counters()
    groups: list[dict[str, Any]] = []
    for unit, pair in zip(official.units, mapped, strict=True):
        en, zh = unit.text, pair.zh
        if unit.kind == "list":
            en, zh = _ensure_list(en), _ensure_list(zh)
        groups.append(
            _make_group(counters, prefix, unit.kind, [_make_block(counters, prefix, en, zh, pair)])
        )
    for pair in content[len(official.units) :]:
        findings.append(
            MigrationFinding(
                "legacy-only",
                "warning",
                official.key,
                "Historical Appendix A entry is outside the current official appendix and was not emitted.",
                _source_lines(pair),
            )
        )
    return groups


def _appendix_d_groups(
    official: OfficialSection,
    pairs: list[LegacyPair],
    prefix: str,
    overrides: dict[str, Any],
    findings: list[MigrationFinding],
) -> list[dict[str, Any]]:
    content = [pair for pair in pairs if pair.kind != "image"]
    configured = overrides.get("appendixD", {}).get("officialToLegacy")
    if configured is None:
        if len(official.units) != 25 or len(content) != 25:
            raise LegacyImportError(
                "Appendix D layout changed and requires an explicit appendixD.officialToLegacy mapping"
            )
        official_to_legacy = {0: 0}
        official_to_legacy.update({index: index - 6 for index in range(7, 25)})
    else:
        if not isinstance(configured, dict):
            raise LegacyImportError("appendixD.officialToLegacy must be an object")
        official_to_legacy = {int(key): int(value) for key, value in configured.items()}
        if len(set(official_to_legacy.values())) != len(official_to_legacy):
            raise LegacyImportError("Appendix D legacy indexes may not be reused")
        if any(index < 0 or index >= len(official.units) for index in official_to_legacy):
            raise LegacyImportError("Appendix D official mapping index is out of range")
        if any(index < 0 or index >= len(content) for index in official_to_legacy.values()):
            raise LegacyImportError("Appendix D legacy mapping index is out of range")
    counters = Counters()
    groups: list[dict[str, Any]] = []
    for index, unit in enumerate(official.units):
        legacy_index = official_to_legacy.get(index)
        pair = content[legacy_index] if legacy_index is not None else None
        zh = pair.zh if pair is not None else ""
        en = unit.text
        if unit.kind == "list":
            en = _ensure_list(en)
            zh = _ensure_list(zh) if zh else ""
        groups.append(
            _make_group(counters, prefix, unit.kind, [_make_block(counters, prefix, en, zh, pair)])
        )
        if pair is None:
            findings.append(
                MigrationFinding(
                    "missing-translation",
                    "error",
                    official.key,
                    "Official Appendix D content is absent from the legacy translation.",
                    [],
                    unit.text,
                )
            )
    used_legacy = set(official_to_legacy.values())
    for index, pair in enumerate(content):
        if index in used_legacy:
            continue
        findings.append(
            MigrationFinding(
                "legacy-only",
                "warning",
                official.key,
                "Legacy Appendix D content is absent from the current official appendix and was not emitted.",
                _source_lines(pair),
            )
        )
    return groups


def _appendix_c_groups(
    official: OfficialSection,
    pairs: list[LegacyPair],
    prefix: str,
    overrides: dict[str, Any],
    findings: list[MigrationFinding],
) -> list[dict[str, Any]]:
    by_line = {pair.spans[0].start: pair for pair in pairs}
    manual = {int(line): text for line, text in overrides["appendixC"]["manualEnglishByLine"].items()}
    counters = Counters()
    groups: list[dict[str, Any]] = []

    def append_official(index: int) -> None:
        unit = official.units[index]
        line = next((line for line, mapped in APPENDIX_C_LINE_TO_OFFICIAL.items() if mapped == index), None)
        pair = by_line.get(line) if line is not None else None
        en, zh, kind = unit.text, pair.zh if pair else "", unit.kind
        if pair is not None and pair.kind == "bold":
            en, kind = f"**{en}**", "markdown"
        if kind == "list":
            en = _ensure_list(en)
            zh = _ensure_list(zh) if zh else ""
        groups.append(_make_group(counters, prefix, kind, [_make_block(counters, prefix, en, zh, pair)]))
        if pair is None:
            findings.append(
                MigrationFinding(
                    "missing-translation",
                    "error",
                    official.key,
                    "Official Appendix C text has no legacy translation.",
                    [],
                    unit.text,
                )
            )

    for index in range(23):
        append_official(index)

    sequence: list[int] = [4365, 4367, 4369, 4371, 4373, 4375]
    for line in sequence:
        pair = by_line[line]
        if pair.kind == "image":
            groups.append(_make_image_group(counters, prefix, pair, overrides["assets"]))
        else:
            groups.append(
                _make_group(
                    counters,
                    prefix,
                    "paragraph",
                    [_make_block(counters, prefix, manual[line], pair.zh, pair)],
                )
            )
            findings.append(
                MigrationFinding(
                    "manual-pdf-transcription",
                    "info",
                    official.key,
                    "English caption was transcribed from positioned PDF artwork.",
                    _source_lines(pair),
                    manual[line],
                )
            )

    append_official(23)
    for line in [4379, 4381, 4383, 4385]:
        pair = by_line[line]
        if pair.kind == "image":
            groups.append(_make_image_group(counters, prefix, pair, overrides["assets"]))
        else:
            groups.append(
                _make_group(
                    counters,
                    prefix,
                    "paragraph",
                    [_make_block(counters, prefix, manual[line], pair.zh, pair)],
                )
            )
            findings.append(
                MigrationFinding(
                    "manual-pdf-transcription",
                    "info",
                    official.key,
                    "English caption was transcribed from positioned PDF artwork.",
                    _source_lines(pair),
                    manual[line],
                )
            )
    for index in range(24, len(official.units)):
        append_official(index)

    unused = sorted(set(by_line) - set(APPENDIX_C_LINE_TO_OFFICIAL) - set(manual) - {4365, 4369, 4373, 4379, 4383})
    for line in unused:
        pair = by_line[line]
        findings.append(
            MigrationFinding(
                "legacy-only",
                "warning",
                official.key,
                "Legacy explanatory label is not present in the official PDF and was not emitted.",
                _source_lines(pair),
            )
        )
    return groups


def _parse_table(markdown: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        if not line.strip().startswith("|"):
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _compact_table(rows: list[list[str]]) -> list[list[str]]:
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    keep = [index for index in range(width) if any(row[index].strip(" -") for row in padded)]
    return [[row[index] for index in keep] for row in padded]


def _table_row(row: list[str]) -> str:
    return "| " + " | ".join(row) + " |"


def _appendix_f_groups(
    official: OfficialSection,
    pairs: list[LegacyPair],
    prefix: str,
    findings: list[MigrationFinding],
) -> list[dict[str, Any]]:
    content = [pair for pair in pairs if pair.kind != "image"]
    if len(official.units) != 2 or len(content) != 2:
        raise LegacyImportError("Appendix F layout no longer matches the reviewed migration")
    counters = Counters()
    groups = [
        _make_group(
            counters,
            prefix,
            "paragraph",
            [_make_block(counters, prefix, official.units[0].text, content[0].zh, content[0])],
        )
    ]
    en_rows = [
        [cell for cell in row if cell.strip()]
        for row in _parse_table(official.units[1].text)
    ]
    legacy_en_rows = _parse_table(content[1].en)
    zh_rows = _parse_table(content[1].zh)
    zh_by_program = {
        en_row[0]: zh_row
        for en_row, zh_row in zip(legacy_en_rows[2:], zh_rows[2:], strict=True)
        if en_row and zh_row
    }
    blocks: list[dict[str, Any]] = []
    header_en = "\n".join([_table_row(en_rows[0]), _table_row(["---"] * len(en_rows[0]))])
    header_zh = "\n".join([_table_row(zh_rows[0]), _table_row(["---"] * len(zh_rows[0]))])
    blocks.append(_make_block(counters, prefix, header_en, header_zh, content[1]))
    for row in en_rows[2:]:
        program = row[0]
        match = next((zh for label, zh in zh_by_program.items() if program in label), None)
        zh_text = _table_row(match) if match is not None else ""
        blocks.append(_make_block(counters, prefix, _table_row(row), zh_text))
        if match is None:
            findings.append(
                MigrationFinding(
                    "missing-translation",
                    "error",
                    official.key,
                    f"Appendix F table row has no legacy translation: {program}",
                    [],
                    _table_row(row),
                )
            )
    groups.append(_make_group(counters, prefix, "table", blocks))
    return groups


def _appendix_title(legacy: LegacySection) -> str:
    match = re.match(r"^#\s+附录[A-F](?:～|—|-)(.+)$", legacy.heading.text)
    if not match:
        raise LegacyImportError(f"Cannot parse appendix heading: {legacy.heading.text}")
    return match.group(1).strip()


def _chapter_file(
    number: int,
    legacy_by_key: dict[str, LegacySection],
    official_by_key: dict[str, OfficialSection],
    overrides: dict[str, Any],
    findings: list[MigrationFinding],
) -> dict[str, Any]:
    chapter_key = f"chapter-{number:02d}"
    chapter = legacy_by_key[chapter_key]
    section_keys = sorted(
        [key for key in official_by_key if key.startswith(f"{number}.")],
        key=lambda value: int(value.split(".")[1]),
    )
    return {
        "chapter": {
            "id": f"mtr-{number}",
            "chapter": f"{number}.",
            "en": CHAPTER_TITLES[number],
            "zh": _chapter_zh(chapter, number),
            "sections": [
                _body_section(
                    official_by_key[key],
                    legacy_by_key[key],
                    overrides,
                    findings,
                )
                for key in section_keys
            ],
        }
    }


def _standalone_file(
    key: str,
    legacy: LegacySection,
    official: OfficialSection,
    overrides: dict[str, Any],
    findings: list[MigrationFinding],
) -> dict[str, Any]:
    pairs, _ = pair_section(legacy)
    prefix = "mtr-introduction" if key == "introduction" else f"mtr-{key}"
    if key in {"introduction", "appendix-b", "appendix-e"}:
        groups = _positional_groups(official, pairs, prefix, overrides, findings)
    elif key == "appendix-a":
        groups = _appendix_a_groups(official, pairs, prefix, findings)
    elif key == "appendix-c":
        groups = _appendix_c_groups(official, pairs, prefix, overrides, findings)
    elif key == "appendix-d":
        groups = _appendix_d_groups(official, pairs, prefix, overrides, findings)
    elif key == "appendix-f":
        groups = _appendix_f_groups(official, pairs, prefix, findings)
    else:
        raise LegacyImportError(f"Unsupported standalone section: {key}")
    if key == "introduction":
        chapter = {
            "id": prefix,
            "chapter": "Introduction",
            "en": "Introduction",
            "zh": "引言",
            "groups": groups,
        }
    else:
        letter = key[-1].upper()
        chapter = {
            "id": prefix,
            "chapter": f"Appendix {letter}",
            "en": official.en,
            "zh": _appendix_title(legacy),
            "groups": groups,
        }
    return {"chapter": chapter}


def _dump_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            value,
            Dumper=SourceDumper,
            allow_unicode=True,
            sort_keys=False,
            width=100000,
            default_flow_style=False,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _report_path(path: Path) -> str:
    """Keep reports portable when the command was given a relative path."""

    return path.as_posix() if not path.is_absolute() else str(path.resolve())


def migrate(
    markdown_path: Path,
    official_pdf: Path,
    overrides_path: Path,
    output_dir: Path,
    report_path: Path,
    markdown_report_path: Path | None = None,
    *,
    official_sections: list[OfficialSection] | None = None,
    effective_date: str = "2025-11-10",
) -> dict[str, Any]:
    overrides = _load_yaml(overrides_path)
    expected_markdown = str(overrides["source"]["markdownSha256"]).lower()
    expected_pdf = str(overrides["source"]["officialPdfSha256"]).lower()
    actual_markdown = sha256_file(markdown_path)
    actual_pdf = sha256_file(official_pdf)
    if actual_markdown != expected_markdown:
        raise LegacyImportError(
            f"Legacy Markdown hash changed: expected {expected_markdown}, got {actual_markdown}"
        )
    if actual_pdf != expected_pdf:
        raise LegacyImportError(f"Official PDF hash changed: expected {expected_pdf}, got {actual_pdf}")

    legacy_sections, section_issues = split_sections(
        tokenize_legacy_markdown(markdown_path.read_text(encoding="utf-8"))
    )
    if any(issue.severity == "error" for issue in section_issues):
        raise LegacyImportError("Legacy Markdown has blocking section errors")
    if official_sections is None:
        official_sections = parse_official_pdf(official_pdf)
    legacy_by_key = {section.key: section for section in legacy_sections}
    official_by_key = {section.key: section for section in official_sections}
    findings: list[MigrationFinding] = []

    files: dict[str, dict[str, Any]] = {}
    files["introduction.yaml"] = _standalone_file(
        "introduction",
        legacy_by_key["introduction"],
        official_by_key["introduction"],
        overrides,
        findings,
    )
    for number in range(1, 11):
        files[f"chapter-{number:02d}.yaml"] = _chapter_file(
            number,
            legacy_by_key,
            official_by_key,
            overrides,
            findings,
        )
    for letter in "abcdef":
        key = f"appendix-{letter}"
        files[f"{key}.yaml"] = _standalone_file(
            key,
            legacy_by_key[key],
            official_by_key[key],
            overrides,
            findings,
        )

    manifest = {
        "schemaVersion": 1,
        "document": {
            "id": "mtr",
            "title": {
                "en": "Magic: The Gathering Tournament Rules",
                "zh": "万智牌比赛规则",
            },
            "version": effective_date.replace("-", ""),
            "effectiveDate": effective_date,
        },
        "files": list(files),
    }
    _dump_yaml(output_dir / "manifest.yaml", manifest)
    for filename, value in files.items():
        _dump_yaml(output_dir / filename, value)

    report = {
        "source": {
            "markdown": _report_path(markdown_path),
            "legacyRepositoryUrl": overrides["source"].get("legacyRepositoryUrl", ""),
            "markdownSha256": actual_markdown,
            "officialPdf": _report_path(official_pdf),
            "officialPdfUrl": overrides["source"].get("officialPdfUrl", ""),
            "officialPdfSha256": actual_pdf,
            "overrides": _report_path(overrides_path),
        },
        "output": _report_path(output_dir),
        "fileCount": len(files) + 1,
        "findings": [asdict(finding) for finding in findings],
        "summary": {
            severity: sum(1 for finding in findings if finding.severity == severity)
            for severity in ("error", "warning", "info")
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if markdown_report_path is None:
        markdown_report_path = report_path.with_suffix(".md")
    lines = [
        "# Legacy MTR migration report",
        "",
        f"- Legacy Markdown SHA-256: `{actual_markdown}`",
        f"- Legacy repository: {overrides['source'].get('legacyRepositoryUrl', 'not recorded')}",
        f"- Official PDF SHA-256: `{actual_pdf}`",
        f"- Official PDF: {overrides['source'].get('officialPdfUrl', 'not recorded')}",
        f"- Generated Schema v1 files: {len(files) + 1}",
        f"- Findings: {report['summary']['error']} errors, {report['summary']['warning']} warnings, {report['summary']['info']} info",
        "",
        "The generated YAML is a review draft while any error-level finding remains.",
        "",
    ]
    for severity in ("error", "warning", "info"):
        lines.extend([f"## {severity.title()} findings", ""])
        selected = [finding for finding in findings if finding.severity == severity]
        if not selected:
            lines.append("- None.")
        for finding in selected:
            source = (
                ", ".join(f"L{line}" for line in finding.source_lines)
                if finding.source_lines
                else "no legacy line"
            )
            text = f" — `{finding.official_text}`" if finding.official_text else ""
            lines.append(
                f"- `{finding.section}` **{finding.code}** ({source}): {finding.message}{text}"
            )
        lines.append("")
    markdown_report_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate the reviewed legacy AMTR into Schema v1 YAML.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--official-pdf", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("reports/legacy-migration.json"))
    parser.add_argument("--markdown-report", type=Path)
    parser.add_argument("--effective-date", default="2025-11-10")
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="return a non-zero status after writing the draft when error findings remain",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = migrate(
        args.source,
        args.official_pdf,
        args.overrides,
        args.output_dir,
        args.report,
        args.markdown_report,
        effective_date=args.effective_date,
    )
    summary = report["summary"]
    print(
        f"Wrote {report['fileCount']} YAML files; findings: "
        f"{summary['error']} error, {summary['warning']} warning, {summary['info']} info."
    )
    return 2 if args.fail_on_errors and summary["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
