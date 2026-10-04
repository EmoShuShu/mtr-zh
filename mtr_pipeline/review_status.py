from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .core import PipelineError, Project, load_project
from .omegat import TranslationUnit, iter_translation_units
from .omegat_import import (
    PoEntry,
    flatten_po_collection,
    parse_po_collection,
    validate_po_collections,
)


@dataclass(frozen=True)
class UnitReviewStatus:
    unit_id: str
    status: str
    kind: str
    source_file: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def empty_ledger(document_version: str) -> dict[str, Any]:
    return {"schemaVersion": 1, "documentVersion": document_version, "units": {}}


def load_ledger(path: Path, document_version: str) -> dict[str, Any]:
    if not path.exists():
        return empty_ledger(document_version)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Unable to read review ledger {path}: {exc}") from exc
    if not isinstance(ledger, dict) or ledger.get("schemaVersion") != 1:
        raise PipelineError(f"Unsupported review ledger: {path}")
    if ledger.get("documentVersion") != document_version:
        raise PipelineError(
            f"Review ledger document version {ledger.get('documentVersion')!r} does not match "
            f"project version {document_version!r}"
        )
    if not isinstance(ledger.get("units"), dict):
        raise PipelineError(f"Review ledger units must be an object: {path}")
    return ledger


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ordered = {
        "schemaVersion": 1,
        "documentVersion": ledger["documentVersion"],
        "units": {key: ledger["units"][key] for key in sorted(ledger["units"])},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_omegat_notes(
    path: Path, translated_collection: dict[str, list[PoEntry]]
) -> dict[str, str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PipelineError(f"Unable to read OmegaT notes {path}: {exc}") from exc
    by_translation: dict[tuple[str, str], list[str]] = {}
    for entry in flatten_po_collection(translated_collection):
        by_translation.setdefault((entry.source, entry.target), []).append(entry.unit_id)

    notes: dict[str, str] = {}
    for translation in root.findall("./body/tu"):
        note_node = translation.find("note")
        note = "" if note_node is None else "".join(note_node.itertext()).strip()
        if not note:
            continue
        segments: dict[str, str] = {}
        for tuv in translation.findall("tuv"):
            language = tuv.get("lang") or tuv.get(
                "{http://www.w3.org/XML/1998/namespace}lang"
            )
            segment = tuv.find("seg")
            if language and segment is not None:
                segments[language] = "".join(segment.itertext())
        source = segments.get("en-US")
        target = segments.get("zh-CN")
        unit_ids = by_translation.get((source or "", target or ""), [])
        if not unit_ids:
            raise PipelineError(
                "OmegaT note does not match the current translated PO: "
                + repr((source or "")[:120])
            )
        for unit_id in unit_ids:
            previous = notes.get(unit_id)
            if previous is not None and previous != note:
                raise PipelineError(f"Conflicting OmegaT notes for {unit_id}")
            notes[unit_id] = note
    return notes


def select_review_entries(
    collection: dict[str, list[PoEntry]],
    *,
    files: list[str],
    id_prefixes: list[str],
    unit_ids: list[str],
) -> list[PoEntry]:
    if not files and not id_prefixes and not unit_ids:
        raise PipelineError("Marking reviewed units requires --file, --id-prefix, or --unit-id")
    unknown_files = [name for name in files if name not in collection]
    if unknown_files:
        raise PipelineError("Unknown PO file selector(s): " + ", ".join(unknown_files))
    all_entries = flatten_po_collection(collection)
    by_id = {entry.unit_id: entry for entry in all_entries}
    unknown_ids = [unit_id for unit_id in unit_ids if unit_id not in by_id]
    if unknown_ids:
        raise PipelineError("Unknown review unit ID(s): " + ", ".join(unknown_ids))

    selected_ids = set(unit_ids)
    for name in files:
        selected_ids.update(entry.unit_id for entry in collection[name])
    for prefix in id_prefixes:
        selected_ids.update(entry.unit_id for entry in all_entries if entry.unit_id.startswith(prefix))
    if not selected_ids:
        raise PipelineError("Review selectors did not match any translation units")
    return [entry for entry in all_entries if entry.unit_id in selected_ids]


def mark_reviewed(
    project: Project,
    ledger: dict[str, Any],
    reference_collection: dict[str, list[PoEntry]],
    translated_collection: dict[str, list[PoEntry]],
    *,
    files: list[str],
    id_prefixes: list[str],
    unit_ids: list[str],
    notes: dict[str, str] | None = None,
) -> list[str]:
    validate_po_collections(reference_collection, translated_collection)
    selected = select_review_entries(
        translated_collection,
        files=files,
        id_prefixes=id_prefixes,
        unit_ids=unit_ids,
    )
    reference_by_id = {
        entry.unit_id: entry for entry in flatten_po_collection(reference_collection)
    }
    project_by_id = {unit.unit_id: unit for unit in iter_translation_units(project)}
    reviewed_ids: list[str] = []
    for entry in selected:
        unit = project_by_id.get(entry.unit_id)
        if unit is None:
            raise PipelineError(f"Review entry is not present in current project: {entry.unit_id}")
        if unit.en != entry.source:
            raise PipelineError(f"English source changed before review marking: {entry.unit_id}")
        if unit.zh != entry.target:
            raise PipelineError(
                f"Translated target has not been applied to formal YAML: {entry.unit_id}"
            )
        reference = reference_by_id[entry.unit_id]
        record = {
            "sourceSha256": _hash(unit.en),
            "targetSha256": _hash(unit.zh),
            "outcome": "unchanged" if reference.target == entry.target else "modified",
        }
        note = (
            notes.get(entry.unit_id)
            if notes is not None
            else ledger["units"].get(entry.unit_id, {}).get("note")
        )
        if note:
            record["note"] = note
        ledger["units"][entry.unit_id] = record
        reviewed_ids.append(entry.unit_id)
    return reviewed_ids


def calculate_status(
    units: list[TranslationUnit], ledger: dict[str, Any]
) -> list[UnitReviewStatus]:
    statuses: list[UnitReviewStatus] = []
    records = ledger["units"]
    known_ids = {unit.unit_id for unit in units}
    orphaned = sorted(set(records) - known_ids)
    if orphaned:
        raise PipelineError("Review ledger contains unknown unit ID(s): " + ", ".join(orphaned))
    for unit in units:
        record = records.get(unit.unit_id)
        if record is None:
            status = "unreviewed"
        elif (
            record.get("sourceSha256") != _hash(unit.en)
            or record.get("targetSha256") != _hash(unit.zh)
        ):
            status = "stale"
        elif record.get("outcome") == "modified":
            status = "reviewed-modified"
        elif record.get("outcome") == "unchanged":
            status = "reviewed-unchanged"
        else:
            raise PipelineError(f"Invalid review outcome for {unit.unit_id}")
        statuses.append(
            UnitReviewStatus(unit.unit_id, status, unit.kind, Path(unit.source_file).name)
        )
    return statuses


def write_status_reports(
    statuses: list[UnitReviewStatus], json_path: Path, markdown_path: Path
) -> None:
    counts: dict[str, int] = {}
    files: dict[str, dict[str, int]] = {}
    for item in statuses:
        counts[item.status] = counts.get(item.status, 0) + 1
        file_counts = files.setdefault(item.source_file, {})
        file_counts[item.status] = file_counts.get(item.status, 0) + 1
    payload = {
        "total": len(statuses),
        "counts": counts,
        "files": files,
        "units": [asdict(item) for item in statuses],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = ["# MTR 审校进度", "", f"总单元：{len(statuses)}", ""]
    for status in ("unreviewed", "reviewed-unchanged", "reviewed-modified", "stale"):
        lines.append(f"- {status}: {counts.get(status, 0)}")
    lines.extend(("", "## 按文件", "", "| 文件 | 未审 | 已审未改 | 已审有改 | 失效 |", "| --- | ---: | ---: | ---: | ---: |"))
    for name, file_counts in sorted(files.items()):
        lines.append(
            f"| {name} | {file_counts.get('unreviewed', 0)} | "
            f"{file_counts.get('reviewed-unchanged', 0)} | "
            f"{file_counts.get('reviewed-modified', 0)} | {file_counts.get('stale', 0)} |"
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _add_project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--ledger", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track MTR review status by stable translation unit ID")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Generate review progress reports")
    _add_project_arguments(status)
    status.add_argument("--json-report", type=Path, required=True)
    status.add_argument("--markdown-report", type=Path, required=True)

    mark = commands.add_parser("mark", help="Mark an explicitly selected reviewed batch")
    _add_project_arguments(mark)
    mark.add_argument("--source-po", type=Path, required=True)
    mark.add_argument("--po", type=Path, required=True)
    mark.add_argument("--file", action="append", default=[])
    mark.add_argument("--id-prefix", action="append", default=[])
    mark.add_argument("--unit-id", action="append", default=[])
    mark.add_argument("--omegat-tmx", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        project = load_project(args.manifest, args.schema, args.project_root)
        document_version = str(project.manifest["document"]["version"])
        ledger = load_ledger(args.ledger, document_version)
        if args.command == "mark":
            reference = parse_po_collection(args.source_po)
            translated = parse_po_collection(args.po)
            notes = (
                parse_omegat_notes(args.omegat_tmx, translated)
                if args.omegat_tmx
                else None
            )
            reviewed_ids = mark_reviewed(
                project,
                ledger,
                reference,
                translated,
                files=args.file,
                id_prefixes=args.id_prefix,
                unit_ids=args.unit_id,
                notes=notes,
            )
            save_ledger(args.ledger, ledger)
            print(f"Marked {len(reviewed_ids)} unit(s) reviewed")
        else:
            statuses = calculate_status(list(iter_translation_units(project)), ledger)
            write_status_reports(statuses, args.json_report, args.markdown_report)
            reviewed = sum(item.status.startswith("reviewed-") for item in statuses)
            stale = sum(item.status == "stale" for item in statuses)
            print(f"Review status: {reviewed}/{len(statuses)} reviewed; {stale} stale")
    except (OSError, PipelineError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
