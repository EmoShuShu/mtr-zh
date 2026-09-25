from __future__ import annotations

import json
from pathlib import Path

from .models import OfficialDocument


def write_official_json(document: OfficialDocument, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_official_json(path: Path) -> OfficialDocument:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("formatVersion") != 1:
        raise ValueError(f"Unsupported official document format: {path}")
    document = OfficialDocument.from_dict(value)
    if value.get("sectionCount") != len(document.sections):
        raise ValueError(f"Incorrect section count: {path}")
    if value.get("unitCount") != sum(len(section.units) for section in document.sections):
        raise ValueError(f"Incorrect unit count: {path}")
    return document


def write_official_markdown(document: OfficialDocument, path: Path) -> None:
    lines = [
        "# Parsed Magic Tournament Rules",
        "",
        f"PDF SHA-256: `{document.source_sha256}`",
        "",
    ]
    for section in document.sections:
        lines.extend([f"## {section.key} {section.en}", ""])
        for unit in section.units:
            pages = ", ".join(str(page) for page in unit.pages)
            lines.extend(
                [f"<!-- PDF page(s): {pages}; type: {unit.kind} -->", unit.text, ""]
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
