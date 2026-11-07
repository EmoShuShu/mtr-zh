"""Adopt a verified official candidate only after an explicit preview/confirmation."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

import yaml

from .core import PipelineError, load_project
from .omegat import iter_translation_units
from .paths import ROOT, current_manifest
from .review_status import load_ledger, calculate_status
from .snapshot import verify_snapshot, SnapshotError
from .official_update import _tree_digest, load_source_state, OfficialUpdateError
from .transaction import restore_on_failure


def _inside(root, path):
    path = root / path
    if path.is_symlink():
        raise PipelineError("候选提升不接受链接")
    path = path.resolve()
    if not path.is_relative_to(root):
        raise PipelineError("候选提升路径越界")
    return path


def _plan(root, snapshot):
    snapshot = _inside(root, snapshot)
    if any(p.is_symlink() for p in snapshot.rglob("*")):
        raise PipelineError("候选快照不能包含链接")
    record = verify_snapshot(snapshot)
    date = record["document"]["effectiveDate"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise PipelineError("候选日期无效")
    base_manifest = current_manifest(root)
    previous = load_source_state(root / "src/mtr/official-source.yaml")
    if previous.effective_date != base_manifest.parent.name:
        raise PipelineError("当前版本指针与官方来源记录不一致")
    if date < previous.effective_date or record["official"]["sha256"] == previous.pdf_sha256:
        raise PipelineError("候选没有更新或日期回退")
    if (record["inputs"]["base"]["version"] != base_manifest.parent.name
            or _tree_digest(base_manifest.parent) != record["inputs"]["base"]["treeSha256"]
            or _tree_digest(snapshot / "inputs/base") != record["inputs"]["base"]["treeSha256"]):
        raise PipelineError("当前源与候选基线不同，请重新检查官方更新")
    schema = root / "schema/mtr-source.schema.json"
    candidate = load_project(snapshot / "candidate/manifest.yaml", schema, snapshot, require_complete=False)
    if candidate.manifest["document"]["effectiveDate"] != date or str(candidate.manifest["document"]["version"]) != date.replace("-", ""):
        raise PipelineError("候选 manifest 日期与快照不一致")
    pdf = _inside(snapshot, record["official"]["path"])
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != record["official"]["sha256"]:
        raise PipelineError("候选 PDF 与版本身份不一致")
    destination = root / "src/mtr" / date
    if destination.exists() and destination != base_manifest.parent:
        raise PipelineError("目标版本已有内容，不会覆盖")
    candidate_root = (snapshot / "candidate").resolve()
    writes = {destination / _inside(candidate_root, p).relative_to(candidate_root): p.read_bytes()
              for p in [candidate.manifest_path, *(s.path for s in candidate.sources)]}
    # Assets are not silently replaced. Preserve native snapshot provenance.
    for file in (snapshot / "assets").rglob("*"):
        if file.is_file():
            path = _inside(root, file.relative_to(snapshot))
            if path.exists() and path.read_bytes() != file.read_bytes():
                raise PipelineError(f"候选图片与当前图片冲突：{path}")
            writes[path] = file.read_bytes()
    base_project = load_project(base_manifest, schema, root, require_complete=False)
    old_version = str(base_project.manifest["document"]["version"])
    old_ledger = root / "review" / f"mtr-{base_manifest.parent.name}.json"
    ledger = copy.deepcopy(load_ledger(old_ledger, old_version))
    calculate_status(list(iter_translation_units(base_project)), ledger)
    ids = {u.unit_id for u in iter_translation_units(candidate)}
    ledger.update(documentVersion=str(candidate.manifest["document"]["version"]))
    # Keep genuine hashes, outcomes and notes; a new official baseline needs review.
    ledger["units"] = {key: {**value, "needsReview": True} for key, value in ledger["units"].items() if key in ids}
    target_ledger = root / "review" / f"mtr-{date}.json"
    if target_ledger.exists() and target_ledger != old_ledger:
        raise PipelineError("目标版本已有审校记录，不会覆盖")
    if old_ledger.exists() and target_ledger == old_ledger:
        content = old_ledger.read_bytes()
        archive = root / "review/history" / f"mtr-{date}-{hashlib.sha256(content).hexdigest()}.json"
        if archive.exists() and archive.read_bytes() != content:
            raise PipelineError("审校历史备份冲突")
        writes[archive] = content
    writes[target_ledger] = (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode()
    writes[root / "src/mtr/official-source.yaml"] = yaml.safe_dump({
        "schemaVersion": 1, "pageUrl": record["official"].get("pageUrl", previous.page_url),
        "title": previous.title, "effectiveDate": date, "pdfUrl": record["official"]["url"],
        "pdfSha256": record["official"]["sha256"]}, allow_unicode=True, sort_keys=False).encode()
    writes[root / "src/mtr/current-version.txt"] = (date + "\n").encode()
    summary = {"baseVersion": base_manifest.parent.name, "effectiveDate": date,
        "snapshot": str(snapshot.relative_to(root)), "findings": record["status"]["findings"],
        "distUnchanged": True, "omegatUnchanged": True, "requiresHumanReview": True}
    protected = {p: p.read_bytes() for p in [schema, root / "src/mtr/current-version.txt", root / "src/mtr/official-source.yaml", old_ledger] if p.exists()}
    return summary, writes, protected


def promote(root, snapshot, *, confirm=None):
    root = Path(root).resolve()
    summary, writes, protected = _plan(root, snapshot)
    if confirm is None:
        return {**summary, "status": "preview"}
    if not confirm(summary):
        return {**summary, "status": "cancelled"}
    fresh, new_writes, new_protected = _plan(root, snapshot)
    if fresh != summary or new_writes != writes or new_protected != protected:
        raise PipelineError("确认期间输入发生变化，未切换")
    pointer = root / "src/mtr/current-version.txt"
    directories = {p for path in writes for p in path.parents if p.is_relative_to(root) and not p.exists()}
    try:
        with restore_on_failure(writes):
            for path in [p for p in writes if p != pointer] + [pointer]:
                _inside(root, path)
                if path.exists() and path.read_bytes() == writes[path]:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(writes[path])
            project = load_project(current_manifest(root), root / "schema/mtr-source.schema.json", root, require_complete=False)
            ledger_path = root / "review" / f"mtr-{summary['effectiveDate']}.json"
            calculate_status(list(iter_translation_units(project)), load_ledger(ledger_path, str(project.manifest["document"]["version"])))
    except BaseException:
        for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        raise
    return {**summary, "status": "promoted-for-review"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="预览官方候选；显式确认后切换审校基线")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    def confirm(report):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return input("确认切换到此候选进行审校？[y/N]：").strip().casefold() in {"y", "yes"}
    try:
        result = promote(args.project_root, args.snapshot, confirm=confirm if args.apply else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["status"] == "cancelled" else 0
    except KeyboardInterrupt:
        print("\n已取消；未完成的切换已恢复。")
        return 130
    except (OSError, ValueError, PipelineError, SnapshotError, OfficialUpdateError, KeyError) as error:
        print(f"候选切换已停止：{error}")
        return 1
