"""One review PR per candidate; closing it defers automated reminders."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def previous_review(root: Path, repo: str, branch: str, *, retry_closed=False):
    result = subprocess.run(["gh", "pr", "list", "--repo", repo, "--head", branch,
        "--state", "all", "--json", "url,state,number"], cwd=root,
        check=True, stdout=subprocess.PIPE, text=True)
    rows = json.loads(result.stdout)
    if not rows:
        return None
    # An open review takes precedence. A merged candidate must never be reopened.
    row = next((r for state in ("OPEN", "MERGED", "CLOSED") for r in rows if r["state"] == state), None)
    if row is None:
        raise ValueError("更新 PR 状态无法识别")
    if row["state"] == "CLOSED" and retry_closed:
        subprocess.run(["gh", "pr", "reopen", str(row["number"]), "--repo", repo], cwd=root, check=True)
        row = {**row, "state": "OPEN"}
    state = {"OPEN": "review-open", "CLOSED": "review-deferred", "MERGED": "review-merged"}[row["state"]]
    return {"url": row["url"], "status": state}


def main(argv=None):
    parser = argparse.ArgumentParser(description="查询更新审阅 PR；关闭表示暂缓，显式重试才重新打开")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--branch", required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--retry-closed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = previous_review(args.project_root, args.repo, args.branch,
            retry_closed=args.retry_closed or os.environ.get("RETRY_CLOSED") == "true")
        if result is None:
            remote = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", args.branch],
                                    cwd=args.project_root, capture_output=True, text=True)
            if remote.returncode != 2:
                raise ValueError("无法创建更新 PR：远端分支已存在但无 PR，或远端查询失败。请检查分支及网络。")
        print(json.dumps(result, ensure_ascii=False))
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                stream.write(f"exists={str(result is not None).lower()}\n")
        if result and os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
                stream.write(f"\n更新审阅：{result['status']} — {result['url']}\n")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"更新 PR 检查失败：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
