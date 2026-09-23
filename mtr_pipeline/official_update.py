from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from mtr_pipeline.core import PipelineError, Project, assemble_group, load_project
from mtr_pipeline.legacy_import import (
    LegacyPair,
    OfficialSection,
    OfficialUnit,
    SourceSpan,
    _match_normalize,
    align_section,
    parse_official_pdf,
    sha256_file,
)
from mtr_pipeline.legacy_migrate import SourceDumper, _partition_official
from mtr_pipeline.snapshot import (
    HASH_PREFIX_LENGTH,
    SnapshotError,
    _artifact_rows,
    _official_json,
    _official_markdown,
    _snapshot_path,
    _write_yaml,
    verify_snapshot,
)


DEFAULT_RULES_PAGE = "https://wpn.wizards.com/en/rules-documents"
EXPECTED_TITLE = "Magic: The Gathering Tournament Rules"
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024
ALLOWED_PAGE_HOST = "wpn.wizards.com"
ALLOWED_PDF_HOST = "media.wizards.com"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"-(?P<kind>[gb])(?P<number>\d+)$")
MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


class OfficialUpdateError(RuntimeError):
    """Raised when an official update cannot be processed without guessing."""


@dataclass(frozen=True)
class OfficialRelease:
    page_url: str
    title: str
    effective_date: str
    pdf_url: str
    pdf_sha256: str = ""


@dataclass
class UpdateFinding:
    code: str
    severity: str
    section: str
    message: str
    content_id: str = ""
    old_en: str = ""
    new_en: str = ""


class _RulesPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[dict[str, str]] = []
        self._document: dict[str, str] | None = None
        self._document_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = next((value for key, value in attrs if key == "class"), "") or ""
        return set(value.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "div" and any("downloadableDocument" in value for value in classes):
            if self._document is not None:
                raise OfficialUpdateError("Nested downloadable-document entries are unsupported")
            self._document = {"title": "", "date": "", "href": ""}
            self._document_depth = 1
            return
        if self._document is None:
            return
        if tag == "div":
            self._document_depth += 1
        if any("titleText" in value for value in classes):
            self._capture = "title"
            self._capture_depth = self._document_depth
        elif any("updated" in value for value in classes):
            self._capture = "date"
            self._capture_depth = self._document_depth
        elif tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if href:
                self._document["href"] = href

    def handle_data(self, data: str) -> None:
        if self._document is not None and self._capture is not None:
            self._document[self._capture] += data

    def handle_endtag(self, tag: str) -> None:
        if self._document is None or tag != "div":
            return
        if self._capture is not None and self._capture_depth == self._document_depth:
            self._capture = None
            self._capture_depth = 0
        self._document_depth -= 1
        if self._document_depth == 0:
            self.documents.append({key: value.strip() for key, value in self._document.items()})
            self._document = None


def _validate_https_url(url: str, expected_host: str, label: str) -> None:
    parsed = urllib.parse.urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise OfficialUpdateError(f"Unexpected {label} URL: {url}") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise OfficialUpdateError(f"Unexpected {label} URL: {url}")


def _parse_display_date(value: str) -> str:
    match = re.fullmatch(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})", value.strip())
    if match is None or match.group(1) not in MONTHS:
        raise OfficialUpdateError(f"Unrecognized official effective date: {value!r}")
    year = int(match.group(3))
    month = MONTHS[match.group(1)]
    day = int(match.group(2))
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError as exc:
        raise OfficialUpdateError(f"Invalid official effective date: {value!r}") from exc


def parse_rules_page(html: str, page_url: str = DEFAULT_RULES_PAGE) -> OfficialRelease:
    _validate_https_url(page_url, ALLOWED_PAGE_HOST, "rules-page")
    parser = _RulesPageParser()
    parser.feed(html)
    matches = [row for row in parser.documents if row["title"] == EXPECTED_TITLE]
    if len(matches) != 1:
        raise OfficialUpdateError(
            f"Expected exactly one {EXPECTED_TITLE!r} entry, found {len(matches)}"
        )
    row = matches[0]
    pdf_url = urllib.parse.urljoin(page_url, row["href"])
    _validate_https_url(pdf_url, ALLOWED_PDF_HOST, "official PDF")
    if not urllib.parse.urlparse(pdf_url).path.lower().endswith(".pdf"):
        raise OfficialUpdateError(f"Official MTR download is not a PDF URL: {pdf_url}")
    return OfficialRelease(
        page_url=page_url,
        title=row["title"],
        effective_date=_parse_display_date(row["date"]),
        pdf_url=pdf_url,
    )


def _download(url: str, destination: Path | None, *, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "mtr-zh-update-checker/1 (+https://github.com/EmoShuShu/mtr-zh)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            final_url = response.geturl()
            expected_host = ALLOWED_PAGE_HOST if destination is None else ALLOWED_PDF_HOST
            _validate_https_url(final_url, expected_host, "redirected download")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise OfficialUpdateError(f"Download exceeds {max_bytes} bytes: {url}")
    except (OSError, ValueError) as exc:
        if isinstance(exc, OfficialUpdateError):
            raise
        raise OfficialUpdateError(f"Unable to download {url}: {exc}") from exc
    payload = b"".join(chunks)
    if destination is not None:
        if not payload.startswith(b"%PDF-"):
            raise OfficialUpdateError("Official MTR download does not have a PDF file signature")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return payload


def discover_official_release(page_url: str = DEFAULT_RULES_PAGE) -> OfficialRelease:
    _validate_https_url(page_url, ALLOWED_PAGE_HOST, "rules-page")
    html = _download(page_url, None, max_bytes=MAX_PAGE_BYTES).decode("utf-8")
    return parse_rules_page(html, page_url)


def download_official_pdf(release: OfficialRelease, destination: Path) -> OfficialRelease:
    _download(release.pdf_url, destination, max_bytes=MAX_PDF_BYTES)
    return OfficialRelease(**{**asdict(release), "pdf_sha256": sha256_file(destination)})


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OfficialUpdateError(f"YAML root must be an object: {path}")
    return value


def load_source_state(path: Path) -> OfficialRelease:
    value = _load_yaml(path)
    if value.get("schemaVersion") != 1:
        raise OfficialUpdateError(f"Unsupported official source state: {path}")
    try:
        release = OfficialRelease(
            page_url=value["pageUrl"],
            title=value["title"],
            effective_date=value["effectiveDate"],
            pdf_url=value["pdfUrl"],
            pdf_sha256=value["pdfSha256"],
        )
    except KeyError as exc:
        raise OfficialUpdateError(f"Official source state is missing {exc.args[0]}: {path}") from exc
    if release.title != EXPECTED_TITLE:
        raise OfficialUpdateError(f"Unexpected stored official title: {release.title!r}")
    _validate_https_url(release.page_url, ALLOWED_PAGE_HOST, "rules-page")
    _validate_https_url(release.pdf_url, ALLOWED_PDF_HOST, "official PDF")
    if not DATE_RE.fullmatch(release.effective_date):
        raise OfficialUpdateError(f"Invalid stored effective date: {release.effective_date}")
    if not re.fullmatch(r"[0-9a-f]{64}", release.pdf_sha256):
        raise OfficialUpdateError("Stored official PDF SHA-256 is invalid")
    return release


def write_source_state(path: Path, release: OfficialRelease) -> None:
    _write_yaml(
        path,
        {
            "schemaVersion": 1,
            "pageUrl": release.page_url,
            "title": release.title,
            "effectiveDate": release.effective_date,
            "pdfUrl": release.pdf_url,
            "pdfSha256": release.pdf_sha256,
        },
    )


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


def _pair_for_group(group: dict[str, Any], index: int) -> LegacyPair:
    return LegacyPair(
        kind=group["type"],
        en=assemble_group(group, "en"),
        zh=assemble_group(group, "zh"),
        spans=[SourceSpan(index + 1, index + 1)],
    )


def _table_rows(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip().startswith("|")]


def _table_key(row: str) -> str:
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    return _match_normalize(cells[0] if cells else row)


def _rebase_table_group(
    group: dict[str, Any],
    unit: OfficialUnit,
    allocator: _IdAllocator,
    section: str,
    findings: list[UpdateFinding],
) -> dict[str, Any]:
    result = copy.deepcopy(group)
    official_rows = _table_rows(unit.text)
    if len(official_rows) < 2:
        raise OfficialUpdateError(f"Official table in {section} has no separator row")
    old_blocks = result["blocks"]
    if not old_blocks:
        raise OfficialUpdateError(f"Existing table in {section} has no blocks")

    header = old_blocks[0]
    old_header_rows = _table_rows(header["en"])
    header["en"] = "\n".join(official_rows[:2])
    if len(old_header_rows) < 2:
        header["zh"] = ""
        findings.append(
            UpdateFinding(
                "table-header-review",
                "error",
                section,
                "The existing translated table header is malformed.",
                header["id"],
            )
        )

    available: dict[str, list[dict[str, Any]]] = {}
    for block in old_blocks[1:]:
        rows = _table_rows(block["en"])
        if rows:
            available.setdefault(_table_key(rows[0]), []).append(block)

    blocks = [header]
    for row in official_rows[2:]:
        key = _table_key(row)
        matches = available.get(key, [])
        if matches:
            block = matches.pop(0)
            block["en"] = row
        else:
            block = {"id": allocator.block_id(), "en": row, "zh": ""}
            findings.append(
                UpdateFinding(
                    "new-table-row",
                    "error",
                    section,
                    "Official table row has no Chinese translation.",
                    block["id"],
                    new_en=row,
                )
            )
        blocks.append(block)
    for rows in available.values():
        for block in rows:
            findings.append(
                UpdateFinding(
                    "removed-table-row",
                    "warning",
                    section,
                    "Previously translated table row is absent from the new official PDF.",
                    block["id"],
                    old_en=block["en"],
                )
            )
    result["blocks"] = blocks
    return result


def _decorate_like_existing(target: str, existing: str) -> str:
    stripped = existing.strip()
    if stripped.startswith("**") and stripped.endswith("**"):
        return f"**{target}**"
    return target


def _rebase_matched_group(
    group: dict[str, Any],
    unit: OfficialUnit,
    allocator: _IdAllocator,
    section: str,
    findings: list[UpdateFinding],
) -> dict[str, Any]:
    old_en = assemble_group(group, "en")
    if (
        group["type"] == "table"
        and len(group["blocks"]) > 1
        and len(_table_rows(unit.text)) >= 2
    ):
        result = _rebase_table_group(group, unit, allocator, section, findings)
    else:
        result = copy.deepcopy(group)
        target = _decorate_like_existing(unit.text, old_en)
        blocks = result["blocks"]
        if len(blocks) == 1:
            blocks[0]["en"] = target
            blocks[0].pop("joinAfter", None)
        else:
            parts, delimiters = _partition_official(
                target,
                [block["en"] for block in blocks],
                None,
            )
            for index, (block, part) in enumerate(zip(blocks, parts, strict=True)):
                block["en"] = part
                if index < len(delimiters):
                    join_after = block.setdefault("joinAfter", {})
                    join_after["en"] = delimiters[index]
                    join_after.setdefault("zh", "")
                else:
                    block.pop("joinAfter", None)
    new_en = assemble_group(result, "en")
    if old_en != new_en:
        findings.append(
            UpdateFinding(
                "official-text-changed",
                "warning",
                section,
                "Official English changed; retained Chinese requires exact review.",
                group["id"],
                old_en,
                new_en,
            )
        )
    return result


def _new_group(
    unit: OfficialUnit,
    allocator: _IdAllocator,
    section: str,
    findings: list[UpdateFinding],
) -> dict[str, Any]:
    block = {"id": allocator.block_id(), "en": unit.text, "zh": ""}
    group = {"id": allocator.group_id(), "type": unit.kind, "blocks": [block]}
    findings.append(
        UpdateFinding(
            "new-official-content",
            "error",
            section,
            "Official content has no inherited Chinese translation.",
            block["id"],
            new_en=unit.text,
        )
    )
    return group


def _rebase_groups(
    old_groups: list[dict[str, Any]],
    official: OfficialSection,
    prefix: str,
    findings: list[UpdateFinding],
) -> list[dict[str, Any]]:
    allocator = _IdAllocator(prefix, old_groups)
    text_groups: list[dict[str, Any]] = []
    images_by_anchor: dict[int, list[dict[str, Any]]] = {}
    for group in old_groups:
        if group["type"] == "image":
            images_by_anchor.setdefault(len(text_groups), []).append(copy.deepcopy(group))
        else:
            text_groups.append(group)
    pairs = [_pair_for_group(group, index) for index, group in enumerate(text_groups)]
    steps = align_section(official, pairs, max_legacy_per_unit=1)
    result: list[dict[str, Any]] = list(images_by_anchor.get(0, []))
    authoritative_groups: list[dict[str, Any]] = []
    consumed = 0
    for step in steps:
        if step.operation == "missing-official":
            group = _new_group(official.units[step.official[0]], allocator, official.key, findings)
            result.append(group)
            authoritative_groups.append(group)
        elif step.operation == "legacy-only":
            group = text_groups[step.legacy[0]]
            artwork_caption = official.key == "appendix-c" and (
                step.legacy[0] in images_by_anchor or step.legacy[0] + 1 in images_by_anchor
            )
            if artwork_caption:
                result.append(copy.deepcopy(group))
                findings.append(
                    UpdateFinding(
                        "preserved-positioned-artwork-caption",
                        "info",
                        official.key,
                        "Caption adjacent to PDF artwork cannot be extracted as running text and was preserved for review.",
                        group["id"],
                        old_en=assemble_group(group, "en"),
                        new_en=assemble_group(group, "en"),
                    )
                )
            else:
                findings.append(
                    UpdateFinding(
                        "removed-official-content",
                        "warning",
                        official.key,
                        "Previously translated content is absent from the new official PDF and was omitted.",
                        group["id"],
                        old_en=assemble_group(group, "en"),
                    )
                )
            consumed = max(consumed, step.legacy[0] + 1)
        else:
            group = text_groups[step.legacy[0]]
            rebased = _rebase_matched_group(
                group,
                official.units[step.official[0]],
                allocator,
                official.key,
                findings,
            )
            result.append(rebased)
            authoritative_groups.append(rebased)
            consumed = max(consumed, step.legacy[0] + 1)
        result.extend(images_by_anchor.get(consumed, []))
    for anchor in sorted(index for index in images_by_anchor if index > consumed):
        result.extend(images_by_anchor[anchor])
    if len(authoritative_groups) != len(official.units):
        raise OfficialUpdateError(
            f"Official unit coverage mismatch in {official.key}: "
            f"official={len(official.units)}, emitted={len(authoritative_groups)}"
        )
    for index, (group, unit) in enumerate(zip(authoritative_groups, official.units, strict=True)):
        actual = assemble_group(group, "en")
        if actual.startswith("**") and actual.endswith("**"):
            actual = actual[2:-2]
        matches = (
            _match_normalize(actual) == _match_normalize(unit.text)
            if group["type"] == "table"
            else actual == unit.text
        )
        if not matches:
            raise OfficialUpdateError(
                f"Authoritative English reconstruction failed in {official.key} unit {index + 1}:\n"
                f"expected: {unit.text}\nactual:   {actual}"
            )
    return result


def _source_sections(project: Project) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    sections: dict[str, dict[str, Any]] = {}
    chapters: dict[int, dict[str, Any]] = {}
    for source in project.sources:
        chapter = source.data["chapter"]
        label = chapter["chapter"]
        if label == "Introduction":
            sections["introduction"] = chapter
        elif match := re.fullmatch(r"(\d+)\.", label):
            number = int(match.group(1))
            chapters[number] = chapter
            for section in chapter["sections"]:
                sections[section["chapter"]] = section
        elif match := re.fullmatch(r"Appendix ([A-F])", label):
            sections[f"appendix-{match.group(1).lower()}"] = chapter
        else:
            raise OfficialUpdateError(f"Unsupported source chapter label: {label}")
    return sections, chapters


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


_DIFF_TOKEN_RE = re.compile(r"\s+|[\w]+(?:[’'][\w]+)*|[^\w\s]", re.UNICODE)


def _exact_text_changes(old: str, new: str) -> list[dict[str, str]]:
    """Return an exact, reversible lexical diff without semantic scoring."""

    old_tokens = _DIFF_TOKEN_RE.findall(old)
    new_tokens = _DIFF_TOKEN_RE.findall(new)
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    changes: list[dict[str, str]] = []
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation == "equal":
            changes.append({"op": "equal", "text": "".join(old_tokens[old_start:old_end])})
        elif operation == "delete":
            changes.append({"op": "delete", "text": "".join(old_tokens[old_start:old_end])})
        elif operation == "insert":
            changes.append({"op": "insert", "text": "".join(new_tokens[new_start:new_end])})
        else:
            changes.append({"op": "delete", "text": "".join(old_tokens[old_start:old_end])})
            changes.append({"op": "insert", "text": "".join(new_tokens[new_start:new_end])})
    return [change for change in changes if change["text"]]


def _render_exact_text_changes(changes: list[dict[str, str]]) -> str:
    rendered: list[str] = []
    for change in changes:
        text = change["text"]
        if change["op"] == "delete":
            rendered.append(f"[-{text}-]")
        elif change["op"] == "insert":
            rendered.append(f"{{+{text}+}}")
        else:
            rendered.append(text)
    return "".join(rendered)


def _write_update_report(
    path: Path,
    base_version: str,
    release: OfficialRelease,
    findings: list[UpdateFinding],
) -> dict[str, Any]:
    summary = {
        severity: sum(finding.severity == severity for finding in findings)
        for severity in ("error", "warning", "info")
    }
    finding_rows = []
    for finding in findings:
        row = asdict(finding)
        row["changes"] = (
            _exact_text_changes(finding.old_en, finding.new_en)
            if finding.old_en != finding.new_en
            else []
        )
        finding_rows.append(row)
    report = {
        "baseVersion": base_version,
        "effectiveDate": release.effective_date,
        "officialPdfUrl": release.pdf_url,
        "officialPdfSha256": release.pdf_sha256,
        "summary": summary,
        "findings": finding_rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Official MTR update review",
        "",
        f"- Base version: `{base_version}`",
        f"- New effective date: `{release.effective_date}`",
        f"- Official PDF: {release.pdf_url}",
        f"- Official PDF SHA-256: `{release.pdf_sha256}`",
        f"- Findings: {summary['error']} errors, {summary['warning']} warnings, {summary['info']} info",
        "",
        "This candidate was generated from the current reviewed YAML. All changed English and inherited Chinese must be checked before the draft PR is marked ready.",
        "",
    ]
    for severity in ("error", "warning", "info"):
        lines.extend([f"## {severity.title()} findings", ""])
        selected = [finding for finding in findings if finding.severity == severity]
        if not selected:
            lines.append("- None.")
        for finding in selected:
            suffix = f" (`{finding.content_id}`)" if finding.content_id else ""
            lines.append(f"- `{finding.section}` **{finding.code}**{suffix}: {finding.message}")
            if finding.old_en != finding.new_en:
                exact_diff = _render_exact_text_changes(
                    _exact_text_changes(finding.old_en, finding.new_en)
                )
                lines.extend(
                    [
                        "",
                        "  Exact diff (`[-deleted-]`, `{+inserted+}`):",
                        "",
                        "```text",
                        exact_diff,
                        "```",
                        "",
                    ]
                )
        lines.append("")
    path.with_suffix(".md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report


def rebase_project(
    project: Project,
    official_sections: list[OfficialSection],
    release: OfficialRelease,
    output_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    old_sections, chapters = _source_sections(project)
    official_by_key = {section.key: section for section in official_sections}
    if len(official_by_key) != len(official_sections):
        raise OfficialUpdateError("Official PDF parser produced duplicate section keys")
    required_standalone = {"introduction", *(f"appendix-{letter}" for letter in "abcdef")}
    missing = sorted(required_standalone - set(official_by_key))
    if missing:
        raise OfficialUpdateError("Official PDF parser did not find required sections: " + ", ".join(missing))
    unsupported = sorted(
        key
        for key in official_by_key
        if key not in required_standalone and not re.fullmatch(r"(?:[1-9]|10)\.\d+", key)
    )
    if unsupported:
        raise OfficialUpdateError(
            "Official PDF contains unsupported sections that must not be ignored: "
            + ", ".join(unsupported)
        )

    findings: list[UpdateFinding] = []
    files: dict[str, dict[str, Any]] = {}
    intro = copy.deepcopy(old_sections["introduction"])
    intro["en"] = official_by_key["introduction"].en
    intro["groups"] = _rebase_groups(
        intro["groups"], official_by_key["introduction"], intro["id"], findings
    )
    files["introduction.yaml"] = {"chapter": intro}

    for number in range(1, 11):
        old_chapter = copy.deepcopy(chapters[number])
        old_by_key = {section["chapter"]: section for section in old_chapter["sections"]}
        official_keys = sorted(
            (key for key in official_by_key if re.fullmatch(rf"{number}\.\d+", key)),
            key=lambda key: int(key.split(".")[1]),
        )
        new_sections: list[dict[str, Any]] = []
        for key in official_keys:
            official = official_by_key[key]
            if key in old_by_key:
                section = copy.deepcopy(old_by_key[key])
                section["en"] = official.en
                section["groups"] = _rebase_groups(section["groups"], official, section["id"], findings)
            else:
                prefix = f"mtr-{key}"
                allocator = _IdAllocator(prefix, [])
                section = {
                    "id": prefix,
                    "chapter": key,
                    "en": official.en,
                    "zh": f"待翻译：{official.en}",
                    "groups": [
                        _new_group(unit, allocator, key, findings) for unit in official.units
                    ],
                }
            new_sections.append(section)
        removed_keys = sorted(set(old_by_key) - set(official_keys))
        for key in removed_keys:
            findings.append(
                UpdateFinding(
                    "removed-section",
                    "warning",
                    key,
                    "Previously translated section is absent from the new official PDF and was omitted.",
                    old_by_key[key]["id"],
                )
            )
        old_chapter["sections"] = new_sections
        files[f"chapter-{number:02d}.yaml"] = {"chapter": old_chapter}

    for letter in "abcdef":
        key = f"appendix-{letter}"
        appendix = copy.deepcopy(old_sections[key])
        official = official_by_key[key]
        appendix["en"] = official.en
        appendix["groups"] = _rebase_groups(appendix["groups"], official, appendix["id"], findings)
        files[f"appendix-{letter}.yaml"] = {"chapter": appendix}

    manifest = copy.deepcopy(project.manifest)
    manifest["document"]["version"] = release.effective_date.replace("-", "")
    manifest["document"]["effectiveDate"] = release.effective_date
    manifest["files"] = list(files)
    _dump_yaml(output_dir / "manifest.yaml", manifest)
    for filename, value in files.items():
        _dump_yaml(output_dir / filename, value)
    return _write_update_report(
        report_path,
        project.manifest["document"]["effectiveDate"],
        release,
        findings,
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _copy_project_assets(project: Project, destination_root: Path) -> None:
    copied: set[str] = set()
    for source in project.sources:
        chapter = source.data["chapter"]
        groups = list(chapter.get("groups", []))
        for section in chapter.get("sections", []):
            groups.extend(section["groups"])
        for group in groups:
            if group["type"] != "image":
                continue
            for block in group["blocks"]:
                relative = Path(block["asset"])
                normalized = relative.as_posix()
                if normalized in copied:
                    continue
                source_path = (project.root / relative).resolve()
                try:
                    source_path.relative_to(project.root.resolve())
                except ValueError as exc:
                    raise OfficialUpdateError(f"Asset escapes project root: {relative}") from exc
                if not source_path.is_file():
                    raise OfficialUpdateError(f"Referenced asset does not exist: {relative}")
                destination = destination_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, destination)
                copied.add(normalized)


def create_update_snapshot(
    *,
    official_pdf: Path,
    release: OfficialRelease,
    base_manifest: Path,
    schema: Path,
    project_root: Path,
    snapshot_root: Path,
) -> tuple[Path, bool, dict[str, Any]]:
    project_root = project_root.resolve()
    actual_pdf_sha256 = sha256_file(official_pdf)
    if actual_pdf_sha256 != release.pdf_sha256:
        raise OfficialUpdateError(
            f"Downloaded official PDF hash changed: expected {release.pdf_sha256}, got {actual_pdf_sha256}"
        )
    project = load_project(base_manifest, schema, project_root, require_complete=True)
    final_path = _snapshot_path(snapshot_root.resolve(), release.effective_date, release.pdf_sha256)
    if final_path.exists():
        manifest = verify_snapshot(final_path, release.pdf_sha256)
        expected_base = manifest.get("inputs", {}).get("base", {}).get("treeSha256")
        if expected_base != _tree_digest(project.manifest_path.parent):
            raise SnapshotError("Existing update snapshot was created from a different base version")
        return final_path, False, manifest

    final_path.parent.mkdir(parents=True, exist_ok=True)
    stage = final_path.parent / f".{final_path.name}.staging-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        snapshot_pdf = stage / "official/MTR_EN.pdf"
        snapshot_pdf.parent.mkdir(parents=True)
        shutil.copyfile(official_pdf, snapshot_pdf)
        shutil.copytree(project.manifest_path.parent, stage / "inputs/base")
        _copy_project_assets(project, stage)
        official_sections = parse_official_pdf(snapshot_pdf)
        extracted = _official_json(official_sections, release.pdf_sha256)
        extracted_path = stage / "extracted/official.json"
        extracted_path.parent.mkdir(parents=True)
        extracted_path.write_text(
            json.dumps(extracted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "extracted/official.md").write_text(
            _official_markdown(official_sections, release.pdf_sha256), encoding="utf-8"
        )
        report = rebase_project(
            project,
            official_sections,
            release,
            stage / "candidate",
            stage / "comparison/update.json",
        )
        load_project(stage / "candidate/manifest.yaml", schema, stage, require_complete=False)
        manifest = {
            "snapshotVersion": 1,
            "snapshotId": f"{release.effective_date}/{release.pdf_sha256[:HASH_PREFIX_LENGTH]}",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "document": {"id": "mtr", "effectiveDate": release.effective_date},
            "official": {
                "pageUrl": release.page_url,
                "url": release.pdf_url,
                "path": "official/MTR_EN.pdf",
                "sha256": release.pdf_sha256,
                "size": snapshot_pdf.stat().st_size,
            },
            "inputs": {
                "base": {
                    "version": project.manifest["document"]["effectiveDate"],
                    "path": "inputs/base",
                    "treeSha256": _tree_digest(stage / "inputs/base"),
                }
            },
            "processing": {
                "schemaVersion": 1,
                "officialSectionCount": extracted["sectionCount"],
                "officialUnitCount": extracted["unitCount"],
                "implementation": {
                    "mtr_pipeline/official_update.py": sha256_file(
                        project_root / "mtr_pipeline/official_update.py"
                    ),
                    "mtr_pipeline/legacy_import.py": sha256_file(
                        project_root / "mtr_pipeline/legacy_import.py"
                    ),
                },
            },
            "status": {"state": "review-required", "findings": report["summary"]},
            "artifacts": _artifact_rows(stage),
        }
        _write_yaml(stage / "snapshot.yaml", manifest)
        os.replace(stage, final_path)
        return final_path, True, manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def promote_candidate(snapshot_path: Path, editable_output: Path, base_source: Path) -> str:
    candidate = snapshot_path / "candidate"
    expected_base = snapshot_path / "inputs/base"
    if _tree_digest(base_source) != _tree_digest(expected_base):
        raise OfficialUpdateError("Editable base changed after the update snapshot was created")
    if not editable_output.exists():
        editable_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidate, editable_output)
        return "created"
    if editable_output.resolve() != base_source.resolve():
        raise OfficialUpdateError(f"Refusing to replace unrelated editable directory: {editable_output}")
    temporary = editable_output.parent / f".{editable_output.name}.update-{uuid.uuid4().hex}"
    backup = editable_output.parent / f".{editable_output.name}.backup-{uuid.uuid4().hex}"
    shutil.copytree(candidate, temporary)
    os.replace(editable_output, backup)
    try:
        os.replace(temporary, editable_output)
    except Exception:
        os.replace(backup, editable_output)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    shutil.rmtree(backup)
    return "updated"


def _within_project(path: Path, project_root: Path, label: str) -> Path:
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise OfficialUpdateError(f"{label} must stay inside the project root: {resolved}") from exc
    return resolved


def check_for_update(
    *,
    page_url: str,
    source_state_path: Path,
    current_version_path: Path,
    schema: Path,
    project_root: Path,
    snapshot_root: Path,
    result_path: Path,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    source_state_path = _within_project(source_state_path, project_root, "Source state")
    current_version_path = _within_project(current_version_path, project_root, "Version pointer")
    schema = _within_project(schema, project_root, "Schema")
    snapshot_root = _within_project(snapshot_root, project_root, "Snapshot root")
    result_path = _within_project(result_path, project_root, "Result file")
    previous = load_source_state(source_state_path)
    discovered = discover_official_release(page_url)
    if discovered.effective_date < previous.effective_date:
        raise OfficialUpdateError(
            f"Official page would downgrade MTR from {previous.effective_date} to {discovered.effective_date}"
        )
    with tempfile.TemporaryDirectory(prefix="mtr-official-update-") as directory:
        pdf_path = Path(directory) / "MTR_EN.pdf"
        release = download_official_pdf(discovered, pdf_path)
        if release.pdf_sha256 == previous.pdf_sha256:
            result = {"changed": False, "effectiveDate": release.effective_date, "pdfSha256": release.pdf_sha256}
        else:
            current_version = current_version_path.read_text(encoding="utf-8").strip()
            if not DATE_RE.fullmatch(current_version):
                raise OfficialUpdateError(f"Invalid current version pointer: {current_version!r}")
            base_source = project_root / "src/mtr" / current_version
            snapshot_path, created, manifest = create_update_snapshot(
                official_pdf=pdf_path,
                release=release,
                base_manifest=base_source / "manifest.yaml",
                schema=schema,
                project_root=project_root,
                snapshot_root=snapshot_root,
            )
            editable_output = project_root / "src/mtr" / release.effective_date
            promotion = promote_candidate(snapshot_path, editable_output, base_source)
            current_version_path.write_text(release.effective_date + "\n", encoding="utf-8", newline="\n")
            write_source_state(source_state_path, release)
            report_relative = (snapshot_path / "comparison/update.md").relative_to(project_root)
            result = {
                "changed": True,
                "effectiveDate": release.effective_date,
                "pdfSha256": release.pdf_sha256,
                "pdfUrl": release.pdf_url,
                "snapshot": snapshot_path.relative_to(project_root).as_posix(),
                "report": report_relative.as_posix(),
                "snapshotCreated": created,
                "promotion": promotion,
                "findings": manifest["status"]["findings"],
                "branch": f"automation/mtr-{release.effective_date}-{release.pdf_sha256[:12]}",
            }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect and prepare an official MTR update")
    parser.add_argument("--page-url", default=DEFAULT_RULES_PAGE)
    parser.add_argument("--source-state", type=Path, default=Path("src/mtr/official-source.yaml"))
    parser.add_argument("--current-version", type=Path, default=Path("src/mtr/current-version.txt"))
    parser.add_argument("--schema", type=Path, default=Path("schema/mtr-source.schema.json"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--snapshot-root", type=Path, default=Path("snapshots"))
    parser.add_argument("--result", type=Path, default=Path("tmp/official-update-result.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        check_for_update(
            page_url=args.page_url,
            source_state_path=args.source_state,
            current_version_path=args.current_version,
            schema=args.schema,
            project_root=args.project_root,
            snapshot_root=args.snapshot_root,
            result_path=args.result,
        )
    except (OfficialUpdateError, SnapshotError, PipelineError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
