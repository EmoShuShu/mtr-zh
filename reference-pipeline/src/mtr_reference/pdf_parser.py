from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_text

from .models import OfficialDocument, OfficialSection, OfficialUnit


class PdfParseError(RuntimeError):
    """Raised when the official PDF cannot be parsed without losing structure."""


@dataclass(frozen=True)
class _PdfLine:
    page: int
    top: float
    x0: float
    text: str
    bold: bool = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_pdf_text(value: str) -> str:
    return value.replace("\x02", "-").replace("\u00ad", "").replace("`", "").strip()


def _table_markdown(rows: list[list[str | None]]) -> str:
    cleaned = [
        [_clean_pdf_text(cell or "").replace("\n", "<br>") for cell in row]
        for row in rows
        if any((cell or "").strip() for cell in row)
    ]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join(
        [render(cleaned[0]), render(["---"] * width), *(render(row) for row in cleaned[1:])]
    )


def _section_heading(text: str, size: float) -> tuple[str, str] | None:
    if text == "Introduction":
        return "introduction", "Introduction"
    match = re.match(r"^((?:[1-9]|10)\.\d+)\s+(.+)$", text)
    if match:
        return match.group(1), match.group(2)
    match = re.match(r"^Appendix\s+([A-F])(?:—|-)(.+)$", text)
    if match:
        return f"appendix-{match.group(1).lower()}", match.group(2).strip()
    if size >= 13 and re.match(r"^\d+\.\s+", text):
        return "chapter", text
    return None


def _is_document_footer(page_number: int, page_count: int, size: float, top: float) -> bool:
    return page_number == page_count and size <= 9.5 and top >= 500


def _append_unit(
    section: OfficialSection | None,
    kind: str | None,
    lines: list[_PdfLine],
) -> None:
    if section is None or kind is None or not lines:
        return
    prefix = "* " if kind == "list" else ""
    pieces = [_clean_pdf_text(line.text).lstrip("• ") for line in lines]
    body = ""
    for piece in pieces:
        if body and body.endswith("-") and piece[:1].isalnum():
            body += piece
        else:
            body += (" " if body else "") + piece
    text = prefix + body.strip()
    if text:
        section.units.append(OfficialUnit(kind, text, tuple(sorted({line.page for line in lines}))))


def _validate_sections(sections: list[OfficialSection]) -> None:
    if not sections or sections[0].key != "introduction":
        raise PdfParseError("The parser did not find the MTR introduction")
    keys = [section.key for section in sections]
    if len(keys) != len(set(keys)):
        raise PdfParseError("The parser produced duplicate section keys")
    missing_appendices = [f"appendix-{letter}" for letter in "abcdef" if f"appendix-{letter}" not in keys]
    if missing_appendices:
        raise PdfParseError("Missing required appendices: " + ", ".join(missing_appendices))
    for chapter in range(1, 11):
        if not any(key.startswith(f"{chapter}.") for key in keys):
            raise PdfParseError(f"Missing chapter {chapter} sections")
    empty = [section.key for section in sections if not section.units]
    if empty:
        raise PdfParseError("Sections without parsed content: " + ", ".join(empty))


def parse_official_pdf(path: Path) -> OfficialDocument:
    """Parse an official English MTR PDF into deterministic structural units.

    The parser intentionally mirrors the proven production parser in the
    parent repository. It extracts running text, lists, tables, headings and
    page provenance. Positioned diagrams and formula artwork require project
    assets or manual overrides and are not silently represented as text.
    """

    path = path.resolve()
    if not path.is_file():
        raise PdfParseError(f"PDF does not exist: {path}")
    if path.read_bytes()[:5] != b"%PDF-":
        raise PdfParseError(f"Input is not a PDF: {path}")

    sections: list[OfficialSection] = []
    current: OfficialSection | None = None
    buffered: list[_PdfLine] = []
    buffered_kind: str | None = None
    previous: _PdfLine | None = None

    def flush() -> None:
        nonlocal buffered, buffered_kind
        _append_unit(current, buffered_kind, buffered)
        buffered = []
        buffered_kind = None

    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) < 4:
            raise PdfParseError("The MTR PDF is unexpectedly shorter than four pages")
        for page_index, page in enumerate(pdf.pages[3:], start=4):
            table_events: list[tuple[float, float, str]] = []
            for table in page.find_tables():
                markdown = _table_markdown(table.extract())
                if markdown:
                    table_events.append((float(table.bbox[1]), float(table.bbox[3]), markdown))

            raw_lines = page.extract_text_lines(return_chars=True)
            two_column_indexes: set[int] = set()
            for index, raw in enumerate(raw_lines):
                if str(raw["text"]).count("•") < 2:
                    continue
                start = index
                while start > 0 and float(raw_lines[start - 1]["x0"]) >= 80:
                    start -= 1
                end = index + 1
                while end < len(raw_lines) and float(raw_lines[end]["x0"]) >= 80:
                    end += 1
                two_column_indexes.update(range(start, end))

            events: list[tuple[float, str, object, int | None]] = [
                (top, "table", (bottom, markdown), None)
                for top, bottom, markdown in table_events
            ]
            for raw_index, raw in enumerate(raw_lines):
                top = float(raw["top"])
                if top >= 720:
                    continue
                if any(table_top <= top <= table_bottom for table_top, table_bottom, _ in table_events):
                    continue
                chars = raw.get("chars", [])
                size = max((float(char.get("size", 0)) for char in chars), default=0)
                if _is_document_footer(page_index, len(pdf.pages), size, top):
                    continue
                visible_chars = [char for char in chars if char.get("text", "").strip()]
                bold = bool(visible_chars) and all(
                    "Bold" in str(char.get("fontname", "")) for char in visible_chars
                )
                midpoint = float(page.width) / 2
                left = [char for char in chars if float(char["x0"]) < midpoint]
                right = [char for char in chars if float(char["x0"]) >= midpoint]
                if raw_index in two_column_indexes:
                    for column, segment in enumerate((left, right)):
                        segment_text = _clean_pdf_text(
                            extract_text(segment, x_tolerance=2, y_tolerance=3)
                        )
                        if segment_text:
                            events.append(
                                (
                                    top,
                                    "line",
                                    (
                                        _PdfLine(
                                            page_index,
                                            top,
                                            min(float(char["x0"]) for char in segment),
                                            segment_text,
                                            bold,
                                        ),
                                        size,
                                    ),
                                    column,
                                )
                            )
                else:
                    events.append(
                        (
                            top,
                            "line",
                            (
                                _PdfLine(
                                    page_index,
                                    top,
                                    float(raw["x0"]),
                                    _clean_pdf_text(raw["text"]),
                                    bold,
                                ),
                                size,
                            ),
                            None,
                        )
                    )

            ordered: list[tuple[float, str, object, int | None]] = []
            spatial = sorted(events, key=lambda event: event[0])
            position = 0
            while position < len(spatial):
                if spatial[position][3] is None:
                    ordered.append(spatial[position])
                    position += 1
                    continue
                end = position
                while end < len(spatial) and spatial[end][3] is not None:
                    end += 1
                ordered.extend(sorted(spatial[position:end], key=lambda event: (event[3], event[0])))
                position = end

            for _, event_kind, payload, _ in ordered:
                if event_kind == "table":
                    flush()
                    _, markdown = payload
                    if current is not None:
                        current.units.append(OfficialUnit("table", markdown, (page_index,)))
                    previous = None
                    continue

                line, size = payload
                heading = _section_heading(line.text, size)
                if heading is not None:
                    flush()
                    if heading[0] != "chapter":
                        current = OfficialSection(heading[0], heading[1])
                        sections.append(current)
                    previous = None
                    continue
                if current is None:
                    continue

                is_bullet = line.text.startswith("•") or (
                    current.key == "appendix-a" and 85 <= line.x0 < 105
                )
                kind = "list" if is_bullet else "paragraph"
                if line.bold and not is_bullet and current.key.startswith("appendix-"):
                    flush()
                    current.units.append(
                        OfficialUnit("markdown", f"**{line.text}**", (line.page,))
                    )
                    previous = line
                    continue

                starts_new = False
                if buffered_kind is not None and kind != buffered_kind:
                    if buffered_kind == "list" and not is_bullet and line.x0 >= 85:
                        kind = "list"
                    else:
                        starts_new = True
                if buffered and buffered_kind == "list" and is_bullet:
                    starts_new = True
                if buffered and previous is not None and line.page == previous.page:
                    if line.top - previous.top > 17.5:
                        starts_new = True
                if buffered and previous is not None and line.page != previous.page:
                    if re.search(r"[.!?][\"'’”)]?$", previous.text):
                        starts_new = True
                if starts_new:
                    flush()
                if not buffered:
                    buffered_kind = kind
                buffered.append(line)
                previous = line
        flush()

    for section in sections:
        expanded: list[OfficialUnit] = []
        for unit in section.units:
            if unit.kind == "paragraph" and re.match(r"^1\.\s", unit.text):
                parts = [
                    part.strip()
                    for part in re.split(r"(?=(?<!\S)\d+\.\s)", unit.text)
                    if part.strip()
                ]
                if len(parts) > 1:
                    expanded.extend(
                        OfficialUnit("markdown", part, unit.pages) for part in parts
                    )
                    continue
            expanded.append(unit)
        section.units = expanded

    _validate_sections(sections)
    return OfficialDocument(_sha256_file(path), sections)
