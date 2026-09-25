from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .diff import diff_documents, write_diff
from .official_io import read_official_json, write_official_json, write_official_markdown
from .official_source import (
    DEFAULT_RULES_PAGE,
    MAX_PDF_BYTES,
    OfficialSourceError,
    _download,
    discover_official_release,
)
from .pdf_parser import parse_official_pdf
from .project import TranslationProject, load_project
from .rebase import rebase_project


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OfficialSourceError(f"Official source state must be a YAML object: {path}")
    return value


def _state_official_json(state_path: Path, state: dict[str, Any]) -> Path | None:
    value = state.get("officialJson")
    if not isinstance(value, str):
        return None
    candidate = (state_path.parent / value).resolve()
    try:
        candidate.relative_to(state_path.parent.resolve())
    except ValueError as exc:
        raise OfficialSourceError("officialJson escapes the state directory") from exc
    return candidate


def _iter_groups(project: TranslationProject):
    for chapter in project.chapters:
        yield from chapter.get("groups", [])
        for section in chapter.get("sections", []):
            yield from section["groups"]


def _copy_assets(project: TranslationProject, destination: Path) -> None:
    copied: set[str] = set()
    for group in _iter_groups(project):
        if group["type"] != "image":
            continue
        for block in group["blocks"]:
            relative = Path(block["asset"])
            key = relative.as_posix()
            if key in copied:
                continue
            source = (project.root / relative).resolve()
            try:
                source.relative_to(project.root.resolve())
            except ValueError as exc:
                raise OfficialSourceError(f"Image asset escapes the project root: {relative}") from exc
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.add(key)


def _copy_base(project: TranslationProject, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project.manifest_path, destination / project.manifest_path.name)
    for relative in project.manifest["files"]:
        source = (project.root / relative).resolve()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    _copy_assets(project, destination)


def _artifact_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "snapshot.yaml":
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(path),
                    "size": path.stat().st_size,
                }
            )
    return rows


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100000),
        encoding="utf-8",
        newline="\n",
    )


def prepare_update(
    *,
    state_path: Path,
    manifest_path: Path,
    source_schema_path: Path,
    snapshot_root: Path,
    page_url: str = DEFAULT_RULES_PAGE,
) -> dict[str, Any]:
    """Check the official source and prepare a self-contained review snapshot."""

    state_path = state_path.resolve()
    snapshot_root = snapshot_root.resolve()
    previous = _read_state(state_path)
    release = discover_official_release(page_url)
    previous_date = previous.get("effectiveDate")
    if isinstance(previous_date, str) and release.effective_date < previous_date:
        raise OfficialSourceError(
            f"Official page would downgrade MTR from {previous_date} to {release.effective_date}"
        )

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

    project = load_project(manifest_path, source_schema_path, require_complete=True)
    final = snapshot_root / release.effective_date / pdf_sha256[:12]
    if final.exists():
        raise OfficialSourceError(f"Snapshot already exists for a different state: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = final.parent / f".{final.name}.staging-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        official_pdf = stage / "official/MTR_EN.pdf"
        official_pdf.parent.mkdir(parents=True)
        official_pdf.write_bytes(pdf)
        document = parse_official_pdf(official_pdf)
        if document.source_sha256 != pdf_sha256:
            raise OfficialSourceError("Parsed PDF hash does not match the downloaded PDF")

        extracted_json = stage / "extracted/official.json"
        write_official_json(document, extracted_json)
        write_official_markdown(document, stage / "extracted/official.md")
        old_json = _state_official_json(state_path, previous)
        if old_json and old_json.is_file():
            changes = diff_documents(read_official_json(old_json), document)
            write_diff(
                changes,
                stage / "comparison/official-changes.json",
                stage / "comparison/official-changes.md",
            )

        _copy_base(project, stage / "inputs/base")
        _copy_assets(project, stage / "candidate")
        report = rebase_project(
            project,
            document,
            effective_date=release.effective_date,
            pdf_url=release.pdf_url,
            output_dir=stage / "candidate",
            report_path=stage / "comparison/update.json",
        )
        load_project(
            stage / "candidate/manifest.yaml",
            source_schema_path,
            require_complete=False,
        )
        snapshot = {
            "snapshotVersion": 1,
            "snapshotId": f"{release.effective_date}/{pdf_sha256[:12]}",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "official": {
                "pageUrl": release.page_url,
                "pdfUrl": release.pdf_url,
                "effectiveDate": release.effective_date,
                "pdfSha256": pdf_sha256,
                "path": "official/MTR_EN.pdf",
            },
            "base": {
                "version": project.manifest["document"]["effectiveDate"],
                "path": "inputs/base",
            },
            "candidate": {"path": "candidate/manifest.yaml"},
            "review": {"state": "required", "findings": report["summary"]},
            "artifacts": _artifact_rows(stage),
        }
        _write_yaml(stage / "snapshot.yaml", snapshot)
        os.replace(stage, final)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    state_path.parent.mkdir(parents=True, exist_ok=True)
    baseline = state_path.parent / "official.json"
    shutil.copyfile(final / "extracted/official.json", baseline)
    _write_yaml(
        state_path,
        {
            "schemaVersion": 1,
            "pageUrl": release.page_url,
            "effectiveDate": release.effective_date,
            "pdfUrl": release.pdf_url,
            "pdfSha256": pdf_sha256,
            "officialJson": "official.json",
        },
    )
    return {
        "changed": True,
        "effectiveDate": release.effective_date,
        "pdfSha256": pdf_sha256,
        "snapshot": str(final),
        "candidate": str(final / "candidate/manifest.yaml"),
        "report": str(final / "comparison/update.md"),
        "findings": report["summary"],
        "branch": f"automation/mtr-{release.effective_date}-{pdf_sha256[:12]}",
    }
