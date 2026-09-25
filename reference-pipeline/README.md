# MTR Reference Pipeline

This directory is a language-neutral example for people who want to parse the official English Magic Tournament Rules PDF or maintain an MTR translation in another language.

It is separate from the Chinese production workflow. You can study it, copy it, or adapt it in a fork without using any Chinese source files.

## What it provides

- an English MTR PDF parser;
- exact additions and deletions between official versions;
- a bilingual YAML format using `en` and `translation`;
- translated text, annotations, tables, lists, images, and paragraph splits;
- generic JSON and Markdown output;
- examples for validation and automatic update checks.

The parser, schemas, example, and tests are all contained in this directory.

## Quick start

Python 3.12 or newer is required.

```bash
cd reference-pipeline
python -m venv .venv
```

Install the project with the command for your system:

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

```bash
# Linux or macOS
./.venv/bin/python -m pip install -e ".[dev]"
```

Parse an official PDF:

```bash
mtr-reference parse MTR_EN.pdf \
  --json-out build/official.json \
  --markdown-out build/official.md
```

Compare two parsed versions:

```bash
mtr-reference diff previous.json current.json \
  --json-out build/changes.json \
  --markdown-out build/changes.md
```

The Markdown report uses `[-deleted-]` and `{+inserted+}` markers. It does not use a semantic-similarity score.

## Translation format

Choose the target language once in the manifest:

```yaml
document:
  sourceLanguage: en
  targetLanguage: de-DE
```

Use the same `translation` field for every target language:

```yaml
blocks:
- id: mtr-1.1-b001
  en: There are two types of sanctioned Magic tournaments.
  translation: Es gibt zwei Arten sanktionierter Magic-Turniere.
  extras:
  - en: An English annotation.
    translation: Eine deutsche Anmerkung.
```

See [`examples/de-DE`](examples/de-DE/) for a small working example.

Validate and build a translation:

```bash
mtr-reference validate examples/de-DE/manifest.yaml
mtr-reference build examples/de-DE/manifest.yaml \
  --json-out build/de-DE.json \
  --markdown-out build/de-DE.md
```

## Automatic updates

The `check` command can look for a new official PDF and prepare parsed files and an exact change report. It never merges or publishes a translation by itself.

Copyable GitHub Actions examples are available in [`workflow-examples`](workflow-examples/). Review their paths and permissions before enabling them in a fork.

## Limitations

- The parser is designed for the official English MTR layout, not arbitrary PDFs.
- Diagrams and formula artwork may still require reviewed PNG files and alt text.
- A website may need a small adapter for its own JSON format.
- A new official PDF layout may require parser changes; validation is intended to stop major incomplete parses.

The data formats are documented by the JSON Schemas in [`schemas`](schemas/).

## Disclaimer

This is unofficial Fan Content and is not approved or endorsed by Wizards of the Coast. Portions of the materials used are property of Wizards of the Coast LLC.

The parser code and the official MTR text are different kinds of material. Review the repository license and Wizards' current policies before redistributing an official PDF, a complete parsed English document, or a translation.
