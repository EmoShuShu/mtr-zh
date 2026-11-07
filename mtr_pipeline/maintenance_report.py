"""Stable daily progress fields, without replacing either native review ledger."""
import json

STATUSES = ('unreviewed', 'reviewed-unchanged', 'reviewed-modified', 'stale')


def progress(document, version, counts, by_file, missing=0, engineering=None):
    counts = {name: counts.get(name, 0) for name in STATUSES}
    files = {name: {'total': sum(values.get(key, 0) for key in STATUSES),
                    'counts': {key: values.get(key, 0) for key in STATUSES}}
             for name, values in sorted(by_file.items())}
    return {'schemaVersion': 1, 'document': document, 'version': version,
            'total': sum(counts.values()), 'reviewed': counts['reviewed-unchanged'] + counts['reviewed-modified'],
            'pending': counts['unreviewed'] + counts['stale'], 'counts': counts,
            'missingTranslations': missing, 'byFile': files,
            'engineering': engineering or {'status': 'not-checked'}}


def print_progress(report, report_path, output=print):
    output(f"{report['document']} 审校进度")
    output(f"  当前版本：{report['version'] or '未指定'}")
    output(f"  总单元：{report['total']}")
    for key, label in [('reviewed-unchanged', '已审未改'), ('reviewed-modified', '已审有改'),
                       ('stale', '失效'), ('unreviewed', '未审')]:
        output(f"  {label}：{report['counts'][key]}")
    output(f"  缺译：{report['missingTranslations']}")
    state = report['engineering']
    if state['status'] == 'needs-attention': output('  工程需处理：' + state.get('message', '请接入当前版本'))
    elif state['status'] == 'not-prepared': output('  工程尚未准备；以上进度按当前源与账本计算。')
    output(f"  详细报告：{report_path}")


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def official_record(document, date, digest, changed, snapshot=None):
    return {"schemaVersion": 1, "document": document, "effectiveDate": date,
            "pdfSha256": digest, "changed": changed, "requiresReview": changed, "snapshot": snapshot}


def official_summary(record):
    state = "发现官方更新，待人工复核" if record["changed"] else "官方 PDF 无变化"
    lines = [f"## {record['document']} 官方检查：{state}", "",
             f"- 有效日期：{record['effectiveDate']}", f"- SHA-256：`{record['pdfSha256']}`",
             "- 当前源、审校记录和正式文件未自动改写。",
             "- 网页及诊断记录见本次运行的附件，保留 30 天。"]
    if record.get("snapshot"): lines.append(f"- 候选与证据：`{record['snapshot']}`")
    return "\n".join(lines) + "\n"
