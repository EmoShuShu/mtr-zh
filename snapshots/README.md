# 官方文件快照

这里保存每一份官方 MTR PDF 的不可变处理记录。目录键为：

```text
snapshots/<有效日期>/<官方 PDF SHA-256 前 12 位>/
```

不能只使用有效日期作为唯一键，因为官方站点可能在文件名和有效日期不变的情况下替换文件。同日期、不同哈希必须保留为并列快照。

每个快照包含：

```text
snapshot.yaml                       完整元数据及所有文件的 SHA-256
official/MTR_EN.pdf                 官方原始 PDF
inputs/legacy.md                    本次迁移使用的旧版输入
inputs/migration-overrides.yaml     本次迁移的人工决策
extracted/official.json             机器可读的官方英文结构单元
extracted/official.md               适合人工检查的官方英文提取结果
comparison/migration.json           机器可读迁移报告
comparison/migration.md             人工审阅报告
candidate/*.yaml                    尚未人工校对的候选中间文档
assets/                              候选文档引用的图片副本
```

## 不可变规则

1. 快照创建后不得人工编辑。
2. 再次处理同一 PDF 时只校验并复用已有快照。
3. `snapshot.yaml` 记录除自身以外每个文件的大小和 SHA-256；缺失或改动都会导致校验失败。
4. 解析器和迁移器实现文件的 SHA-256 也记录在 `snapshot.yaml` 中。
5. 人工校对只在 `src/mtr/<版本>/` 进行。

## 后续版本快照

第一次迁移使用 `inputs/legacy.md` 和 `inputs/migration-overrides.yaml`。自动检测到后续官方版本时，不再回到旧版 Markdown，而是以当前已校对的 `src/mtr/<版本>/` 为基线，快照结构相应为：

```text
inputs/base/                        当前已校对的 Schema v1 YAML
comparison/update.json              机器可读的版本重基报告
comparison/update.md                含英文差异的人工审阅报告
candidate/*.yaml                    继承 ID、中文和注解后的新版本候选
```

同一有效日期下如果官方 PDF 的 SHA-256 改变，仍会建立并列快照。自动任务只有在确认现有可编辑目录与快照记录的基线完全一致后，才允许同日期候选替换工作分支中的旧目录；所有替换都会作为 Draft PR 差异接受人工审阅，不会直接进入 `master`。

## 已保存快照

- `2025-11-10/162253d5cd84`：历史基线；官方 PDF 完整哈希 `162253d5cd84068bed69eefd09a0e96fcf13e095aa0ec2fce24a9209d1afc57b`。
- `2026-02-27/a627fb8c8568`：当前校对基线；官方 PDF 完整哈希 `a627fb8c8568d8fbb0d4bd6ffeaee460501c07b80b6928cf8c038d708ad0602e`。

`snapshot.yaml` 中的 `review-required` 记录的是快照刚生成时的状态，不会在后续人工校对后回写。2026-02-27 候选已经在 `src/mtr/2026-02-27/` 完成人工校对并发布。该审计直接比较最新官方 PDF 与快照内保存的 `inputs/legacy.md`，不经过 2025-11-10 官方 PDF。
