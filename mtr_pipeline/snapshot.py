from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from mtr_pipeline.core import PipelineError, load_project
from mtr_pipeline.legacy_import import OfficialSection, parse_official_pdf, sha256_file
from mtr_pipeline.legacy_migrate import LegacyImportError, migrate


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HASH_PREFIX_LENGTH = 12


class SnapshotError(RuntimeError):
    """Raised when an immutable official-document snapshot is unsafe or invalid."""


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100000),
        encoding="utf-8",
        newline="\n",
    )


def _snapshot_path(root: Path, effective_date: str, pdf_sha256: str) -> Path:
    if not DATE_RE.fullmatch(effective_date):
        raise SnapshotError(f"Invalid effective date: {effective_date!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", pdf_sha256):
        raise SnapshotError("Official PDF SHA-256 must be 64 lowercase hexadecimal characters")
    return root / effective_date / pdf_sha256[:HASH_PREFIX_LENGTH]


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SnapshotError(f"Snapshot input does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _official_json(sections: list[OfficialSection], pdf_sha256: str) -> dict[str, Any]:
    return {
        "formatVersion": 1,
        "officialPdfSha256": pdf_sha256,
        "sectionCount": len(sections),
        "unitCount": sum(len(section.units) for section in sections),
        "sections": [
            {
                "key": section.key,
                "en": section.en,
                "units": [asdict(unit) for unit in section.units],
            }
            for section in sections
        ],
    }


def _official_markdown(sections: list[OfficialSection], pdf_sha256: str) -> str:
    lines = [
        "# Extracted official MTR English",
        "",
        f"Official PDF SHA-256: `{pdf_sha256}`",
        "",
    ]
    for section in sections:
        lines.extend([f"## {section.key} {section.en}", ""])
        for unit in section.units:
            pages = ", ".join(str(page) for page in unit.pages)
            lines.extend([f"<!-- PDF page(s): {pages}; type: {unit.kind} -->", unit.text, ""])
    return "\n".join(lines).rstrip() + "\n"


def _normalize_migration_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    report["source"]["markdown"] = "inputs/legacy.md"
    report["source"]["officialPdf"] = "official/MTR_EN.pdf"
    report["source"]["overrides"] = "inputs/migration-overrides.yaml"
    report["output"] = "candidate"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def _copy_assets(overrides_path: Path, project_root: Path, snapshot_root: Path) -> None:
    overrides = yaml.safe_load(overrides_path.read_text(encoding="utf-8"))
    asset_rows = overrides.get("assets", {})
    if not isinstance(asset_rows, dict):
        raise SnapshotError("Migration override assets must be an object")
    root = project_root.resolve()
    for row in asset_rows.values():
        relative = Path(row["path"])
        source = (root / relative).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise SnapshotError(f"Asset escapes project root: {relative}") from exc
        _copy_file(source, snapshot_root / relative)


def _artifact_rows(snapshot_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(snapshot_root.rglob("*")):
        if not path.is_file() or path.name == "snapshot.yaml":
            continue
        rows.append(
            {
                "path": path.relative_to(snapshot_root).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return rows


def verify_snapshot(snapshot_path: Path, expected_pdf_sha256: str | None = None) -> dict[str, Any]:
    manifest_path = snapshot_path / "snapshot.yaml"
    if not manifest_path.is_file():
        raise SnapshotError(f"Existing snapshot has no snapshot.yaml: {snapshot_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("snapshotVersion") != 1:
        raise SnapshotError(f"Unsupported snapshot manifest: {manifest_path}")
    actual_pdf_sha256 = manifest.get("official", {}).get("sha256")
    if expected_pdf_sha256 is not None and actual_pdf_sha256 != expected_pdf_sha256:
        raise SnapshotError(
            f"Snapshot hash-prefix collision: expected {expected_pdf_sha256}, "
            f"found {actual_pdf_sha256}"
        )
    recorded_artifacts = manifest.get("artifacts")
    if not isinstance(recorded_artifacts, list):
        raise SnapshotError(f"Snapshot artifact ledger is missing: {manifest_path}")
    actual_artifacts = _artifact_rows(snapshot_path)
    if recorded_artifacts != actual_artifacts:
        recorded = {row.get("path"): row for row in recorded_artifacts if isinstance(row, dict)}
        actual = {row["path"]: row for row in actual_artifacts}
        changed = sorted(
            path for path in set(recorded) | set(actual) if recorded.get(path) != actual.get(path)
        )
        raise SnapshotError("Immutable snapshot artifacts changed: " + ", ".join(changed))
    return manifest


def _verify_reuse_request(
    manifest: dict[str, Any],
    *,
    official_url: str,
    effective_date: str,
    legacy_sha256: str,
    overrides_sha256: str,
) -> None:
    expected = {
        "official URL": (manifest["official"]["url"], official_url),
        "effective date": (manifest["document"]["effectiveDate"], effective_date),
        "legacy input SHA-256": (manifest["inputs"]["legacy"]["sha256"], legacy_sha256),
        "override input SHA-256": (
            manifest["inputs"]["overrides"]["sha256"],
            overrides_sha256,
        ),
    }
    mismatches = [
        f"{label}: snapshot={recorded!r}, request={requested!r}"
        for label, (recorded, requested) in expected.items()
        if recorded != requested
    ]
    if mismatches:
        raise SnapshotError(
            "Existing immutable snapshot was created from different inputs:\n"
            + "\n".join(mismatches)
        )


def _same_candidate(candidate: Path, editable_output: Path) -> bool:
    candidate_files = {
        path.relative_to(candidate).as_posix(): sha256_file(path)
        for path in candidate.rglob("*")
        if path.is_file()
    }
    output_files = {
        path.relative_to(editable_output).as_posix(): sha256_file(path)
        for path in editable_output.rglob("*")
        if path.is_file()
    }
    return candidate_files == output_files


def materialize_candidate(snapshot_path: Path, editable_output: Path) -> str:
    candidate = snapshot_path / "candidate"
    if not editable_output.exists():
        editable_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidate, editable_output)
        return "created"
    if not editable_output.is_dir() or not _same_candidate(candidate, editable_output):
        raise SnapshotError(
            f"Editable output already exists and differs from the immutable candidate: {editable_output}"
        )
    return "verified"


def create_snapshot(
    *,
    official_pdf: Path,
    official_url: str,
    effective_date: str,
    legacy_source: Path,
    overrides: Path,
    schema: Path,
    project_root: Path,
    snapshot_root: Path,
) -> tuple[Path, bool, dict[str, Any]]:
    official_pdf = official_pdf.resolve()
    legacy_source = legacy_source.resolve()
    overrides = overrides.resolve()
    schema = schema.resolve()
    project_root = project_root.resolve()
    snapshot_root = snapshot_root.resolve()
    pdf_sha256 = sha256_file(official_pdf)
    final_path = _snapshot_path(snapshot_root, effective_date, pdf_sha256)
    if final_path.exists():
        manifest = verify_snapshot(final_path, pdf_sha256)
        _verify_reuse_request(
            manifest,
            official_url=official_url,
            effective_date=effective_date,
            legacy_sha256=sha256_file(legacy_source),
            overrides_sha256=sha256_file(overrides),
        )
        return final_path, False, manifest

    final_path.parent.mkdir(parents=True, exist_ok=True)
    stage = final_path.parent / f".{final_path.name}.staging-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        snapshot_pdf = stage / "official/MTR_EN.pdf"
        snapshot_legacy = stage / "inputs/legacy.md"
        snapshot_overrides = stage / "inputs/migration-overrides.yaml"
        _copy_file(official_pdf, snapshot_pdf)
        _copy_file(legacy_source, snapshot_legacy)
        _copy_file(overrides, snapshot_overrides)
        _copy_assets(snapshot_overrides, project_root, stage)

        official_sections = parse_official_pdf(snapshot_pdf)
        extracted = _official_json(official_sections, pdf_sha256)
        extracted_path = stage / "extracted/official.json"
        extracted_path.parent.mkdir(parents=True, exist_ok=True)
        extracted_path.write_text(
            json.dumps(extracted, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (stage / "extracted/official.md").write_text(
            _official_markdown(official_sections, pdf_sha256),
            encoding="utf-8",
            newline="\n",
        )

        migration_report = migrate(
            snapshot_legacy,
            snapshot_pdf,
            snapshot_overrides,
            stage / "candidate",
            stage / "comparison/migration.json",
            stage / "comparison/migration.md",
            official_sections=official_sections,
            effective_date=effective_date,
        )
        migration_report = _normalize_migration_report(stage / "comparison/migration.json")
        load_project(
            stage / "candidate/manifest.yaml",
            schema,
            stage,
            require_complete=False,
        )

        implementation_files = [
            project_root / "mtr_pipeline/legacy_import.py",
            project_root / "mtr_pipeline/legacy_migrate.py",
            project_root / "mtr_pipeline/snapshot.py",
        ]
        manifest = {
            "snapshotVersion": 1,
            "snapshotId": f"{effective_date}/{pdf_sha256[:HASH_PREFIX_LENGTH]}",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "document": {"id": "mtr", "effectiveDate": effective_date},
            "official": {
                "url": official_url,
                "path": "official/MTR_EN.pdf",
                "sha256": pdf_sha256,
                "size": snapshot_pdf.stat().st_size,
            },
            "inputs": {
                "legacy": {
                    "path": "inputs/legacy.md",
                    "sha256": sha256_file(snapshot_legacy),
                },
                "overrides": {
                    "path": "inputs/migration-overrides.yaml",
                    "sha256": sha256_file(snapshot_overrides),
                },
            },
            "processing": {
                "schemaVersion": 1,
                "officialSectionCount": extracted["sectionCount"],
                "officialUnitCount": extracted["unitCount"],
                "implementation": {
                    path.relative_to(project_root).as_posix(): sha256_file(path)
                    for path in implementation_files
                },
            },
            "status": {
                "state": "review-required" if migration_report["summary"]["error"] else "candidate",
                "findings": migration_report["summary"],
            },
            "artifacts": _artifact_rows(stage),
        }
        _write_yaml(stage / "snapshot.yaml", manifest)

        try:
            os.replace(stage, final_path)
        except FileExistsError:
            existing = verify_snapshot(final_path, pdf_sha256)
            shutil.rmtree(stage)
            return final_path, False, existing
        return final_path, True, manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify an immutable official MTR snapshot")
    parser.add_argument("--official-pdf", type=Path, required=True)
    parser.add_argument("--official-url", required=True)
    parser.add_argument("--effective-date", required=True)
    parser.add_argument("--legacy-source", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--snapshot-root", type=Path, default=Path("snapshots"))
    parser.add_argument("--editable-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        path, created, manifest = create_snapshot(
            official_pdf=args.official_pdf,
            official_url=args.official_url,
            effective_date=args.effective_date,
            legacy_source=args.legacy_source,
            overrides=args.overrides,
            schema=args.schema,
            project_root=args.project_root,
            snapshot_root=args.snapshot_root,
        )
        editable_status = None
        if args.editable_output is not None:
            editable_status = materialize_candidate(path, args.editable_output)
    except (SnapshotError, LegacyImportError, PipelineError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    action = "Created" if created else "Verified"
    print(f"{action} immutable snapshot: {path}")
    print(
        "Findings: "
        f"{manifest['status']['findings']['error']} error, "
        f"{manifest['status']['findings']['warning']} warning, "
        f"{manifest['status']['findings']['info']} info"
    )
    if editable_status is not None:
        print(f"Editable candidate {editable_status}: {args.editable_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
