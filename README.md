# MTR 中文维护流水线

本仓库以 YAML 作为人工维护的唯一结构化源，并确定性生成单文件 JSON 与 Markdown。Schema v1 已于 2026-09-22 冻结；完整设计见 [`outputs/2026-09-22-mtr-pipeline-design.html`](outputs/2026-09-22-mtr-pipeline-design.html)，字段约束及演进规则见 [`schema/README.md`](schema/README.md)。

## 当前状态

`src/mtr/2026-02-27/` 是从旧版 `AMTR_2025.md` 和 Wizards 当前发布的 2026-02-27 MTR PDF **直接对比**生成的全量 Schema v1 审阅草稿，不是可发布版本。`src/mtr/2025-11-10/` 仅作为历史基线保留，不再是当前校对目标。

- 已生成 1 个 manifest 和 17 个正文文件；
- 官方英文的每个结构单元均须逐字重组回 PDF 解析结果，否则迁移立即失败；
- 9 张图片均以稳定资源路径保存在 YAML 中，构建 JSON/Markdown 时转为 Base64 Data URI；
- 当前报告有 46 个阻断项：40 处新英文缺少中文翻译，6 处低相似度但同名系列的继承需要人工确认；
- 另有 107 个警告：54 处新版英文变化、53 处已从官方文档移除或明确拒绝错误继承的旧内容；
- 严格校验会拒绝当前草稿，因此它不会被误当成正式发布输入。

当前处理的不可变快照位于 [`snapshots/2026-02-27/a627fb8c8568/`](snapshots/2026-02-27/a627fb8c8568/)，权威审计清单见其中的 [`comparison/migration.md`](snapshots/2026-02-27/a627fb8c8568/comparison/migration.md)。迁移决策、五处 PDF 图片文字人工转录、正文拆分、拒绝错误继承规则和图片映射集中记录在 [`migration/legacy-v20260227.yaml`](migration/legacy-v20260227.yaml)，没有散落在生成结果中。

## 来源与可追溯性

- 旧版 Markdown SHA-256：`87ff603d9743a3177422dabc5c672c787acc8b3703e71af0e307679c712b33ce`
- 当前官方 PDF SHA-256：`a627fb8c8568d8fbb0d4bd6ffeaee460501c07b80b6928cf8c038d708ad0602e`

迁移器在写出任何 YAML 前校验这两个哈希；来源变化时会停止，不会自动套用旧映射。官方原始文件、解析结果、迁移报告、候选 YAML、输入副本和相关图片都保存在以“有效日期/官方 PDF 哈希前缀”命名的不可变快照中。

## 本地环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 创建或验证官方文件快照

每份新官方 PDF 必须先进入快照，再产生可编辑副本。不要直接将迁移结果写入 `src/mtr/`。

```powershell
.\.venv\Scripts\python.exe scripts\create_snapshot.py `
  --official-pdf tmp\pdfs\MTG_MTR_2026_Feb27_EN.pdf `
  --official-url https://media.wizards.com/ContentResources/WPN/MTG_MTR_2026_Feb27_EN.pdf `
  --effective-date 2026-02-27 `
  --legacy-source AMTR_2025.md `
  --overrides migration\legacy-v20260227.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --snapshot-root snapshots `
  --editable-output src\mtr\2026-02-27
```

安全规则如下：

- 同一日期、同一 PDF 哈希只复用并校验已有快照；
- 同一日期但 PDF 哈希不同会创建新的并列快照；
- 快照中的任一文件被修改后，完整性校验会失败；
- `--editable-output` 只会创建不存在的目录，或确认现有目录与候选完全一致；绝不覆盖人工修改；
- 人工校对开始后只编辑 `src/mtr/<版本>/`，不编辑 `snapshots/`。

快照目录约定及恢复方法见 [`snapshots/README.md`](snapshots/README.md)。底层 `migrate_legacy.py` 保留用于开发和诊断，不作为日常操作入口。

## 校验

审阅期间允许中文暂时为空，但仍执行 Schema、ID、资源路径、PNG 文件等所有其他检查：

```powershell
.\.venv\Scripts\python.exe scripts\validate.py `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --allow-incomplete
```

正式发布前必须去掉 `--allow-incomplete`。只要正文、图片替代文本或注解任一语言为空，严格校验就会失败。

## 构建单文件产物

只有严格校验通过后才应执行正式构建：

```powershell
.\.venv\Scripts\python.exe scripts\build.py `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --json-out dist\rules.json `
  --markdown-out dist\MTR.md
```

最终 JSON 会扁平化中间 YAML 的 `group` 层，不包含 `groups` 字段。图片 Markdown 直接含 `data:image/png;base64,...`，产物不依赖附属图片文件。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试覆盖 Schema/构建、Base64 图片、官方段落拆分、Tab 列表标记、含斜杠牌名、双语表格、旧文档的异常注解格式，以及快照完整性和禁止覆盖人工编辑的规则。
