from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

import pdfplumber
from pdfplumber.utils import extract_text


HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"^!\[(?P<alt>[^]]*)]\((?P<asset>[^)]+)\)\s*$")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
SECTION_RE = re.compile(r"^MTR\s+(?P<number>\d+\.\d+)\s+(?P<title>.+)$")
CHAPTER_RE = re.compile(r"^MTR\s+(?P<number>\d+)\.\s+(?P<title>.+)$")
APPENDIX_RE = re.compile(r"^附录(?P<letter>[A-F])(?:～|—|-)(?P<title>.+)$")

BlockKind = Literal["heading", "paragraph", "list", "quote", "table", "image", "bold"]


class LegacyImportError(RuntimeError):
    """Raised when a legacy source cannot be interpreted without guessing."""


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int


@dataclass(frozen=True)
class LegacyBlock:
    kind: BlockKind
    text: str
    span: SourceSpan


@dataclass
class Issue:
    code: str
    message: str
    severity: Literal["error", "warning", "info"]
    spans: list[SourceSpan] = field(default_factory=list)


@dataclass
class LegacyPair:
    kind: str
    en: str
    zh: str
    spans: list[SourceSpan]
    annotation_en: str = ""
    annotation_zh: str = ""
    asset: str = ""


@dataclass
class LegacySection:
    key: str
    heading: LegacyBlock
    blocks: list[LegacyBlock] = field(default_factory=list)


@dataclass(frozen=True)
class PdfLine:
    page: int
    top: float
    x0: float
    text: str
    bold: bool = False


@dataclass(frozen=True)
class OfficialUnit:
    kind: str
    text: str
    pages: tuple[int, ...]


@dataclass
class OfficialSection:
    key: str
    en: str
    units: list[OfficialUnit] = field(default_factory=list)


@dataclass(frozen=True)
class AlignmentStep:
    operation: Literal["match", "missing-official", "legacy-only"]
    official: tuple[int, ...]
    legacy: tuple[int, ...]
    similarity: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind_for_line(line: str, current_kind: BlockKind | None) -> BlockKind:
    if line.startswith(">") or current_kind == "quote":
        return "quote"
    if line.startswith("|") or current_kind == "table":
        return "table"
    if IMAGE_RE.match(line):
        return "image"
    if line in {"*", "-"} or line.startswith("* ") or line.startswith("- "):
        return "list"
    if line.startswith("**") and line.endswith("**"):
        return "bold"
    return "paragraph"


def tokenize_legacy_markdown(text: str) -> list[LegacyBlock]:
    """Tokenize legacy Markdown while preserving an exact line coverage ledger.

    Headings are always split even when the old document omitted a blank line.
    Within a nonblank run, quote/table continuation lines inherit the first
    line's type. List markers each begin a separate item.
    """

    blocks: list[LegacyBlock] = []
    buffer: list[str] = []
    start = 0
    kind: BlockKind | None = None

    def flush(end: int) -> None:
        nonlocal buffer, start, kind
        if buffer and kind is not None:
            blocks.append(LegacyBlock(kind, "\n".join(buffer), SourceSpan(start, end)))
        buffer = []
        start = 0
        kind = None

    lines = text.splitlines()
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        heading = HEADING_RE.match(line)
        if heading:
            flush(number - 1)
            blocks.append(LegacyBlock("heading", line, SourceSpan(number, number)))
            continue
        if not line.strip():
            flush(number - 1)
            continue

        next_kind = _kind_for_line(line, kind)
        if next_kind == "list" and kind == "list" and line.startswith(("* ", "- ")):
            flush(number - 1)
            next_kind = "list"
        elif kind is not None and next_kind != kind:
            flush(number - 1)
            next_kind = _kind_for_line(line, None)

        if not buffer:
            start = number
            kind = next_kind
        buffer.append(line)

    flush(len(lines))
    return blocks


def _heading_key(block: LegacyBlock) -> str | None:
    match = HEADING_RE.match(block.text)
    if not match:
        return None
    level, title = match.groups()
    if title == "引言":
        return "introduction"
    if level == "#":
        chapter = CHAPTER_RE.match(title)
        if chapter:
            return f"chapter-{int(chapter.group('number')):02d}"
        appendix = APPENDIX_RE.match(title)
        if appendix:
            return f"appendix-{appendix.group('letter').lower()}"
    section = SECTION_RE.match(title)
    if level == "##" and section:
        return section.group("number")
    return None


def split_sections(blocks: Iterable[LegacyBlock]) -> tuple[list[LegacySection], list[Issue]]:
    sections: list[LegacySection] = []
    issues: list[Issue] = []
    current: LegacySection | None = None
    for block in blocks:
        if block.kind == "heading":
            key = _heading_key(block)
            if key is not None:
                current = LegacySection(key=key, heading=block)
                sections.append(current)
                continue
            if current is not None:
                current.blocks.append(block)
            continue
        if current is not None:
            current.blocks.append(block)

    keys = [section.key for section in sections]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        issues.append(
            Issue(
                code="duplicate-section",
                severity="error",
                message="Duplicate structural sections: " + ", ".join(duplicates),
            )
        )
    return sections, issues


def _strip_marker(block: LegacyBlock) -> str:
    if block.kind == "list":
        return re.sub(r"^[*-]\s*", "", block.text, count=1).strip()
    return block.text.strip()


def _split_inline_bilingual(value: str) -> tuple[str, str] | None:
    match = HAN_RE.search(value)
    if not match or not LATIN_RE.search(value[: match.start()]):
        return None
    en = value[: match.start()].rstrip(" /—–-：:")
    zh = value[match.start() :].strip()
    if not en or not zh:
        return None
    return en, zh


def _split_slash_bilingual(value: str) -> tuple[str, str] | None:
    """Find the English lookup name in legacy `中文/English` list entries.

    The complete original string remains the Chinese rendering so that no
    card-name or effective-date information is discarded.
    """

    if not HAN_RE.search(value) or "/" not in value:
        return None
    _, candidate = value.split("/", 1)
    candidate = re.split(r"\s*\([^)]*[\u3400-\u9fff][^)]*\)\s*$", candidate)[0].strip()
    if not candidate or not LATIN_RE.search(candidate) or HAN_RE.search(candidate):
        return None
    return candidate, value


def _split_suffix_bilingual(value: str) -> tuple[str, str] | None:
    if not HAN_RE.match(value):
        return None
    match = re.search(r"[A-Z][A-Za-z0-9’' :,.-]+$", value)
    if match is None or HAN_RE.search(value[match.start() :]):
        return None
    candidate = value[match.start() :].strip()
    if len(candidate.split()) < 2:
        return None
    return candidate, value


def _split_bilingual_table(value: str) -> tuple[str, str] | None:
    en_lines: list[str] = []
    zh_lines: list[str] = []
    split_any = False
    for line in value.splitlines():
        if not line.startswith("|"):
            return None
        cells = line.split("|")
        en_cells: list[str] = []
        zh_cells: list[str] = []
        for cell in cells:
            inline = _split_inline_bilingual(cell.strip())
            if inline is None:
                en_cells.append(cell)
                zh_cells.append(cell)
            else:
                split_any = True
                leading = cell[: len(cell) - len(cell.lstrip())]
                trailing = cell[len(cell.rstrip()) :]
                en_cells.append(f"{leading}{inline[0]}{trailing}")
                zh_cells.append(f"{leading}{inline[1]}{trailing}")
        en_lines.append("|".join(en_cells))
        zh_lines.append("|".join(zh_cells))
    if not split_any:
        return None
    return "\n".join(en_lines), "\n".join(zh_lines)


def _quote_languages(block: LegacyBlock) -> tuple[str, str, list[Issue]]:
    paragraphs: list[tuple[str, SourceSpan]] = []
    current: list[str] = []
    paragraph_start = block.span.start
    lines = block.text.splitlines()
    for offset, raw in enumerate(lines):
        value = raw[1:] if raw.startswith(">") else raw
        if not value.strip():
            if current:
                paragraphs.append(("\n".join(current).strip(), SourceSpan(paragraph_start, block.span.start + offset - 1)))
                current = []
            paragraph_start = block.span.start + offset + 1
            continue
        if not current:
            paragraph_start = block.span.start + offset
        current.append(value.strip())
    if current:
        paragraphs.append(("\n".join(current).strip(), SourceSpan(paragraph_start, block.span.end)))

    en: list[str] = []
    zh: list[str] = []
    issues: list[Issue] = []
    saw_zh = False
    for paragraph, span in paragraphs:
        if HAN_RE.search(paragraph):
            saw_zh = True
            zh.append(paragraph)
        else:
            if saw_zh:
                issues.append(
                    Issue(
                        code="annotation-language-order",
                        severity="warning",
                        message="English annotation paragraph follows Chinese annotation text.",
                        spans=[span],
                    )
                )
            en.append(paragraph)
    return "\n\n".join(en), "\n\n".join(zh), issues


def pair_section(section: LegacySection) -> tuple[list[LegacyPair], list[Issue]]:
    pairs: list[LegacyPair] = []
    issues: list[Issue] = []
    pending: LegacyBlock | None = None
    leading_annotation: tuple[str, str, SourceSpan] | None = None

    def flush_pending() -> None:
        nonlocal pending
        if pending is not None:
            pairs.append(
                LegacyPair(
                    kind=pending.kind,
                    en=_strip_marker(pending),
                    zh="",
                    spans=[pending.span],
                )
            )
            issues.append(
                Issue(
                    code="unpaired-english",
                    severity="warning",
                    message=f"English content has no adjacent Chinese partner in {section.key}.",
                    spans=[pending.span],
                )
            )
            pending = None

    for block in section.blocks:
        if block.kind == "heading":
            flush_pending()
            pairs.append(LegacyPair("subheading", "", block.text.lstrip("# "), [block.span]))
            continue
        if block.kind == "quote":
            flush_pending()
            en, zh, quote_issues = _quote_languages(block)
            issues.extend(quote_issues)
            if not pairs or pairs[-1].kind in {"image", "subheading"}:
                if leading_annotation is not None:
                    issues.append(
                        Issue(
                            code="multiple-leading-annotations",
                            severity="error",
                            message=f"Multiple annotations precede the first body block in {section.key}.",
                            spans=[leading_annotation[2], block.span],
                        )
                    )
                leading_annotation = (en, zh, block.span)
                continue
            pairs[-1].annotation_en = en
            pairs[-1].annotation_zh = zh
            pairs[-1].spans.append(block.span)
            if not en or not zh:
                issues.append(
                    Issue(
                        code="incomplete-annotation",
                        severity="warning",
                        message=f"Annotation is missing one language in {section.key}.",
                        spans=[block.span],
                    )
                )
            continue
        if block.kind == "image":
            flush_pending()
            match = IMAGE_RE.match(block.text)
            if match is None:
                raise LegacyImportError(f"Invalid image at line {block.span.start}")
            pairs.append(
                LegacyPair(
                    kind="image",
                    en="",
                    zh="",
                    spans=[block.span],
                    asset=match.group("asset"),
                )
            )
            continue

        value = _strip_marker(block)
        inline = _split_inline_bilingual(value)
        if block.kind == "table":
            inline = _split_bilingual_table(value)
        if inline is None and block.kind == "list":
            slash_inline = _split_slash_bilingual(value)
            if slash_inline is not None and pending is not None:
                pending_key = "".join(character.casefold() for character in _strip_marker(pending) if character.isalnum())
                slash_key = "".join(character.casefold() for character in slash_inline[0] if character.isalnum())
                if pending_key != slash_key:
                    flush_pending()
                    inline = slash_inline
            elif pending is None:
                inline = slash_inline
        if inline is None and block.kind == "list" and pending is None:
            inline = _split_suffix_bilingual(value)
        if inline is not None:
            flush_pending()
            pair = LegacyPair(block.kind, inline[0], inline[1], [block.span])
            if leading_annotation is not None:
                pair.annotation_en, pair.annotation_zh, annotation_span = leading_annotation
                pair.spans.insert(0, annotation_span)
                leading_annotation = None
            pairs.append(pair)
            continue

        if HAN_RE.search(value):
            if pending is None:
                pairs.append(LegacyPair(block.kind, "", value, [block.span]))
                issues.append(
                    Issue(
                        code="unpaired-chinese",
                        severity="info" if section.key.startswith(("appendix-", "introduction")) else "warning",
                        message=f"Chinese content has no adjacent English partner in {section.key}.",
                        spans=[block.span],
                    )
                )
            else:
                pair = LegacyPair(
                    kind=pending.kind if pending.kind == block.kind else "markdown",
                    en=_strip_marker(pending),
                    zh=value,
                    spans=[pending.span, block.span],
                )
                if leading_annotation is not None:
                    pair.annotation_en, pair.annotation_zh, annotation_span = leading_annotation
                    pair.spans.insert(0, annotation_span)
                    leading_annotation = None
                pairs.append(pair)
                pending = None
            continue

        if pending is not None:
            flush_pending()
        pending = block

    flush_pending()
    if leading_annotation is not None:
        issues.append(
            Issue(
                code="orphan-annotation",
                severity="error",
                message=f"Annotation has no body block to attach to in {section.key}.",
                spans=[leading_annotation[2]],
            )
        )
    return pairs, issues


def _clean_pdf_text(value: str) -> str:
    return value.replace("\x02", "-").replace("\u00ad", "").replace("`", "").strip()


def _table_markdown(rows: list[list[str | None]]) -> str:
    cleaned = [
        [(_clean_pdf_text(cell or "").replace("\n", "<br>")) for cell in row]
        for row in rows
        if any((cell or "").strip() for cell in row)
    ]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join([render(cleaned[0]), render(["---"] * width), *(render(row) for row in cleaned[1:])])


def _pdf_section_heading(text: str, size: float) -> tuple[str, str] | None:
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
    """Ignore the small-print legal block on the final PDF page.

    The final page number changes between releases, so this must be based on
    page position and typography rather than a version-specific page number.
    """

    return page_number == page_count and size <= 9.5 and top >= 500


def _append_official_unit(
    section: OfficialSection | None,
    kind: str | None,
    lines: list[PdfLine],
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


def parse_official_pdf(path: Path) -> list[OfficialSection]:
    """Extract authoritative English units with page provenance.

    Paragraph boundaries are based on the source PDF's vertical spacing and
    list indentation, not on reflowed plain text. Word tables are extracted
    through their geometry and converted to Markdown.
    """

    sections: list[OfficialSection] = []
    current: OfficialSection | None = None
    buffered: list[PdfLine] = []
    buffered_kind: str | None = None
    previous: PdfLine | None = None

    def flush() -> None:
        nonlocal buffered, buffered_kind
        _append_official_unit(current, buffered_kind, buffered)
        buffered = []
        buffered_kind = None

    with pdfplumber.open(path) as pdf:
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
                (top, "table", (bottom, markdown), None) for top, bottom, markdown in table_events
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
                bold = bool(chars) and all("Bold" in str(char.get("fontname", "")) for char in chars if char.get("text", "").strip())
                midpoint = float(page.width) / 2
                left = [char for char in chars if float(char["x0"]) < midpoint]
                right = [char for char in chars if float(char["x0"]) >= midpoint]
                if raw_index in two_column_indexes:
                    for column, segment in enumerate((left, right)):
                        segment_text = _clean_pdf_text(
                            extract_text(segment, x_tolerance=2, y_tolerance=3)
                        )
                        if not segment_text:
                            continue
                        events.append(
                            (
                                top,
                                "line",
                                (
                                    PdfLine(
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
                    text = _clean_pdf_text(raw["text"])
                    events.append(
                        (
                            top,
                            "line",
                            (PdfLine(page_index, top, float(raw["x0"]), text, bold), size),
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
                heading = _pdf_section_heading(line.text, size)
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
                    current.units.append(OfficialUnit("markdown", f"**{line.text}**", (line.page,)))
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
                    expanded.extend(OfficialUnit("markdown", part, unit.pages) for part in parts)
                    continue
            expanded.append(unit)
        section.units = expanded
    return sections


def _match_normalize(value: str) -> str:
    value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value)
    value = value.replace("™", "").replace("®", "").replace("`", "")
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = value.replace("–", "-").replace("—", "-")
    return "".join(character.casefold() for character in value if character.isalnum())


def _similarity(official: OfficialUnit, legacy: list[LegacyPair]) -> float:
    left = _match_normalize(official.text)
    right = _match_normalize(" ".join(pair.en for pair in legacy))
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def align_section(
    official: OfficialSection,
    legacy_pairs: list[LegacyPair],
    *,
    max_legacy_per_unit: int = 8,
) -> list[AlignmentStep]:
    """Sequence-align authoritative PDF units to legacy bilingual blocks.

    The dynamic program permits one official paragraph to contain several
    legacy blocks (the annotation-driven split case), while making insertions
    and deletions explicit instead of silently forcing a pairing.
    """

    official_units = official.units
    legacy = [pair for pair in legacy_pairs if pair.kind != "image"]

    @lru_cache(maxsize=None)
    def solve(i: int, j: int) -> tuple[float, tuple[AlignmentStep, ...]]:
        if i == len(official_units) and j == len(legacy):
            return 0.0, ()
        candidates: list[tuple[float, tuple[AlignmentStep, ...]]] = []
        if i < len(official_units):
            tail_cost, tail = solve(i + 1, j)
            candidates.append(
                (
                    0.45 + tail_cost,
                    (AlignmentStep("missing-official", (i,), (), 0.0),) + tail,
                )
            )
        if j < len(legacy):
            tail_cost, tail = solve(i, j + 1)
            candidates.append(
                (
                    0.45 + tail_cost,
                    (AlignmentStep("legacy-only", (), (j,), 0.0),) + tail,
                )
            )
        if i < len(official_units) and j < len(legacy):
            for count in range(1, min(max_legacy_per_unit, len(legacy) - j) + 1):
                selected = legacy[j : j + count]
                if count > 1:
                    selected_types = {pair.kind for pair in selected}
                    if official_units[i].kind == "paragraph" and not selected_types <= {
                        "paragraph",
                        "markdown",
                        "bold",
                        "subheading",
                    }:
                        continue
                    if official_units[i].kind in {"list", "table"}:
                        continue
                similarity = _similarity(official_units[i], selected)
                if similarity < 0.12:
                    continue
                if count > 1:
                    official_length = len(_match_normalize(official_units[i].text))
                    legacy_length = len(_match_normalize(" ".join(pair.en for pair in selected)))
                    if legacy_length > official_length * 1.08:
                        continue
                if count > 1 and similarity < 0.80:
                    continue
                type_penalty = 0.0
                legacy_types = {pair.kind for pair in selected}
                if official_units[i].kind == "list" and legacy_types != {"list"}:
                    type_penalty = 0.08
                if official_units[i].kind == "table" and legacy_types != {"table"}:
                    type_penalty = 0.12
                grouping_penalty = 0.012 * (count - 1)
                tail_cost, tail = solve(i + 1, j + count)
                candidates.append(
                    (
                        (1.0 - similarity) + type_penalty + grouping_penalty + tail_cost,
                        (
                            AlignmentStep(
                                "match",
                                (i,),
                                tuple(range(j, j + count)),
                                similarity,
                            ),
                        )
                        + tail,
                    )
                )
        return min(candidates, key=lambda candidate: candidate[0])

    return list(solve(0, 0)[1])


def audit_alignment(
    official_sections: list[OfficialSection],
    legacy_sections: list[LegacySection],
) -> dict[str, object]:
    legacy_by_key = {section.key: section for section in legacy_sections}
    rows: list[dict[str, object]] = []
    missing_keys: list[str] = []
    for official in official_sections:
        legacy_section = legacy_by_key.get(official.key)
        if legacy_section is None:
            missing_keys.append(official.key)
            continue
        pairs, _ = pair_section(legacy_section)
        if not re.fullmatch(r"(?:[1-9]|10)\.\d+", official.key):
            rows.append(
                {
                    "key": official.key,
                    "mode": "translation-only-sequence",
                    "officialUnitCount": len(official.units),
                    "legacyPairCount": len([pair for pair in pairs if pair.kind != "image"]),
                    "legacyImageCount": len([pair for pair in pairs if pair.kind == "image"]),
                }
            )
            continue
        steps = align_section(official, pairs)
        rows.append(
            {
                "key": official.key,
                "mode": "text-alignment",
                "officialUnitCount": len(official.units),
                "legacyPairCount": len([pair for pair in pairs if pair.kind != "image"]),
                "legacyImageCount": len([pair for pair in pairs if pair.kind == "image"]),
                "steps": [asdict(step) for step in steps],
                "exactMatches": sum(
                    1 for step in steps if step.operation == "match" and step.similarity >= 0.995
                ),
                "reviewMatches": sum(
                    1 for step in steps if step.operation == "match" and 0.80 <= step.similarity < 0.995
                ),
                "lowMatches": sum(
                    1 for step in steps if step.operation == "match" and step.similarity < 0.80
                ),
                "missingOfficial": sum(1 for step in steps if step.operation == "missing-official"),
                "legacyOnly": sum(1 for step in steps if step.operation == "legacy-only"),
            }
        )
    return {"missingLegacySections": missing_keys, "sections": rows}


def audit_legacy(path: Path, official_pdf: Path | None = None) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    blocks = tokenize_legacy_markdown(text)
    sections, issues = split_sections(blocks)
    section_rows: list[dict[str, object]] = []
    covered_lines: set[int] = set()

    for section in sections:
        pairs, pair_issues = pair_section(section)
        issues.extend(pair_issues)
        spans = [section.heading.span]
        spans.extend(block.span for block in section.blocks)
        for span in spans:
            covered_lines.update(range(span.start, span.end + 1))
        section_rows.append(
            {
                "key": section.key,
                "heading": section.heading.text,
                "headingSpan": asdict(section.heading.span),
                "blockCount": len(section.blocks),
                "pairCount": len(pairs),
                "pairs": [asdict(pair) for pair in pairs],
            }
        )

    nonblank_lines = {
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    }
    ignored = sorted(nonblank_lines - covered_lines)
    result: dict[str, object] = {
        "source": str(path.resolve()),
        "sha256": sha256_file(path),
        "lineCount": len(text.splitlines()),
        "blockCount": len(blocks),
        "sectionCount": len(sections),
        "ignoredNonblankLines": ignored,
        "sections": section_rows,
        "issues": [
            {
                **asdict(issue),
                "spans": [asdict(span) for span in issue.spans],
            }
            for issue in issues
        ],
    }
    if official_pdf is not None:
        official_sections = parse_official_pdf(official_pdf)
        result["officialPdf"] = {
            "path": str(official_pdf.resolve()),
            "sha256": sha256_file(official_pdf),
            "sectionCount": len(official_sections),
            "unitCount": sum(len(section.units) for section in official_sections),
        }
        result["alignment"] = audit_alignment(official_sections, sections)
    return result


def _write_audit_markdown(audit: dict[str, object], path: Path) -> None:
    issues = audit["issues"]
    assert isinstance(issues, list)
    by_severity = {
        severity: sum(1 for issue in issues if issue["severity"] == severity)
        for severity in ("error", "warning", "info")
    }
    lines = [
        "# Legacy MTR import audit",
        "",
        f"- Source SHA-256: `{audit['sha256']}`",
        f"- Physical lines: {audit['lineCount']}",
        f"- Structural blocks: {audit['blockCount']}",
        f"- Detected sections: {audit['sectionCount']}",
        f"- Issues: {by_severity['error']} errors, {by_severity['warning']} warnings, {by_severity['info']} info",
        "",
    ]
    alignment = audit.get("alignment")
    if isinstance(alignment, dict):
        alignment_sections = alignment["sections"]
        text_rows = [row for row in alignment_sections if row["mode"] == "text-alignment"]
        lines.extend(
            [
                "## Official PDF alignment",
                "",
                f"- Official sections: {audit['officialPdf']['sectionCount']}",
                f"- Official structural units: {audit['officialPdf']['unitCount']}",
                f"- Exact matches: {sum(row['exactMatches'] for row in text_rows)}",
                f"- Matches requiring review: {sum(row['reviewMatches'] for row in text_rows)}",
                f"- Low-confidence matches: {sum(row['lowMatches'] for row in text_rows)}",
                f"- Official units without a legacy partner: {sum(row['missingOfficial'] for row in text_rows)}",
                f"- Legacy blocks without an official partner: {sum(row['legacyOnly'] for row in text_rows)}",
                "",
                "### Sections requiring review",
                "",
            ]
        )
        review_rows = [
            row
            for row in text_rows
            if row["reviewMatches"] or row["lowMatches"] or row["missingOfficial"] or row["legacyOnly"]
        ]
        for row in review_rows:
            lines.append(
                f"- `{row['key']}`: review={row['reviewMatches']}, low={row['lowMatches']}, "
                f"missing={row['missingOfficial']}, legacy-only={row['legacyOnly']}"
            )
        if not review_rows:
            lines.append("- None.")
        lines.extend(["", "## Legacy parsing issues", ""])
    else:
        lines.extend(["## Legacy parsing issues", ""])
    for issue in issues:
        spans = ", ".join(
            f"L{span['start']}" if span["start"] == span["end"] else f"L{span['start']}-L{span['end']}"
            for span in issue["spans"]
        )
        lines.append(f"- **{issue['severity'].upper()} `{issue['code']}`** {spans}: {issue['message']}")
    if not issues:
        lines.append("- None.")
    lines.extend(["", "## Ignored nonblank lines", ""])
    ignored = audit["ignoredNonblankLines"]
    lines.append(", ".join(f"L{number}" for number in ignored) if ignored else "None.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the legacy annotated MTR Markdown before migration.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--official-pdf", type=Path)
    parser.add_argument("--json-report", type=Path, default=Path("reports/legacy-import.json"))
    parser.add_argument("--markdown-report", type=Path, default=Path("reports/legacy-import.md"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    audit = audit_legacy(args.source, args.official_pdf)
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_audit_markdown(audit, args.markdown_report)
    errors = sum(1 for issue in audit["issues"] if issue["severity"] == "error")
    print(
        f"Audited {audit['lineCount']} lines across {audit['sectionCount']} sections; "
        f"found {errors} blocking issue(s)."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
