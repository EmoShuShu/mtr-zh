from pathlib import Path
import json
import shutil
import subprocess
import pytest
from mtr_pipeline import github_release as publishing
ROOT = Path(__file__).resolve().parents[1]

COMMIT = "a" * 40
OLDER = "b" * 40
REPO = "example/mtr-test"


class FakeCLI:
    """In-memory GitHub; no test publishes or authenticates to a real repository."""
    def __init__(self, artifacts, metadata):
        self.artifacts, self.metadata = artifacts, metadata
        self.calls = []
        self.remote_tag = None
        self.main = COMMIT
        self.release = None
        self.files = {}
        self.http_failure = None
        self.download_hook = None
        self.upload_failure = False
        self.list_response = None

    def seed(self, *, draft=False, commit=COMMIT):
        self.remote_tag = commit
        self.release = {"tag_name": self.metadata["tag"], "draft": draft,
                        "body": f"- 来源提交：`{commit}`", "assets": [],
                        "html_url": "https://github.com/example/mtr-test/releases/test-only"}
        self.files = {name: (self.artifacts / name).read_bytes() for name in publishing.ASSETS}
        self.refresh()

    def refresh(self):
        self.release["assets"] = [{"name": name} for name in self.files]

    def run(self, root, *args):
        self.calls.append(args)
        def result(stdout="", code=0, stderr=""):
            return subprocess.CompletedProcess(args, code, stdout, stderr)
        if args[0] == "git":
            if args[1:4] == ("remote", "get-url", "origin"):
                return result(f"https://github.com/{REPO}.git\n")
            if args[1:3] == ("rev-parse", "HEAD"):
                return result(COMMIT + "\n")
            if args[1] in ("diff", "merge-base"):
                return result()
            if args[1] == "ls-remote":
                if args[3] == "refs/heads/master":
                    return result(f"{self.main}\trefs/heads/master\n")
                return result(f"{self.remote_tag}\t{args[3]}\n" if self.remote_tag else "")
        if args[:2] == ("gh", "api"):
            if "--method" in args:
                assert args[args.index("--method") + 1] == "POST"
                self.remote_tag = COMMIT
                return result("{}")
            if "--slurp" in args:
                if self.list_response is not None:
                    return result(*self.list_response)
                return result(json.dumps([[self.release] if self.release else []]))
            if self.http_failure:
                return result(*self.http_failure)
            if self.release is None or self.release["draft"]:
                return result('HTTP/2.0 404 Not Found\r\n\r\n{"message":"Not Found"}', 1)
            return result("HTTP/2.0 200 OK\r\n\r\n" + json.dumps(self.release))
        if args[:2] == ("gh", "release"):
            action = args[2]
            if action == "create":
                assert "--draft" in args and "--verify-tag" in args
                self.seed(draft=True)
                self.release["body"] = Path(args[args.index("--notes-file") + 1]).read_text(encoding="utf-8")
                if self.upload_failure:
                    self.files = {"MTR.md": self.files["MTR.md"]}
                    self.refresh()
                    return result(code=1, stderr="synthetic upload failure")
                return result()
            if action == "download":
                directory = Path(args[args.index("--dir") + 1])
                for i, value in enumerate(args):
                    if value == "--pattern":
                        filename = args[i + 1]
                        (directory / filename).write_bytes(self.files[filename])
                if self.download_hook:
                    self.download_hook()
                    self.download_hook = None
                return result()
            if action == "upload":
                assert "--clobber" not in args
                for filename in args[4:args.index("--repo")]:
                    path = Path(filename)
                    assert path.name not in self.files
                    self.files[path.name] = path.read_bytes()
                self.refresh()
                return result()
            if action == "edit":
                assert "--draft=false" in args and "--latest=true" in args
                self.release["draft"] = False
                return result()
        raise AssertionError(f"Unexpected CLI call: {args}")

    @property
    def writes(self):
        return [args for args in self.calls if args[:2] == ("gh", "api") and "POST" in args
                or args[:2] == ("gh", "release") and args[2] in ("create", "upload", "edit")]


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-only"
    for name in ("src", "schema", "assets"):
        shutil.copytree(ROOT / name, root / name)
    # Build a self-contained test release, independent of the live review ledger
    # and dist (the assistant runs tests before recording the current batch).
    from mtr_pipeline.review_workflow import default_config
    from mtr_pipeline.omegat import iter_translation_units
    config = default_config(root)
    project = publishing.load_project(config.manifest, config.schema, root)
    ledger = {"schemaVersion": 1, "documentVersion": str(project.manifest["document"]["version"]),
              "units": {u.unit_id: {"sourceSha256": publishing.sha256_bytes(u.en.encode()),
                                    "targetSha256": publishing.sha256_bytes(u.zh.encode()), "outcome": "unchanged"}
                        for u in iter_translation_units(project)}}
    config.ledger.parent.mkdir(parents=True)
    config.ledger.write_text(json.dumps(ledger))
    publishing.write_outputs(project, root / "dist/rules.json", root / "dist/MTR.md", output_schema_path=config.output_schema)
    artifacts = root / "outputs/release-test-only"
    artifacts.mkdir(parents=True)
    for name in publishing.ASSETS[:2]:
        shutil.copyfile(root / "dist" / name, artifacts / name)
    (artifacts / "SHA256SUMS").write_text("".join(f"{publishing.sha256_bytes((artifacts / n).read_bytes())}  {n}\n" for n in ("rules.json", "MTR.md")))
    metadata, _ = publishing.prepare_release(root, artifacts, COMMIT)
    cli = FakeCLI(artifacts, metadata)
    monkeypatch.setattr(publishing, "_run", cli.run)
    return root, artifacts, metadata, cli


def test_new_release_uploads_and_verifies_before_publication(package):
    root, artifacts, _, cli = package
    before = {name: (root / "dist" / name).read_bytes() for name in publishing.ASSETS[:2]}
    report = publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert report["status"] == "published" and cli.release["draft"] is False
    actions = [args[2] for args in cli.calls if args[:2] == ("gh", "release")]
    assert actions == ["create", "download", "edit"]
    assert set(cli.files) == set(publishing.ASSETS)
    assert before == {name: (root / "dist" / name).read_bytes() for name in publishing.ASSETS[:2]}


def test_existing_identical_release_is_verified_without_republishing_or_moving_tag(package):
    root, artifacts, _, cli = package
    cli.seed(commit=OLDER)
    body = cli.release["body"]
    report = publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert report["status"] == "verified-existing" and not cli.writes
    assert cli.remote_tag == OLDER and cli.release["body"] == body


@pytest.mark.parametrize("fault", ["changed", "missing", "extra", "duplicate", "source-record"])
def test_existing_public_release_is_never_overwritten(package, fault):
    root, artifacts, _, cli = package
    cli.seed()
    if fault == "changed":
        cli.files["MTR.md"] = b"OTHER PUBLISHED CONTENT"
    elif fault == "missing":
        cli.files.pop("rules.json")
    elif fault == "extra":
        cli.files["unexpected.txt"] = b"EXTRA"
    elif fault == "source-record":
        cli.release["body"] = "unknown source"
    cli.refresh()
    if fault == "duplicate":
        cli.release["assets"].append({"name": "MTR.md"})
    with pytest.raises(ValueError):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert not cli.writes


@pytest.mark.parametrize("response", [("HTTP/2.0 403 Forbidden\n\n{}", 1, "permission denied"),
                                      ("", 1, "network failed"),
                                      ("HTTP/2.0 200 OK\n\n{}", 0, "")])
def test_api_errors_are_not_treated_as_absent_release(package, response):
    root, artifacts, _, cli = package
    cli.http_failure = response
    with pytest.raises(ValueError):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert not cli.writes


def test_draft_can_resume_missing_assets_but_never_overwrites(package):
    root, artifacts, _, cli = package
    cli.seed(draft=True)
    cli.files.pop("rules.json")
    cli.refresh()
    report = publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert report["status"] == "published"
    actions = [args[2] for args in cli.writes if args[:2] == ("gh", "release")]
    assert actions == ["upload", "edit"]


def test_complete_ancestor_draft_can_be_verified_without_moving_its_tag(package):
    root, artifacts, _, cli = package
    cli.seed(draft=True, commit=OLDER)
    report = publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert report["status"] == "published" and report["releaseSourceCommit"] == OLDER
    assert cli.remote_tag == OLDER and f"`{OLDER}`" in cli.release["body"]
    assert [args[2] for args in cli.writes if args[:2] == ("gh", "release")] == ["edit"]


def test_incomplete_ancestor_draft_cannot_be_taken_over(package):
    root, artifacts, _, cli = package
    cli.seed(draft=True, commit=OLDER)
    cli.files.pop("MTR.md")
    cli.refresh()
    with pytest.raises(ValueError, match="不同提交"):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert not cli.writes and cli.release["draft"] is True


@pytest.mark.parametrize("fault", ["network", "shape", "duplicate"])
def test_draft_discovery_errors_stop_without_creating_another_release(package, fault):
    root, artifacts, _, cli = package
    cli.seed(draft=True)
    if fault == "network":
        cli.list_response = ("", 1, "network failed")
    elif fault == "shape":
        cli.list_response = ('{}', 0, "")
    else:
        cli.list_response = (json.dumps([[cli.release], [cli.release]]), 0, "")
    with pytest.raises(ValueError):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert not cli.writes


def test_failed_upload_leaves_only_a_draft_and_retry_can_finish(package):
    root, artifacts, _, cli = package
    cli.upload_failure = True
    with pytest.raises(ValueError):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert cli.release["draft"] is True and not any(args[2] == "edit" for args in cli.writes if args[:2] == ("gh", "release"))
    cli.upload_failure = False
    assert publishing.publish_release(root, artifacts, COMMIT, REPO)["status"] == "published"


@pytest.mark.parametrize("fault", ["main-moved", "tag-conflict", "source-after-check"])
def test_races_or_tag_conflicts_stop_publication(package, fault):
    root, artifacts, _, cli = package
    if fault == "main-moved":
        cli.download_hook = lambda: setattr(cli, "main", OLDER)
    elif fault == "tag-conflict":
        cli.remote_tag = OLDER
    else:
        cli.download_hook = lambda: (root / "src/mtr/version-notes.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert not any(args[2] == "edit" for args in cli.writes if args[:2] == ("gh", "release"))


def test_local_preparation_requires_no_token_and_does_not_publish(package):
    root, artifacts, _, cli = package
    assert publishing.main(["--project-root", str(root), "--artifacts", str(artifacts), "--source-commit", COMMIT]) == 0
    assert not cli.calls


def test_cloud_failure_has_an_actionable_annotation(package, monkeypatch, capsys):
    root, artifacts, _, cli = package
    cli.http_failure = ("HTTP/2.0 403 Forbidden\n\n{}", 1, "permission denied")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert publishing.main(["--project-root", str(root), "--artifacts", str(artifacts),
                            "--source-commit", COMMIT, "--repo", REPO, "--publish"]) == 1
    assert "::error::GitHub 查询失败" in capsys.readouterr().out
    assert not cli.writes



@pytest.mark.parametrize("fault", ["unreviewed", "stale", "dist", "sums"])
def test_review_and_asset_gates_before_remote_writes(package, fault):
    root, artifacts, _, cli = package
    if fault in {"unreviewed", "stale"}:
        path = next((root / "review").glob("mtr-*.json"))
        ledger = json.loads(path.read_text())
        key = next(iter(ledger["units"]))
        if fault == "unreviewed":
            del ledger["units"][key]
        else:
            ledger["units"][key]["sourceSha256"] = "0" * 64
        path.write_text(json.dumps(ledger))
    else:
        (root / "dist/MTR.md" if fault == "dist" else artifacts / "SHA256SUMS").write_text("bad")
    with pytest.raises((ValueError, publishing.PipelineError)):
        publishing.publish_release(root, artifacts, COMMIT, REPO)
    assert not cli.writes
