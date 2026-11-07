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

两个项目均于北京时间每天 06:00 自动检查官方文件。实际启动时间可能受 GitHub 排队影响。
关闭更新 PR 表示暂缓处理，不会让每日检查报错或重复建 PR。要继续处理，手动运行时勾选
`retry_closed`；若远端分支已被删除，请先在原 PR 恢复分支。已合并的 PR 不会被重开。
在 [检查记录](https://github.com/EmoShuShu/mtr-zh/actions/workflows/official-update.yml) 查看当天运行状态，
或选择 Run workflow 手动检查。配套项目的共同约定见 [维护约定](docs/maintenance-conventions.md)。

网页读取或识别失败、PDF 下载失败时最多尝试 3 次，间隔为 5 秒和 15 秒。
重试仅限取得官方资料，不重复执行候选生成或版本切换；全部失败仍会使任务报错。
每次取得的网页原文、时间、文件哈希及错误记录都会作为运行页面的
`mtr-official-update-…` 附件保留 30 天，重试后恢复成功也保留前次失败证据。

如果没有变化，不会创建更新分支或 PR。如果发现变化，系统会创建一个 Draft PR，并保留：

- 新的官方 PDF 和解析结果；
- 与上一正式版本的英文差异；
- 继承旧译文和注解后的候选 YAML；
- 需要人工检查的项目。

每日检查只保存候选证据，不修改当前源、版本指针、审校账本或 dist。先按文末“官方候选接入”显式确认，
再完成翻译校对、更新版本说明、生成正式文件并确认自动检查通过，最后人工合并并发布。

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
   覆盖译文、批注或术语表。接入新版本时，会先询问是否完整归档旧工程，确认后才创建新工程。
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
7. 把所选文件标记已审，按稳定 ID 保存批注，并刷新进度报告；
8. 全文译文与审校均就绪时生成 `dist/MTR.md` 和 `dist/rules.json`；否则保存
   `outputs/current-candidate/` 候选阅读文件，保留旧 dist。

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

## 失败恢复与发布核验

确认后的整批回写现在包含源 YAML、审校记录、进度/术语报告和最终文件的恢复保护。
测试、构建或登记失败以及 Ctrl+C 会恢复本批操作；OmegaT 译文、TMX 和共享词汇表不回退。
这不替代断电、强制终止进程时的备份；继续用 Git 保存已完成的审校工作。

默认本地构建、CI 和公开发布均要求所有当前单元的审校记录有效。`--require-reviewed` 旧选项继续可用。
独立导出只检查并继续已有工程；生成文件发生错误或 Ctrl+C 时恢复旧 JSON/Markdown。
Release 先以草稿上传，下载逐字节核对后公开；查询失败不视为“不存在”，既有公开附件不覆盖。
测试报告及发布核验证据保存在 Actions 附件中，保留 14 天。
网页“更新时间”和 PDF“生效日期”分别记录，版本身份以 PDF 内文为准。

## 官方候选接入与常用命令

在更新分支中阅读差异和 findings，确认候选后执行：

```powershell
python scripts/promote_official_update.py --snapshot <候选目录>
python scripts/promote_official_update.py --snapshot <候选目录> --apply
python scripts/export_omegat.py --new-version
```

第一条只预览；第二条再次确认后才接入新的审校基线，第三条也可由助手第 1 项完成。
旧工程整体归档到 `outputs/omegat-archive/`，保留 PO、TMX 和批注，创建失败恢复旧工程。
候选接入保留旧译和真实审校历史，要求人工复审；同日修订将原账本备份到 `review/history/`。
源结构、稳定 ID 和快照目录不迁移。此过程不生成 dist，也不发布。

以下命令均自动读取当前版本，可在任意启动目录加 `--project-root <仓库路径>`：

```powershell
python scripts/review_status.py
python scripts/audit_terminology.py
python scripts/validate.py --profile candidate
python scripts/build.py --profile candidate
python scripts/review_workflow.py --candidate-only
python scripts/review_workflow.py --check-release
python scripts/build.py --profile release --rehearsal
python scripts/validate.py
python scripts/build.py
python scripts/validate_output.py
```

普通候选在 `outputs/candidate/`，正式演练在 `outputs/release-rehearsal/`，均保留旧 dist。
旧 manifest/schema 和分文件输出参数继续可用；相对路径均以所选仓库为基准。


## 重复运行与结果确认

导出工程、查看进度、术语检查及构建命令默认显示简短结果；需要完整机器可读结果时加 `--json`。
进度报告保留原生数据，并增加共同的 `maintenance` 字段，含当前版本、已审/待审/缺译数量和工程状态。
官方检查的 Actions 摘要也采用相同格式；运行的网页诊断附件保留 30 天。

候选和正式演练可以重复运行：内容相同保留原文件和时间戳；变化时完整归档旧结果到
`outputs/build-history/` 后生成新结果。若要严格保留指定目录，可加 `--keep-existing`；
目录含用户额外文件时会停止，不会清空。相同内容的正式构建也不会重写 dist。

`validate_output.py` 默认检查结构和当前版本；检查历史文件须明确使用
`python scripts/validate_output.py --input <历史文件> --schema-only`，这不等于通过正式发布检查。
术语检查默认只供参考；需要严格返回失败状态时，可显式加 `--fail-on-errors`。
准备或回写失败、Ctrl+C 会恢复本批状态；确认期间输入发生变化则要求重新预览。
完整约定和保留差异见 [配套维护约定](docs/maintenance-conventions.md)。

新建 OmegaT 工程会记录导出时的版本；旧工程继续兼容，不补写用户文件。
日期变更而英文恰好相同时，仍会提示归档旧工程后接入新版本。
MTR 自定义嵌套候选目录的历史保存在同级 `build-history/`。
同一官方 PDF 在审校基线变化后会另存新快照到 `snapshots/regenerated/`，原冻结证据不变。
