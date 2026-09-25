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

通常不需要手动运行更新和发布流程；GitHub Actions 会在 PR 中完成校验，并在合并后发布正式文件。

## 其他语言

如果希望参考 PDF 解析器或维护其他语言的 MTR，请阅读英文的 [`reference-pipeline/README.md`](reference-pipeline/README.md)。该参考流程与中文正式流程相互独立。