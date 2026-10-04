from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .core import PipelineError, load_project
from .omegat import TranslationUnit, iter_translation_units


_FORBIDDEN_RE = re.compile(r"(?:禁用译法|禁用)\s*[:：]\s*([^；;]+)")
_FORBIDDEN_SPLIT_RE = re.compile(r"[、/,，]+")


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    preferred: str
    comment: str
    forbidden: tuple[str, ...]


@dataclass(frozen=True)
class TerminologyFinding:
    severity: str
    code: str
    term: str
    preferred: str
    unit_id: str
    source_file: str
    message: str
    source_text: str
    target_text: str


def parse_glossary(path: Path) -> list[GlossaryEntry]:
    entries: list[GlossaryEntry] = []
    seen: dict[str, int] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) not in (2, 3):
            raise PipelineError(
                f"Glossary row must have 2 or 3 tab-separated columns in {path} "
                f"at line {line_number}"
            )
        source, preferred = columns[:2]
        comment = columns[2] if len(columns) == 3 else ""
        if not source or not preferred:
            raise PipelineError(
                f"Glossary source and target must not be empty in {path} at line {line_number}"
            )
        key = source.casefold()
        if key in seen:
            raise PipelineError(
                f"Duplicate glossary source term {source!r} in {path} at lines "
                f"{seen[key]} and {line_number}"
            )
        seen[key] = line_number
        forbidden_match = _FORBIDDEN_RE.search(comment)
        forbidden = ()
        if forbidden_match:
            forbidden = tuple(
                item.strip()
                for item in _FORBIDDEN_SPLIT_RE.split(forbidden_match.group(1))
                if item.strip()
            )
        entries.append(GlossaryEntry(source, preferred, comment, forbidden))
    if not entries:
        raise PipelineError(f"Glossary has no entries: {path}")
    return entries


def _source_pattern(term: str) -> re.Pattern[str]:
    prefix = r"(?<![A-Za-z0-9])" if term[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if term[-1].isalnum() else ""
    return re.compile(prefix + re.escape(term) + suffix, re.IGNORECASE)


def audit_terminology(
    units: list[TranslationUnit], entries: list[GlossaryEntry]
) -> list[TerminologyFinding]:
    findings: list[TerminologyFinding] = []
    for entry in entries:
        pattern = _source_pattern(entry.source)
        for unit in units:
            source_matches = pattern.search(unit.en) is not None
            if source_matches and entry.preferred not in unit.zh:
                findings.append(
                    TerminologyFinding(
                        severity="warning",
                        code="preferred-translation-missing",
                        term=entry.source,
                        preferred=entry.preferred,
                        unit_id=unit.unit_id,
                        source_file=unit.source_file,
                        message=f"Source contains {entry.source!r} but target lacks {entry.preferred!r}",
                        source_text=unit.en,
                        target_text=unit.zh,
                    )
                )
            for forbidden in entry.forbidden:
                if forbidden and forbidden in unit.zh:
                    findings.append(
                        TerminologyFinding(
                            severity="error",
                            code="forbidden-translation",
                            term=entry.source,
                            preferred=entry.preferred,
                            unit_id=unit.unit_id,
                            source_file=unit.source_file,
                            message=(
                                f"Target contains forbidden translation {forbidden!r}; "
                                f"preferred: {entry.preferred!r}"
                            ),
                            source_text=unit.en,
                            target_text=unit.zh,
                        )
                    )
    return findings


def write_audit_reports(
    entries: list[GlossaryEntry],
    findings: list[TerminologyFinding],
    json_path: Path,
    markdown_path: Path,
) -> None:
    error_count = sum(item.severity == "error" for item in findings)
    warning_count = sum(item.severity == "warning" for item in findings)
    payload = {
        "glossaryEntryCount": len(entries),
        "findingCount": len(findings),
        "errorCount": error_count,
        "warningCount": warning_count,
        "findings": [asdict(item) for item in findings],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    lines = [
        "# MTR 术语审计",
        "",
        f"- 术语条目：{len(entries)}",
        f"- 错误：{error_count}",
        f"- 警告：{warning_count}",
    ]
    for finding in findings:
        lines.extend(
            (
                "",
                f"## [{finding.severity.upper()}] `{finding.unit_id}`",
                "",
                f"- 术语：`{finding.term}` → `{finding.preferred}`",
                f"- 文件：`{finding.source_file}`",
                f"- 问题：{finding.message}",
                "",
                f"原文：{finding.source_text}",
                "",
                f"译文：{finding.target_text}",
            )
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit MTR terminology across all translation units")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--glossary", type=Path, required=True)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    parser.add_argument("--fail-on-errors", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        project = load_project(args.manifest, args.schema, args.project_root)
        entries = parse_glossary(args.glossary)
        findings = audit_terminology(list(iter_translation_units(project)), entries)
        write_audit_reports(entries, findings, args.json_report, args.markdown_report)
    except (OSError, PipelineError) as exc:
        print(f"error: {exc}")
        return 1
    errors = sum(item.severity == "error" for item in findings)
    warnings = sum(item.severity == "warning" for item in findings)
    print(
        f"Audited {len(entries)} terms across project: "
        f"{errors} error(s), {warnings} warning(s)"
    )
    return 1 if args.fail_on_errors and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
