# MTR 中文翻译项目

本项目维护《万智牌比赛规则》的中文正文、译文注解，并生成供阅读和网站使用的 Markdown 与 JSON 文件。

## 常用文件

- [`dist/MTR.md`](dist/MTR.md)：生成的中文版 MTR，适合直接阅读；
- [`dist/rules.json`](dist/rules.json)：JSON格式文件；
- [`src/mtr/current-version.txt`](src/mtr/current-version.txt)：当前正式版本的位置；
- [`src/mtr/version-notes.md`](src/mtr/version-notes.md)：所有版本共用的版本说明；
- [`snapshots/`](snapshots/)：官方文件、解析结果和更新记录的备份。

获取最新 JSON：

```text
https://github.com/EmoShuShu/mtr-zh/releases/latest/download/rules.json
```

## 更新方式

项目每天自动检查官方 MTR 是否更新。

如果没有变化，不会创建任何内容。如果发现变化，系统会创建一个 Draft PR，并保留：

- 新的官方 PDF 和解析结果；
- 与上一正式版本的英文差异；
- 继承旧译文和注解后的候选 YAML；
- 需要人工检查的项目。

维护者在 PR 中完成翻译校对、更新版本说明并确认自动检查通过，然后将 PR 标记为可审阅并人工合并。合并后，正式 JSON 和 Markdown 会自动发布到 GitHub Release。

## 校对时编辑哪里

- 编辑 `src/mtr/<版本>/` 中的 YAML；
- 编辑共用版本说明时修改 `src/mtr/version-notes.md`；
- 不要直接编辑 `snapshots/`，其中内容用于备份和审计；
- 不要直接手工修改 `dist/rules.json` 或 `dist/MTR.md`，它们应由构建脚本生成。

英文差异报告中：

- `[-文字-]` 表示官方删除；
- `{+文字+}` 表示官方新增。

## 本地使用

首次使用时安装环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

运行全部检查：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## OmegaT 审校工作流

### 日常使用：审校助手（推荐）

双击仓库根目录的 [`审校助手.cmd`](审校助手.cmd)，即可从同一个菜单完成项目准备、
审校回写和进度查看。也可以在终端中运行：

```powershell
.\审校助手.cmd
```

菜单包含三项：

1. “准备或继续 OmegaT 审校”会在首次使用时生成完整项目；已有项目只做结构检查，绝不
   覆盖译文、批注或术语表。
2. “完成审校并生成最终文档”用于 OmegaT 中选择“项目 → 创建已译文档”之后的一键回写。
3. “查看审校进度”会刷新报告并直接显示各类单元数量。

`src/mtr/version-notes.md` 是所有版本共用的发布说明，不属于 OmegaT 的 17 个 PO。
选择第 2 项时，正式构建器会直接读取它：完整内容会位于 `dist/MTR.md` 开头，同时作为
`mtr-version-notes` 单元写入 `dist/rules.json`；因此它不会因未进入 OmegaT 而被遗漏。

完成阶段会自动检测哪些 PO 含有尚未回写的修改。直接回车即可采用自动选择；输入 `a` 可选择
全部17个文件，也仍可输入编号手动选择。确认已逐条审完的文件后，它会自动：

1. 验证 OmegaT 源文档、译文文档和句段批注；
2. 生成差异报告，并在系统临时目录验证隔离候选版本；
3. 阻止未选择文件中的临时修改混入本批回写；
4. 显示已审单元、实际修改和保留旧译的数量；
5. 在一次人工确认后，最小差异回写正式 YAML；
6. 运行完整测试，并生成仅供参考的术语审计报告；
7. 从正式 YAML 自动生成 `dist/MTR.md` 和 `dist/rules.json`；
8. 仅在必要检查通过后标记所选文件已审，把批注按稳定 ID 写入审校记录，并刷新进度报告。

如果整份文件经核对后无需修改，助手会跳过 YAML 回写，但仍会正确记录为“已审未改”。
取消确认不会修改正式 YAML 或审校记录。日常使用无需填写路径、候选目录或变更数。
OmegaT 的句段批注原本保存在项目的 `omegat/project_save.tmx` 中；审校助手会将非空批注
归档到 `review/mtr-2026-02-27.json` 对应单元的 `note` 字段。若完全相同的原文和译文
对应多个稳定 ID，同一批注会关联到这些重复单元。批注无法对应当前 PO，或在预览确认
期间发生变化时，助手会停止，不会静默遗漏。

### 首次生成 OmegaT 项目

推荐双击 `审校助手.cmd` 并选择第 1 项。它会生成包含全部 17 个源文件和 1453 个翻译
单元的 OmegaT 项目；如果项目已经存在，只会检查，不会重新生成。

以下底层命令仅用于排错和复现：

```powershell
.\.venv\Scripts\python.exe scripts\export_omegat.py `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --output-dir outputs\omegat-mtr-full `
  --scope full `
  --glossary terminology\mtr-glossary.txt
```

随后在 OmegaT 中打开 `outputs/omegat-mtr-full`。这是一个项目，`source` 中的 17 个
PO 与正式 YAML 一一对应；`Ctrl+F` 仍会搜索整个项目。项目使用段落级分段和稳定的
MTR ID。源 PO 的英文 `msgid` 与现有中文 `msgstr` 会分别显示为原文和可编辑译文。

仓库内的 `terminology/mtr-glossary.txt` 是项目唯一的可写词汇表。它是 UTF-8、制表符
分隔的三列文件：英文术语、首选中文、说明。在 OmegaT 中使用 `Ctrl+Shift+G` 添加的
术语会直接写入该文件，并能通过 Git 审阅。说明列可用 `禁用译法：甲、乙` 声明弃用
译法。

运行跨全文术语审计：

```powershell
.\.venv\Scripts\python.exe scripts\audit_terminology.py `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --glossary terminology\mtr-glossary.txt `
  --json-report outputs\terminology-audit.json `
  --markdown-report outputs\terminology-audit.md
```

审计会把禁用译法列为错误，把英文包含术语但中文未包含首选形式列为警告。后者可能
因复合赛事名称或语境变化而产生合理例外，需要人工判断。当前审校助手将整个术语审计
视为参考信息：无论发现术语错误、警告，还是词汇表暂时无法解析，都不会阻塞译文回写、
最终文档生成或审校登记。

### 手动回写（排错与复现）

以下命令是审校助手调用的底层接口，日常审校不需要手动执行。审校后选择
“项目 → 创建已译文档”。正式回写分为预览、候选验证、确认应用三步。
先运行只读预览，逐条校验稳定 ID 和英文原文，再审阅中文差异：

```powershell
.\.venv\Scripts\python.exe scripts\import_omegat.py `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --source-po outputs\omegat-mtr-full\source `
  --po outputs\omegat-mtr-full\target `
  --json-report outputs\omegat-mtr-full\import-preview.json `
  --markdown-report outputs\omegat-mtr-full\import-preview.md
```

需要验证实际写回时，在同一命令末尾加上：

```powershell
  --candidate-dir outputs\omegat-roundtrip-candidate
```

`--candidate-dir` 必须指向一个尚不存在、且位于正式源目录之外的目录。工具只会把
修改写入这个隔离副本，不会修改 `src`。确认预览中的变更数为 `N` 后，移除
`--candidate-dir`，改为：

```powershell
  --apply --expected-change-count N
```

只有重新计算出的变更数恰好等于 `N` 时，工具才会修改正式 YAML；写回只替换对应的
`zh` 标量，不会重新排版整份 YAML，并会先在临时副本中完成结构与内容验证。如果
PO 文件或条目缺失、重复或出现陌生 ID，英文 `msgid` 与导出时或当前 YAML 不同，或
YAML 在预览后又发生变化，操作都会中止。应用后运行全部测试，核验源文件、构建逻辑
和预期发布结果（不要手工编辑 `dist`）：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

审校进度保存在 `review/mtr-2026-02-27.json`，以稳定 ID 和中英文哈希记录，不把
“译文与旧译相同”误判成“尚未审校”。只有在译文已经正式回写后才能标记。若完整审完
`introduction.po`（包括确认无需修改的条目），运行：

```powershell
.\.venv\Scripts\python.exe scripts\review_status.py mark `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --ledger review\mtr-2026-02-27.json `
  --source-po outputs\omegat-mtr-full\source `
  --po outputs\omegat-mtr-full\target `
  --omegat-tmx outputs\omegat-mtr-full\omegat\project_save.tmx `
  --file introduction.po
```

也可用一个或多个 `--id-prefix` 或 `--unit-id` 精确标记较小批次；不要把尚未逐条确认
的整份 PO 标为已审。之后生成进度报告：

```powershell
.\.venv\Scripts\python.exe scripts\review_status.py status `
  --manifest src\mtr\2026-02-27\manifest.yaml `
  --schema schema\mtr-source.schema.json `
  --project-root . `
  --ledger review\mtr-2026-02-27.json `
  --json-report outputs\review-status.json `
  --markdown-report outputs\review-status.md
```

状态分为 `unreviewed`、`reviewed-unchanged`、`reviewed-modified` 和 `stale`。已审条目的
英文或正式中文后来发生变化时会自动变为 `stale`，需要重新核对并再次标记。

若只需快速回归测试，可在导出命令中使用 `--scope pilot`，生成原来的 28 条试验项目。

通常不需要手动运行更新和发布流程；GitHub Actions 会在 PR 中完成校验，并在合并后发布正式文件。

## 其他语言

如果希望参考 PDF 解析器或维护其他语言的 MTR，请阅读英文的 [`reference-pipeline/README.md`](reference-pipeline/README.md)。该参考流程与中文正式流程相互独立。
