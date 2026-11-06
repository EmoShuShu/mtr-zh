"""Publish verified MTR assets through a download-verified GitHub draft."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from .core import PipelineError, load_project, write_outputs
from .review_workflow import default_config
from .release_gate import require_reviewed
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("MTR.md", "rules.json", "SHA256SUMS")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _unchanged(hashes):
    for name, digest in hashes.items():
        path = Path(name)
        if not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
            raise ValueError(f"核验过程中输入发生变化：{path}")


def candidate_destination(root, path):
    path = root / path
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("发布资料目录不能是链接")
    path = path.resolve()
    if not path.is_relative_to(root / "outputs") or path == root / "outputs":
        raise ValueError("发布资料必须位于 outputs 的独立子目录")
    return path


def prepare_release(root, artifacts, commit):
    root = root.resolve()
    artifacts = candidate_destination(root, artifacts)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("来源提交必须是完整 Git commit SHA。")
    config = default_config(root)
    inputs = [root / "src/mtr/current-version.txt", config.manifest, config.schema,
              config.output_schema, config.ledger, root / "src/mtr/version-notes.md",
              *sorted(config.manifest.parent.glob("*.yaml")), root / "dist/MTR.md", root / "dist/rules.json"]
    hashes = {str(p): sha256_bytes(p.read_bytes()) for p in inputs}
    project = load_project(config.manifest, config.schema, root)
    require_reviewed(project)
    for name in (*ASSETS, "RELEASE_NOTES.md", "release-metadata.json"):
        if (artifacts / name).is_symlink():
            raise ValueError("发布资料不能是链接：" + name)
    with tempfile.TemporaryDirectory(prefix="mtr-release-check-") as temporary:
        expected = Path(temporary)
        write_outputs(project, expected / "rules.json", expected / "MTR.md", output_schema_path=config.output_schema)
        sums = "".join(f"{sha256_bytes((expected / name).read_bytes())}  {name}\n" for name in ("rules.json", "MTR.md"))
        (expected / "SHA256SUMS").write_text(sums, encoding="utf-8")
        for name in ASSETS:
            if (artifacts / name).read_bytes() != (expected / name).read_bytes():
                raise ValueError("发布资料与当前源不一致：" + name)
        for name in ASSETS[:2]:
            if (root / "dist" / name).read_bytes() != (expected / name).read_bytes():
                raise ValueError("dist 已过期：" + name)
    version = str(project.manifest["document"]["effectiveDate"])
    asset_hashes = {name: sha256_bytes((artifacts / name).read_bytes()) for name in ASSETS}
    metadata = {"version": version, "sourceCommit": commit,
                "tag": f"mtr-{version}-{asset_hashes['rules.json'][:12]}",
                "title": f"MTR 中文版 {version}", "hashes": asset_hashes}
    notes = (f"# {metadata['title']}\n\n本 Release 经全文审校状态、测试及产物核验后发布。\n\n"
             f"- 来源提交：`{commit}`\n- rules.json SHA-256：`{asset_hashes['rules.json']}`\n\n"
             "附件：MTR.md、rules.json、SHA256SUMS。\n\n" + (root / "src/mtr/version-notes.md").read_text(encoding="utf-8") + "\n")
    for name, content in [("RELEASE_NOTES.md", notes), ("release-metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")]:
        path = artifacts / name
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("发布证据已存在且内容不同，不会覆盖：" + name)
        else:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    hashes.update({str(artifacts / name): sha256_bytes((artifacts / name).read_bytes()) for name in (*ASSETS, "RELEASE_NOTES.md", "release-metadata.json")})
    _unchanged(hashes)
    return metadata, hashes


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)


def _checked(root: Path, *args: str) -> str:
    result = _run(root, *args)
    if result.returncode:
        raise ValueError(f"{args[0]} 操作失败：{result.stderr.strip()}")
    return result.stdout.strip()


def _release(root: Path, repo: str, tag: str) -> dict | None:
    result = _run(root, "gh", "api", f"repos/{repo}/releases/tags/{quote(tag, safe='')}", "--include")
    text = result.stdout.replace("\r\n", "\n")
    header = re.match(r"HTTP/\S+ (\d{3})[^\n]*\n", text)
    status = int(header[1]) if header else None
    if status == 404:
        # REST's tag endpoint only finds published releases. Authenticated listing
        # includes drafts; do not mistake a pending tag for a missing release.
        pages = json.loads(_checked(root, "gh", "api", f"repos/{repo}/releases", "--paginate", "--slurp"))
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError("GitHub 草稿查询响应结构不合法。")
        releases = [item for page in pages for item in page]
        if any(not isinstance(item, dict) or not isinstance(item.get("tag_name"), str) for item in releases):
            raise ValueError("GitHub 草稿查询条目不合法。")
        matches = [item for item in releases if item["tag_name"] == tag]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("GitHub 返回重复的同标签 Release，不会选择或覆盖。")
        release = matches[0]
    elif result.returncode or status != 200:
        raise ValueError("GitHub 查询失败，不会视为版本不存在：" + result.stderr.strip())
    else:
        release = json.loads(text.split("\n\n", 1)[1])
    if (not isinstance(release, dict) or release.get("tag_name") != tag
            or type(release.get("draft")) is not bool or not isinstance(release.get("assets"), list)):
        raise ValueError("GitHub Release 响应结构不合法。")
    return release


def _tag_commit(root: Path, tag: str) -> str | None:
    ref = "refs/tags/" + tag
    text = _checked(root, "git", "ls-remote", "origin", ref, ref + "^{}")
    refs = {}
    for line in text.splitlines():
        sha, name = line.split()
        if name not in (ref, ref + "^{}") or name in refs or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("远端标签响应不合法。")
        refs[name] = sha
    return refs.get(ref + "^{}", refs.get(ref))


def _verify_assets(root: Path, repo: str, tag: str, artifacts: Path, release: dict) -> set[str]:
    names = [asset.get("name") for asset in release["assets"]]
    if len(names) != len(set(names)) or not set(names).issubset(ASSETS):
        raise ValueError("已有 Release 含重复或非预期附件，不会覆盖。")
    if names:
        with tempfile.TemporaryDirectory(prefix="mtr-existing-release-") as directory:
            patterns = [item for name in names for item in ("--pattern", name)]
            _checked(root, "gh", "release", "download", tag, "--repo", repo, "--dir", directory, *patterns)
            for name in names:
                if (Path(directory) / name).read_bytes() != (artifacts / name).read_bytes():
                    raise ValueError("已有 Release 附件不同，不会覆盖：" + name)
    return set(names)


def publish_release(root: Path, artifacts: Path, commit: str, repo: str) -> dict:
    root = root.resolve()
    artifacts = candidate_destination(root, artifacts)
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub 仓库标识不合法。")
    origin = _checked(root, "git", "remote", "get-url", "origin").removesuffix(".git").lower()
    if origin not in (f"https://github.com/{repo}".lower(), f"git@github.com:{repo}".lower()):
        raise ValueError("origin 与指定发布仓库不同。")
    if _checked(root, "git", "rev-parse", "HEAD") != commit:
        raise ValueError("来源提交与当前检出不同。")
    _checked(root, "git", "diff", "--quiet", "HEAD", "--")
    metadata, hashes = prepare_release(root, artifacts, commit)
    tag = metadata["tag"]
    release = _release(root, repo, tag)
    target = _tag_commit(root, tag)
    if release is not None:
        if (not target or f"- 来源提交：`{target}`" not in release.get("body", "")
                or _run(root, "git", "merge-base", "--is-ancestor", target, commit).returncode):
            raise ValueError("已有 Release 的标签、来源提交或 master 历史不一致。")
        existing = _verify_assets(root, repo, tag, artifacts, release)
        if not release["draft"]:
            if existing != set(ASSETS):
                raise ValueError("已公开 Release 缺少附件，不会自动改写。")
            _unchanged(hashes)
            return {**metadata, "releaseSourceCommit": target,
                    "status": "verified-existing", "url": release.get("html_url")}
    else:
        existing = set()
    # Only current master can create/publish. Old published releases can be verified read-only.
    if _checked(root, "git", "ls-remote", "origin", "refs/heads/master").split() != [commit, "refs/heads/master"]:
        raise ValueError("master 已变化或当前检出不是 master；不会发布旧运行。")
    if target is not None and target != commit and (release is None or existing != set(ASSETS)):
        raise ValueError("标签指向不同提交；不会移动标签或接管旧草稿。")
    _unchanged(hashes)
    if release is None:
        if target is None:
            _checked(root, "gh", "api", f"repos/{repo}/git/refs", "--method", "POST",
                     "--raw-field", f"ref=refs/tags/{tag}", "--raw-field", f"sha={commit}")
        if _tag_commit(root, tag) != commit:
            raise ValueError("无法确认新标签的来源提交，未创建 Release。")
        _checked(root, "gh", "release", "create", tag, *(str(artifacts / name) for name in ASSETS),
                 "--repo", repo, "--draft", "--verify-tag", "--target", commit, "--title", metadata["title"],
                 "--notes-file", str(artifacts / "RELEASE_NOTES.md"))
    elif missing := sorted(set(ASSETS) - existing):
        _checked(root, "gh", "release", "upload", tag, *(str(artifacts / name) for name in missing), "--repo", repo)
    release = _release(root, repo, tag)
    release_commit = target or commit
    if (release is None or not release["draft"] or f"- 来源提交：`{release_commit}`" not in release.get("body", "")
            or _tag_commit(root, tag) != release_commit
            or _verify_assets(root, repo, tag, artifacts, release) != set(ASSETS)):
        raise ValueError("草稿或附件核验失败，未公开发布。")
    _unchanged(hashes)
    if _checked(root, "git", "ls-remote", "origin", "refs/heads/master").split() != [commit, "refs/heads/master"]:
        raise ValueError("核验期间 master 已变化；保留草稿，不会公开。")
    _checked(root, "gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest=true")
    published = _release(root, repo, tag)
    if published is None or published["draft"]:
        raise ValueError("无法确认公开发布结果；请查看运行记录，不会重新覆盖。")
    return {**metadata, "releaseSourceCommit": release_commit,
            "status": "published", "url": published.get("html_url")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="准备或发布已经验证的 MTR 资料；默认只准备。")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    try:
        if args.publish:
            report = publish_release(root, root / args.artifacts, args.source_commit, args.repo or "")
        else:
            report, _ = prepare_release(root, root / args.artifacts, args.source_commit)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"发布流程已停止：{error}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            message = str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::error::" + message)
        return 1
