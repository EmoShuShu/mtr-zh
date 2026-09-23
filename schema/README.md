# MTR 中间格式 Schema

## 状态

- 当前版本：`1`
- 状态：已冻结
- 冻结日期：2026-09-22
- 规范文件：[`mtr-source.schema.json`](mtr-source.schema.json)

Schema v1 已通过代表性内容验证，覆盖普通段落、按注解拆分的 block、逐项注解列表、Markdown 表格、代码块公式和 PNG 图片引用。

## 冻结的不变量

- YAML 是唯一人工编辑的内容事实源。
- 内容层级为 `section → group → block`。
- `group` 保存官方文档的段落、列表、表格或图片边界。
- `block` 是按注解拆分的最小编辑和发布单位。
- 一个 block 最多包含一个 `extras` 元素；注解可包含多个 Markdown 段落。
- 图片 block 在 YAML 中引用 `assets/` 下的 PNG，不直接保存 Base64。
- 最终 JSON 展平 group，只输出 block。
- 多 block 表格在最终产物中重新组装为一个连续 Markdown 单元；其中间行 block 仍保留在 YAML 中用于维护和差异审阅。
- `review` 和逐条 `source` 不属于内容节点。
- 既有 ID 不因正文修改、移动或中间插入新内容而重新编号。

## 演进规则

- Schema v1 的字段语义和层级不得原地进行不兼容修改。
- 需要改变必填字段、层级、ID 语义、extras 关系或图片模型时，必须增加 `schemaVersion` 并提供迁移程序。
- 文档措辞、错误消息和不改变有效文档集合的实现修正可以继续维护。
- 发现 Schema v1 无法表达的正式 MTR 内容时，应先记录真实样例和迁移方案，不得通过未声明字段绕过校验。
- 生成 JSON 的字段契约可独立演进，但不得导致 YAML 信息丢失。

## 当前实现边界

Schema 冻结不代表完整流水线已经完成。PDF 获取、PDF 解析、新旧版本对齐、差异报告和 GitHub Actions 属于后续阶段。

## 最终 JSON Output Schema

- 当前版本：`1`
- 状态：已冻结
- 冻结日期：2026-09-23
- 规范文件：[`mtr-output.schema.json`](mtr-output.schema.json)

Output Schema v1 固定 `version / intro / main / appendices` 层级，以及 chapter、subrule、content 和 extras 的发布字段。版本说明和自动目录是仅有的允许英文为空的 content。

构建器会在写出文件前自动执行 Output Schema 校验，并额外检查：

- 所有发布 content ID 全局唯一；
- 主章节和附录完整且顺序固定；
- 目录包含每个章节、小节和附录的站内路由；
- Base64 Data URI 能严格解码为 PNG，且中英文引用同一图片；
- 以管道符开头的 Markdown 表格包含连续表头、分隔行和至少一行数据，且每行列数一致。

Output Schema v1 不得原地进行不兼容修改。需要改变顶层结构、必填字段、content 结构或路由模型时，必须新增 Output Schema 版本，并同步修改构建器和前端消费方。

