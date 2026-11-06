# MTR / IPG 配套维护约定

两项目以 MTR 的日常操作为基准，各自保留独立的解析器和数据模型。
本文件在两个仓库保持相同内容；修改共同约定时同步另一份，不能只修改其中一套菜单或自动化。
运行时无需检出或安装另一个仓库。

## 共同操作

| 项目 | 约定 |
|---|---|
| 本地环境 | Python 3.12+；`python -m pip install -e ".[dev]"`；`.venv` |
| 云端 Python | 3.13 |
| Windows 入口 | `审校助手.cmd` → `scripts/review_workflow.py`，UTF-8，操作结束后保留结果 |
| 审校菜单 | 1 准备或继续；2 完成审校并生成最终文档；3 查看进度；q 退出 |
| 文件选择 | 回车采用自动检测；a 全部；逗号分隔编号；q 取消 |
| 人工确认 | `[y/N]`，接受 y / yes；默认取消 |
| 既有工程 | 检查并继续，不覆盖 source、target、TMX 或共享术语库 |
| 无文字修改 | 人工确认后仍可登记为已审未改 |
| 未选文件的修改 | 阻止混入当前批次 |
| 官方检查 | 北京时间每天 06:00，可手动运行；GitHub 排队可能延迟 |
| 官方读取失败 | 网页读取/识别、PDF 下载最多 3 次，间隔 5 秒、15 秒；不重跑源写入 |
| 更新结果 | 无变化不创建更新 PR；变化生成 Draft PR，人工复核后才允许正式切换 |
| 更新 PR 关闭 | 表示暂缓；每日检查继续成功但不重复创建。手动运行勾选 `retry_closed` 才重新打开；已合并 PR 不重开 |
| 提交与合并 | 主分支禁止删除/强推，变更须经 PR，要求当前基线上的 `Validate and build current release` 通过 |
| 正式 Release | 全文审校有效、产物与当前源及已提交 dist 一致；先建草稿，下载核验后公开，已有公开版本只校验不覆盖 |
| 审校失败 | 测试、生成、登记失败或 Ctrl+C 时恢复本批源/账本/输出；不恢复或覆盖用户正在编辑的 OmegaT/TMX/词汇表 |
| 诊断附件 | 每次取得的网页及尝试记录，成功/失败均上传，保留 30 天 |
| CI 证据 | 完整测试、确定性构建及已提交产物比对，附件保留 14 天 |
| 最终产物 | `dist/<MTR或IPG>.md`、`dist/rules.json`；Release 另附 SHA256SUMS |
| 术语检查 | 只作参考，不阻塞回写、构建或登记 |

## 常用脚本与输出

两个仓库提供相同用途的 `scripts/review_workflow.py`、`export_omegat.py`、
`review_status.py`、`audit_terminology.py`、`create_snapshot.py`、
`check_official_update.py`、`validate.py`、`build.py`、`validate_output.py`。
维护入口共同使用 `--project-root` 选择仓库。交互助手返回 0 表示完成，2 表示取消确认，
130 表示 Ctrl+C，1 表示处理失败；参数用法错误由参数解析器返回 2。
其他参数随各自原生数据模型而异，使用 `--help` 查看；不声称底层 CLI 参数可逐字互换。

| 内容 | MTR | IPG |
|---|---|---|
| OmegaT 工程 | `outputs/omegat-mtr-full/` | `outputs/omegat-ipg-full/` |
| 回写预览 | 工程内 `import-preview.json` / `.md` | 同左 |
| 进度报告 | `outputs/review-status.json` / `.md` | 同左 |
| 术语报告 | `outputs/terminology-audit.json` / `.md` | 同左 |
| 官方检查结果 | `tmp/official-update-result.json` | 同左，另保留完整快照报告 |
| 网页诊断 | `tmp/official-update-diagnostics/` | 同左 |
| 词汇表 | `terminology/mtr-glossary.txt` | `terminology/ipg-glossary.txt` |
| 共用版本说明 | `src/mtr/version-notes.md` | `src/ipg/version-notes.md` |

## 保留的原生结构

| 内容 | MTR | IPG | 原因 |
|---|---|---|---|
| 主分支 | master | main | 保留远端规则和已有引用 |
| 当前指针 | `src/mtr/current-version.txt` | `src/ipg/current-release.txt` | 日期与完整版本标识含义不同，不增设第二个可写指针 |
| 源目录 | `src/mtr/<日期>/` | `src/ipg/releases/<完整版本标识>/` | IPG 有官方、注解、中文三个版本部分，目录被溯源与哈希引用 |
| 审校与批注 | `review/mtr-<日期>.json` | `review/status/`、`review/translation-notes/`、`review/actions/` | 保留既有状态和独立批注生命周期 |
| 官方更新候选 | PR 内保存快照与待审源 | PR 内 `snapshots/updates/` 保存完整原生候选和证据 | IPG 使用 `promote_official_update.py` 显式预览/确认；不会借目录对齐自动切换版本 |
| 正式生成条件 | 原生本地构建；CI 和 Release 另要求全文审校有效 | 保留 IPG 全文审校与原生 release 校验 | 公开发布均不接受缺审/失效；本地候选行为保留原生兼容 |
| 部分审校 | 按 MTR 原有构建行为 | 未满足全文条件时保存本批进度和候选、保留旧 dist | 避免无法逐批保存审校成果或发布未审文件 |
| 既有历史 | 固定快照与迁移证据 | 固定快照、首版冻结与阶段复现入口 | 不改写已审证据，不删除可追溯内容 |

相同命名用于日常入口和可再生成报告；承载身份和历史证据的目录不做机械重命名。
不改变解析范围、英文文本、中文译文、稳定 ID、注解锚点、JSON schema 或最终产物字节。
改变这类数据需另做迁移设计并核验所有引用。

## 后续修改检查

新增或修改菜单、命令、报告路径、定时检查或测试环境时，先对照此文件和配套仓库。
运行 `tests/test_maintenance_contract.py` 及本项目相关测试；涉及回写时必须验证取消、
未选文件修改、旧输出保留、失败恢复，以及最终文件满足原有质量门槛。
旧命令以兼容入口保留，开发阶段命名不再出现在默认维护路径中。

## IPG 官方候选接入

`python scripts/promote_official_update.py --snapshot <候选目录>` 只检查并展示预览；
加 `--apply` 后再次用 `[y/N]` 确认，才写入新的原生 release、继承账本和证据，最后切换指针。
缺失/模糊身份、丢失注解锚点等问题必须先通过 override 和重新检查解决。
因上下文变化而失效的注解保留为待审；新增英文不自动成为已审译文。
预览与确认之间的输入变化、写入失败和 Ctrl+C 都不能留下半个新版本。

切换后运行 `python scripts/export_omegat.py --new-version`，确认后把整个旧工程移到
`outputs/omegat-archive/`，按新 manifest 的实际文件/单元生成新工程。旧 PO、TMX、
批注和词汇表不覆盖；创建失败恢复旧工程。版本切换和工程准备不会更新 dist 或发布。
历史迁移覆盖通过显式证据链继承，不把历史覆盖记录当作新英文已经译审的证明。
