"""Paired acquisition diagnostics; never retry candidate generation or source writes."""
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError

RETRY_DELAYS = (60, 180)
ERROR_LABELS = {
    "official-service-error": "官网文档服务异常",
    "official-data-missing": "官网文档资料缺失",
    "page-validation-error": "官网页面结构或校验异常",
    "download-error": "官方资料下载失败",
    "pdf-validation-error": "官方 PDF 校验异常",
    "internal-error": "检查程序异常",
}


class OfficialCheckError(ValueError):
    def __init__(self, category, message, *, status_code=None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


def error_details(error, stage):
    # MTR preserves its public exception type; follow explicit causes to the
    # original typed error instead of guessing from translated message text.
    chain, current = [], error
    while current is not None and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__
    typed = next((item for item in chain if isinstance(item, OfficialCheckError)), None)
    network = next((item for item in chain if isinstance(item, OSError)), None)
    category = (typed.category if typed else "download-error" if network else
                "page-validation-error" if stage == "rules-page" else "pdf-validation-error")
    result = {"errorCategory": category, "errorLabel": ERROR_LABELS[category], "error": str(error)}
    status = typed.status_code if typed else network.code if isinstance(network, HTTPError) else None
    if status is not None:
        result["upstreamStatusCode"] = status
    return result


def write_failure_summary(document, evidence):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    # Treat upstream error text as text, not Markdown/HTML supplied by the site.
    detail = html.escape(evidence["error"])
    body = (f"## {document} 官方检查未完成\n\n"
            f"- 原因：{evidence['errorLabel']}（{evidence['errorCategory']}）\n"
            f"- 阶段：{evidence['stage']}；已尝试 {evidence['attempt']} 次。\n"
            "- 本次无法确认官方文件是否更新；未判定为无变化。\n"
            "- 当前源、审校记录和正式文件未自动改写。\n"
            "- 网页及逐次诊断见本次运行附件，保留 30 天。\n\n"
            f"<pre>{detail}</pre>\n")
    try:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(body)
    except OSError as error:
        print(f"检查摘要写入失败：{error}", file=sys.stderr, flush=True)


def retry_request(operation, *, stage, diagnostics_dir, document, retry_errors, sleep):
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        evidence = {"stage": stage, "attempt": attempt,
                    "checkedAt": datetime.now(timezone.utc).isoformat()}
        try:
            result = operation(attempt, evidence)
        except Exception as error:
            retryable = isinstance(error, retry_errors)
            details = error_details(error, stage) if retryable else {
                "errorCategory": "internal-error", "errorLabel": ERROR_LABELS["internal-error"],
                "error": f"{type(error).__name__}: {error}"}
            delay = RETRY_DELAYS[attempt - 1] if retryable and attempt < attempts else None
            evidence.update(status="failure", **details, nextRetrySeconds=delay)
            message = f"{stage} 第 {attempt}/{attempts} 次失败：{details['errorLabel']}；{details['error']}"
            if delay is None:
                print(message + "；本次未能核实官方更新。", file=sys.stderr, flush=True)
                write_failure_summary(document, evidence)
                raise
            print(message + f"；{delay} 秒后重试。", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            evidence["status"] = "cancelled"
            raise
        else:
            evidence["status"] = "success"
            if attempt > 1:
                print(f"{stage} 第 {attempt}/{attempts} 次成功。", file=sys.stderr, flush=True)
            return result
        finally:
            if diagnostics_dir is not None:
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                (diagnostics_dir / f"{stage}-attempt-{attempt}.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sleep(delay)
