from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from mtr_pipeline.snapshot import (
    SnapshotError,
    _snapshot_path,
    _verify_reuse_request,
    materialize_candidate,
    verify_snapshot,
)


PDF_HASH = "a" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_snapshot(root: Path) -> Path:
    snapshot = root / "2025-11-10" / PDF_HASH[:12]
    artifact = snapshot / "candidate/manifest.yaml"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("schemaVersion: 1\n", encoding="utf-8")
    manifest = {
        "snapshotVersion": 1,
        "official": {"sha256": PDF_HASH},
        "artifacts": [
            {
                "path": "candidate/manifest.yaml",
                "sha256": _sha256(artifact),
                "size": artifact.stat().st_size,
            }
        ],
    }
    (snapshot / "snapshot.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return snapshot


def test_snapshot_path_uses_date_and_pdf_hash_prefix(tmp_path):
    assert _snapshot_path(tmp_path, "2025-11-10", PDF_HASH) == (
        tmp_path / "2025-11-10" / "aaaaaaaaaaaa"
    )
    with pytest.raises(SnapshotError, match="Invalid effective date"):
        _snapshot_path(tmp_path, "20251110", PDF_HASH)


def test_snapshot_verification_detects_modified_artifact(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    verify_snapshot(snapshot, PDF_HASH)
    (snapshot / "candidate/manifest.yaml").write_text("changed: true\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="Immutable snapshot artifacts changed"):
        verify_snapshot(snapshot, PDF_HASH)


def test_snapshot_verification_detects_unrecorded_file(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    (snapshot / "unexpected.txt").write_text("not in ledger\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="unexpected.txt"):
        verify_snapshot(snapshot, PDF_HASH)


def test_materialize_candidate_never_overwrites_existing_edits(tmp_path):
    snapshot = _make_snapshot(tmp_path / "snapshots")
    editable = tmp_path / "editable"
    assert materialize_candidate(snapshot, editable) == "created"
    assert materialize_candidate(snapshot, editable) == "verified"
    (editable / "manifest.yaml").write_text("human: edit\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="differs from the immutable candidate"):
        materialize_candidate(snapshot, editable)


def test_reuse_rejects_different_processing_inputs():
    manifest = {
        "official": {"url": "https://example.test/mtr.pdf"},
        "document": {"effectiveDate": "2025-11-10"},
        "inputs": {
            "legacy": {"sha256": "legacy-a"},
            "overrides": {"sha256": "overrides-a"},
        },
    }
    with pytest.raises(SnapshotError, match="different inputs"):
        _verify_reuse_request(
            manifest,
            official_url="https://example.test/mtr.pdf",
            effective_date="2025-11-10",
            legacy_sha256="legacy-b",
            overrides_sha256="overrides-a",
        )
