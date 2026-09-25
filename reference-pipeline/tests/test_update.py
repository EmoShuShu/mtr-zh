from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import yaml

from mtr_reference.models import OfficialDocument
from mtr_reference.update import prepare_update


def test_prepare_update_creates_snapshot_state_and_candidate(tmp_path: Path, monkeypatch):
    payload = b"%PDF-test"
    digest = hashlib.sha256(payload).hexdigest()
    project_root = tmp_path / "translation"
    project_root.mkdir()
    manifest_path = project_root / "manifest.yaml"
    manifest_path.write_text("schemaVersion: 1\n", encoding="utf-8")
    project = SimpleNamespace(
        root=project_root,
        manifest_path=manifest_path,
        manifest={"document": {"effectiveDate": "2026-02-27"}, "files": []},
        chapters=[],
    )
    release = SimpleNamespace(
        effective_date="2026-09-25",
        page_url="https://wpn.wizards.com/en/rules-documents",
        pdf_url="https://media.wizards.com/MTR.pdf",
    )
    monkeypatch.setattr("mtr_reference.update.discover_official_release", lambda _url: release)
    monkeypatch.setattr("mtr_reference.update._download", lambda *_args, **_kwargs: payload)
    monkeypatch.setattr("mtr_reference.update.load_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(
        "mtr_reference.update.parse_official_pdf",
        lambda _path: OfficialDocument(digest, []),
    )

    def fake_rebase(*_args, **kwargs):
        candidate = kwargs["output_dir"]
        candidate.mkdir(parents=True)
        (candidate / "manifest.yaml").write_text("schemaVersion: 1\n", encoding="utf-8")
        report = kwargs["report_path"]
        report.parent.mkdir(parents=True)
        report.write_text("{}\n", encoding="utf-8")
        report.with_suffix(".md").write_text("# Review\n", encoding="utf-8")
        return {"summary": {"error": 0, "warning": 0, "info": 0}}

    monkeypatch.setattr("mtr_reference.update.rebase_project", fake_rebase)
    state_path = tmp_path / "official/official-source.yaml"

    result = prepare_update(
        state_path=state_path,
        manifest_path=manifest_path,
        source_schema_path=tmp_path / "schema.json",
        snapshot_root=tmp_path / "snapshots",
    )

    snapshot = Path(result["snapshot"])
    assert result["changed"] is True
    assert (snapshot / "official/MTR_EN.pdf").read_bytes() == payload
    assert (snapshot / "candidate/manifest.yaml").is_file()
    assert (snapshot / "comparison/update.md").is_file()
    assert (snapshot / "snapshot.yaml").is_file()
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert state["pdfSha256"] == digest
    assert state["officialJson"] == "official.json"

    unchanged = prepare_update(
        state_path=state_path,
        manifest_path=manifest_path,
        source_schema_path=tmp_path / "schema.json",
        snapshot_root=tmp_path / "snapshots",
    )
    assert unchanged == {
        "changed": False,
        "effectiveDate": "2026-09-25",
        "pdfSha256": digest,
    }
