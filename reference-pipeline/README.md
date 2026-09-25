# MTR Reference Pipeline

This directory is a self-contained, language-neutral reference implementation for parsing and maintaining a bilingual version of the Magic: The Gathering Tournament Rules (MTR).

It is intentionally isolated from the production Chinese translation pipeline in the parent repository. It does not import the parent `mtr_pipeline`, modify Chinese source files, or participate in the Chinese release process. Maintainers can study this directory, copy it, or fork the repository and adapt it for another target language.

## What is included

- deterministic extraction of English paragraphs, lists, tables, headings, and page provenance from the official PDF;
- exact additions and deletions between two parsed versions, without a semantic-similarity score;
- a bilingual YAML Schema using `en` and the language-neutral `translation` field;
- support for translated annotations, annotation-driven paragraph splits, `joinAfter`, Markdown tables, and PNG assets;
- generic bilingual JSON and Markdown builders;
- secure discovery of the current official MTR PDF from the WPN rules page;
- an isolated German example and copyable GitHub Actions workflows.

The three public data contracts are documented by `schemas/mtr-official.schema.json`, `schemas/mtr-source.schema.json`, and `schemas/mtr-output.schema.json`.

The Chinese translation in the parent repository is the proven production implementation from which this reference was derived. The reference parser is tested against that parser on the same official PDF and must produce the same 94 sections and 947 structural units.

## Install

Python 3.12 or newer is required.

```bash
cd reference-pipeline
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Linux or macOS:

```bash
./.venv/bin/python -m pip install -e ".[dev]"
```

## Parse an official PDF

The parser does not require a translation project or any Chinese files.

```bash
mtr-reference parse MTR_EN.pdf \
  --json-out build/official.json \
  --markdown-out build/official.md
```

The JSON contains the PDF SHA-256, section and unit counts, section keys, content kinds, exact English text, and source page numbers.

## Compare official versions

```bash
mtr-reference diff previous.json current.json \
  --json-out build/changes.json \
  --markdown-out build/changes.md
```

The Markdown report uses these exact markers:

```text
[-deleted text-]{+inserted text+}
```

The JSON report stores reversible `equal`, `delete`, and `insert` operations. Sequence alignment is used only to keep unchanged units in order; the report contains no semantic similarity score.

## Maintain another language

The target locale is declared once in the manifest:

```yaml
document:
  sourceLanguage: en
  targetLanguage: de-DE
```

Translated content always uses `translation`, regardless of the target locale:

```yaml
blocks:
- id: mtr-1.1-b001
  en: There are two types of sanctioned Magic tournaments.
  translation: Es gibt zwei Arten sanktionierter Magic-Turniere.
  extras:
  - en: An English annotation.
    translation: Eine deutsche Anmerkung.
```

If an annotation applies to only part of an official paragraph, split that paragraph into multiple blocks and use `joinAfter` to record how the source and translation are reconstructed. The German example in `examples/de-DE` demonstrates this structure.

Validate and build it:

```bash
mtr-reference validate examples/de-DE/manifest.yaml
mtr-reference build examples/de-DE/manifest.yaml \
  --json-out build/de-DE.json \
  --markdown-out build/de-DE.md
```

Blank translations are allowed only during editing:

```bash
mtr-reference validate examples/de-DE/manifest.yaml --allow-incomplete
```

## Check the official source

Once a project has an `official/official-source.yaml` and its referenced `official.json`, run:

```bash
mtr-reference check \
  --state official/official-source.yaml \
  --output-dir official-candidate
```

If the PDF hash has not changed, no candidate is generated. If it changed, the command writes parsed JSON and Markdown, exact change reports, and a candidate state file. A maintainer must review and promote the candidate; this command does not merge or publish anything.

## GitHub Actions examples

Files in `workflow-examples` are documentation and are not executed by this repository. Copy the desired file into `.github/workflows/` in a fork, review its permissions, and adjust paths before enabling it.

## Known limitations

- The parser is specific to the current layout of the official English MTR PDF, not arbitrary PDFs.
- Positioned diagrams and formula artwork are not silently converted to text. Translation projects must supply reviewed PNG assets and alt text, or maintain explicit overrides.
- A new official layout can invalidate spacing or typography assumptions. Required chapters and appendices are checked so that major failures stop instead of producing an apparently complete document.
- The generic output is a reference format. A website may require a small adapter, like the Chinese website-specific `rules.json` builder in the parent project.

## Project isolation

This reference has its own package, schemas, tests, and dependency manifest. Removing the entire `reference-pipeline` directory has no effect on the Chinese production pipeline.

## Legal notice

This is unofficial Fan Content and is not approved or endorsed by Wizards of the Coast. Portions of the materials used are property of Wizards of the Coast LLC.

The parser code and the official MTR text are different categories of material. Review the repository's code license and Wizards' current policies before redistributing a PDF, a complete parsed English document, or a translation. The example workflows download the source from Wizards rather than bundling an official PDF in this reference directory.
