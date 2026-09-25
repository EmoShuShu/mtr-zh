# MTR Reference Pipeline

This directory is a language-neutral example for people who want to parse the official English Magic Tournament Rules PDF or maintain an MTR translation in another language.

It is separate from the Chinese production workflow. You can study it, copy it, or adapt it in a fork without using any Chinese source files.

## What it provides

- an English MTR PDF parser;
- exact additions and deletions between official versions;
- a bilingual YAML format using `en` and `translation`;
- translated text, annotations, tables, lists, images, and paragraph splits;
- inheritance of stable IDs, translations, annotations, and paragraph splits when the official PDF changes;
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

Prepare a complete update from an existing reviewed translation:

```bash
mtr-reference prepare-update \
  --state official/official-source.yaml \
  --manifest translations/current/manifest.yaml \
  --snapshot-root snapshots
```

When the PDF changes, this creates a snapshot containing the official PDF and parsed text, an exact English diff, inherited candidate YAML, and a review report. The currently reviewed translation is not overwritten. Copy the candidate to your editable translation directory, review every finding, then validate and build it.

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

The full update example is [`workflow-examples/official-update.yml`](workflow-examples/official-update.yml). A file in `workflow-examples` is documentation only; GitHub does not run it there.

To enable it in a fork:

1. Copy it to `.github/workflows/official-update.yml` in your repository.
2. Edit `MTR_MANIFEST`, `MTR_STATE`, and `MTR_SNAPSHOT_ROOT` near the top of the job so they match your repository.
3. In **Settings → Actions → General → Workflow permissions**, allow read and write access and allow GitHub Actions to create pull requests.
4. Commit the workflow to the default branch. Open **Actions → Check official MTR update → Run workflow** once to test and initialize the state.

The example runs every day at 06:00 in `Asia/Shanghai`:

```yaml
schedule:
  - cron: "0 6 * * *"
    timezone: "Asia/Shanghai"
```

Change those two values to use another local time. Scheduled workflows run from the default branch and may start a little late when GitHub Actions is busy.

No pull request is created when the official PDF is unchanged. When it changes, the workflow opens one draft pull request containing the snapshot and candidate. You do not need to inspect Actions every day: use the repository's **Watch → Custom → Pull requests** setting, or your normal GitHub email/web notification settings, if you want an explicit notification.

In that draft pull request, read `comparison/update.md`, copy `candidate` to the editable translation location named by `MTR_MANIFEST`, correct the target text and annotations, run `validate` and `build`, and only then mark the pull request ready and merge it. Keeping `MTR_MANIFEST` pointed at the newly reviewed version ensures that the next update compares against the previous version rather than the original translation.

The workflow never merges or publishes a translation by itself.

## Limitations

- The parser is designed for the official English MTR layout, not arbitrary PDFs.
- Diagrams and formula artwork may still require reviewed PNG files and alt text.
- A website may need a small adapter for its own JSON format.
- A new official PDF layout may require parser changes; validation is intended to stop major incomplete parses.

The data formats are documented by the JSON Schemas in [`schemas`](schemas/).
