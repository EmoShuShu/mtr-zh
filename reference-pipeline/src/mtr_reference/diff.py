from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import OfficialDocument, OfficialUnit


_TOKEN_RE = re.compile(r"\s+|[\w]+(?:[’'][\w]+)*|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class Change:
    section: str
    operation: str
    old_kind: str = ""
    new_kind: str = ""
    old_text: str = ""
    new_text: str = ""

    @property
    def exact_changes(self) -> list[dict[str, str]]:
        return exact_text_changes(self.old_text, self.new_text)


def exact_text_changes(old: str, new: str) -> list[dict[str, str]]:
    old_tokens = _TOKEN_RE.findall(old)
    new_tokens = _TOKEN_RE.findall(new)
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


def render_exact_changes(changes: list[dict[str, str]]) -> str:
    rendered: list[str] = []
    for change in changes:
        if change["op"] == "delete":
            rendered.append(f"[-{change['text']}-]")
        elif change["op"] == "insert":
            rendered.append(f"{{+{change['text']}+}}")
        else:
            rendered.append(change["text"])
    return "".join(rendered)


def _unit_key(unit: OfficialUnit) -> tuple[str, str]:
    return unit.kind, unit.text


def diff_documents(old: OfficialDocument, new: OfficialDocument) -> list[Change]:
    """Return deterministic, exact changes without semantic similarity scoring."""

    old_sections = {section.key: section for section in old.sections}
    new_sections = {section.key: section for section in new.sections}
    section_order = [section.key for section in old.sections]
    section_order.extend(key for key in new_sections if key not in old_sections)
    changes: list[Change] = []

    for key in section_order:
        old_units = old_sections.get(key).units if key in old_sections else []
        new_units = new_sections.get(key).units if key in new_sections else []
        matcher = difflib.SequenceMatcher(
            None,
            [_unit_key(unit) for unit in old_units],
            [_unit_key(unit) for unit in new_units],
            autojunk=False,
        )
        for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if operation == "equal":
                continue
            if operation == "replace":
                old_chunk = old_units[old_start:old_end]
                new_chunk = new_units[new_start:new_end]
                shared = min(len(old_chunk), len(new_chunk))
                for index in range(shared):
                    changes.append(
                        Change(
                            key,
                            "replace",
                            old_chunk[index].kind,
                            new_chunk[index].kind,
                            old_chunk[index].text,
                            new_chunk[index].text,
                        )
                    )
                for unit in old_chunk[shared:]:
                    changes.append(Change(key, "delete", unit.kind, old_text=unit.text))
                for unit in new_chunk[shared:]:
                    changes.append(Change(key, "insert", new_kind=unit.kind, new_text=unit.text))
            elif operation == "delete":
                for unit in old_units[old_start:old_end]:
                    changes.append(Change(key, "delete", unit.kind, old_text=unit.text))
            elif operation == "insert":
                for unit in new_units[new_start:new_end]:
                    changes.append(Change(key, "insert", new_kind=unit.kind, new_text=unit.text))
    return changes


def write_diff(changes: list[Change], json_path: Path, markdown_path: Path) -> None:
    rows = []
    for change in changes:
        row = asdict(change)
        row["changes"] = change.exact_changes
        rows.append(row)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps({"formatVersion": 1, "changes": rows}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    lines = ["# Official MTR changes", "", f"Total changes: {len(changes)}", ""]
    for change in changes:
        lines.extend(
            [
                f"## {change.section} — {change.operation}",
                "",
                "```text",
                render_exact_changes(change.exact_changes),
                "```",
                "",
            ]
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
