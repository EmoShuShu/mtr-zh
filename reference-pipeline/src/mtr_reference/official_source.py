from __future__ import annotations

import hashlib
import re
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import yaml

from .diff import diff_documents, write_diff
from .official_io import read_official_json, write_official_json, write_official_markdown
from .pdf_parser import parse_official_pdf


DEFAULT_RULES_PAGE = "https://wpn.wizards.com/en/rules-documents"
EXPECTED_TITLE = "Magic: The Gathering Tournament Rules"
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024


class OfficialSourceError(RuntimeError):
    """Raised when the official source cannot be discovered or verified safely."""


@dataclass(frozen=True)
class OfficialRelease:
    effective_date: str
    page_url: str
    pdf_url: str
    pdf_sha256: str = ""


class _RulesPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._depth = 0
        self._capture: str | None = None

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = next((value for key, value in attrs if key == "class"), "") or ""
        return set(value.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "div" and any("downloadableDocument" in value for value in classes):
            if self._current is not None:
                raise OfficialSourceError("Nested downloadable-document entries are unsupported")
            self._current = {"title": "", "date": "", "href": ""}
            self._depth = 1
            return
        if self._current is None:
            return
        if tag == "div":
            self._depth += 1
        if any("titleText" in value for value in classes):
            self._capture = "title"
        elif any("updated" in value for value in classes):
            self._capture = "date"
        elif tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if href:
                self._current["href"] = href

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            self._current[self._capture] += data

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self.documents.append(
                    {key: value.strip() for key, value in self._current.items()}
                )
                self._current = None
        self._capture = None


def _validate_url(url: str, expected_host: str, label: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != expected_host or parsed.port not in (None, 443):
        raise OfficialSourceError(f"Unexpected {label} URL: {url}")


def _download(url: str, expected_host: str, max_bytes: int) -> bytes:
    _validate_url(url, expected_host, "download")
    request = urllib.request.Request(url, headers={"User-Agent": "mtr-reference-pipeline/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        _validate_url(response.geturl(), expected_host, "redirected download")
        length = response.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise OfficialSourceError(f"Download is larger than {max_bytes} bytes")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OfficialSourceError(f"Download is larger than {max_bytes} bytes")
    return data


def _display_date(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip())
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(compact, pattern).date().isoformat()
        except ValueError:
            pass
    raise OfficialSourceError(f"Unsupported official date: {value!r}")


def parse_rules_page(html: str, page_url: str = DEFAULT_RULES_PAGE) -> OfficialRelease:
    parser = _RulesPageParser()
    parser.feed(html)
    matches = [row for row in parser.documents if row["title"] == EXPECTED_TITLE]
    if len(matches) != 1:
        raise OfficialSourceError(
            f"Expected exactly one {EXPECTED_TITLE!r} entry, found {len(matches)}"
        )
    row = matches[0]
    pdf_url = urllib.parse.urljoin(page_url, row["href"])
    _validate_url(pdf_url, "media.wizards.com", "official PDF")
    return OfficialRelease(_display_date(row["date"]), page_url, pdf_url)


def discover_official_release(page_url: str = DEFAULT_RULES_PAGE) -> OfficialRelease:
    html = _download(page_url, "wpn.wizards.com", MAX_PAGE_BYTES).decode("utf-8")
    return parse_rules_page(html, page_url)


def check_for_update(state_path: Path, output_dir: Path) -> dict[str, object]:
    state_path = state_path.resolve()
    output_dir = output_dir.resolve()
    previous: dict[str, object] = {}
    if state_path.is_file():
        loaded = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded

    release = discover_official_release()
    pdf = _download(release.pdf_url, "media.wizards.com", MAX_PDF_BYTES)
    if not pdf.startswith(b"%PDF-"):
        raise OfficialSourceError("Official download is not a PDF")
    pdf_sha256 = hashlib.sha256(pdf).hexdigest()
    if previous.get("pdfSha256") == pdf_sha256:
        return {
            "changed": False,
            "effectiveDate": release.effective_date,
            "pdfSha256": pdf_sha256,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mtr-reference-") as temporary:
        pdf_path = Path(temporary) / "MTR_EN.pdf"
        pdf_path.write_bytes(pdf)
        document = parse_official_pdf(pdf_path)
    official_json = output_dir / "official.json"
    write_official_json(document, official_json)
    write_official_markdown(document, output_dir / "official.md")

    previous_json_value = previous.get("officialJson")
    if isinstance(previous_json_value, str):
        previous_json = (state_path.parent / previous_json_value).resolve()
        try:
            previous_json.relative_to(state_path.parent)
        except ValueError as exc:
            raise OfficialSourceError("officialJson escapes the state directory") from exc
        if previous_json.is_file():
            changes = diff_documents(read_official_json(previous_json), document)
            write_diff(changes, output_dir / "changes.json", output_dir / "changes.md")

    candidate_state = {
        "schemaVersion": 1,
        "pageUrl": release.page_url,
        "effectiveDate": release.effective_date,
        "pdfUrl": release.pdf_url,
        "pdfSha256": pdf_sha256,
        "officialJson": "official.json",
    }
    (output_dir / "official-source.yaml").write_text(
        yaml.safe_dump(candidate_state, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "changed": True,
        "effectiveDate": release.effective_date,
        "pdfSha256": pdf_sha256,
        "candidateState": str(output_dir / "official-source.yaml"),
        "officialJson": str(official_json),
    }
