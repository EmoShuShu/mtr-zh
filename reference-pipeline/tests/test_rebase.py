from __future__ import annotations

from pathlib import Path

import yaml

from mtr_reference.models import OfficialDocument
from mtr_reference.models import OfficialSection, OfficialUnit
from mtr_reference.project import assemble_group, load_project
from mtr_reference.rebase import Finding, rebase_groups, rebase_project


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCHEMA = ROOT / "schemas/mtr-source.schema.json"


def test_changed_paragraph_keeps_ids_translation_annotation_and_split():
    groups = [
        {
            "id": "mtr-1.1-g001",
            "type": "paragraph",
            "blocks": [
                {
                    "id": "mtr-1.1-b001",
                    "en": "A player must shuffle",
                    "translation": "Spieler müssen mischen",
                    "joinAfter": {"en": " ", "translation": " "},
                },
                {
                    "id": "mtr-1.1-b002",
                    "en": "before presenting the deck.",
                    "translation": "bevor sie das Deck präsentieren.",
                    "extras": [
                        {
                            "en": "This is an annotation.",
                            "translation": "Dies ist eine Anmerkung.",
                        }
                    ],
                },
            ],
        }
    ]
    official = OfficialSection(
        "1.1",
        "Test",
        [OfficialUnit("paragraph", "A player must thoroughly shuffle before presenting the deck.", (1,))],
    )
    findings: list[Finding] = []

    result = rebase_groups(groups, official, "mtr-1.1", findings)

    assert [block["id"] for block in result[0]["blocks"]] == [
        "mtr-1.1-b001",
        "mtr-1.1-b002",
    ]
    assert result[0]["blocks"][1]["extras"] == groups[0]["blocks"][1]["extras"]
    assert result[0]["blocks"][0]["translation"] == "Spieler müssen mischen"
    assert assemble_group(result[0], "en") == official.units[0].text
    assert [finding.code for finding in findings] == ["official-text-changed"]


def test_new_and_removed_units_are_explicit_and_new_translation_is_blank():
    groups = [
        {
            "id": "mtr-1.1-g001",
            "type": "paragraph",
            "blocks": [
                {"id": "mtr-1.1-b001", "en": "Keep this sentence.", "translation": "Behalten."}
            ],
        },
        {
            "id": "mtr-1.1-g002",
            "type": "paragraph",
            "blocks": [
                {"id": "mtr-1.1-b002", "en": "Remove this sentence.", "translation": "Entfernen."}
            ],
        },
    ]
    official = OfficialSection(
        "1.1",
        "Test",
        [
            OfficialUnit("paragraph", "Keep this sentence.", (1,)),
            OfficialUnit("paragraph", "Add a completely different rule.", (1,)),
        ],
    )
    findings: list[Finding] = []

    result = rebase_groups(groups, official, "mtr-1.1", findings)

    assert result[0]["id"] == "mtr-1.1-g001"
    assert result[1]["blocks"][0]["translation"] == ""
    assert result[1]["id"] == "mtr-1.1-g003"
    assert {finding.code for finding in findings} == {
        "new-official-content",
        "removed-official-content",
    }


def test_table_rows_inherit_by_first_column_key():
    groups = [
        {
            "id": "mtr-d-g001",
            "type": "table",
            "blocks": [
                {
                    "id": "mtr-d-b001",
                    "en": "| Program | Level |\n| --- | --- |",
                    "translation": "| Programm | Stufe |\n| --- | --- |",
                },
                {
                    "id": "mtr-d-b002",
                    "en": "| Friday Night Magic | Regular |",
                    "translation": "| Friday Night Magic | Regulär |",
                    "extras": [
                        {"en": "Old note.", "translation": "Alte Anmerkung."}
                    ],
                },
            ],
        }
    ]
    official = OfficialSection(
        "appendix-d",
        "Test",
        [
            OfficialUnit(
                "table",
                "| Program | Rules Enforcement Level |\n| --- | --- |\n| Friday Night Magic | Regular (Competitive recommended) |",
                (1,),
            )
        ],
    )
    findings: list[Finding] = []

    result = rebase_groups(groups, official, "mtr-d", findings)

    row = result[0]["blocks"][1]
    assert row["id"] == "mtr-d-b002"
    assert row["translation"] == "| Friday Night Magic | Regulär |"
    assert row["extras"] == groups[0]["blocks"][1]["extras"]
    assert assemble_group(result[0], "en") == official.units[0].text
    assert [finding.code for finding in findings] == ["official-text-changed"]


def test_image_anchor_is_preserved_once_when_new_text_precedes_it():
    groups = [
        {
            "id": "mtr-a-g001",
            "type": "image",
            "blocks": [
                {
                    "id": "mtr-a-b001",
                    "asset": "assets/diagram.png",
                    "alt": {"en": "Diagram", "translation": "Diagramm"},
                }
            ],
        },
        {
            "id": "mtr-a-g002",
            "type": "paragraph",
            "blocks": [
                {"id": "mtr-a-b002", "en": "Existing body.", "translation": "Bestehend."}
            ],
        },
    ]
    official = OfficialSection(
        "appendix-a",
        "Test",
        [
            OfficialUnit("paragraph", "New unrelated body.", (1,)),
            OfficialUnit("paragraph", "Existing body.", (1,)),
        ],
    )

    result = rebase_groups(groups, official, "mtr-a", [])

    assert sum(group["type"] == "image" for group in result) == 1
    assert result[0]["type"] == "image"


def test_full_project_rebase_writes_a_valid_candidate_with_annotations(tmp_path: Path):
    files: list[str] = []
    sections: list[OfficialSection] = []

    def text_group(prefix: str, text: str) -> dict:
        block = {
            "id": f"{prefix}-b001",
            "en": text,
            "translation": f"Translated {text}",
            "extras": [{"en": "Annotation.", "translation": "Translated annotation."}],
        }
        return {"id": f"{prefix}-g001", "type": "paragraph", "blocks": [block]}

    intro = {
        "id": "mtr-introduction",
        "chapter": "Introduction",
        "en": "Introduction",
        "translation": "Einleitung",
        "groups": [text_group("mtr-introduction", "Introduction body.")],
    }
    files.append("introduction.yaml")
    (tmp_path / files[-1]).write_text(
        yaml.safe_dump({"chapter": intro}, sort_keys=False), encoding="utf-8"
    )
    sections.append(
        OfficialSection(
            "introduction", "Introduction", [OfficialUnit("paragraph", "Introduction body.", (1,))]
        )
    )

    for number in range(1, 11):
        key = f"{number}.1"
        chapter = {
            "id": f"mtr-{number}",
            "chapter": f"{number}.",
            "en": f"Chapter {number}",
            "translation": f"Kapitel {number}",
            "sections": [
                {
                    "id": f"mtr-{key}",
                    "chapter": key,
                    "en": f"Section {key}",
                    "translation": f"Abschnitt {key}",
                    "groups": [text_group(f"mtr-{key}", f"Body {key}.")],
                }
            ],
        }
        filename = f"chapter-{number:02d}.yaml"
        files.append(filename)
        (tmp_path / filename).write_text(
            yaml.safe_dump({"chapter": chapter}, sort_keys=False), encoding="utf-8"
        )
        body = "Body 1.1 updated." if key == "1.1" else f"Body {key}."
        sections.append(
            OfficialSection(key, f"Section {key}", [OfficialUnit("paragraph", body, (number,))])
        )

    for letter in "ABCDEF":
        key = f"appendix-{letter.lower()}"
        chapter = {
            "id": f"mtr-{key}",
            "chapter": f"Appendix {letter}",
            "en": f"Appendix {letter}",
            "translation": f"Anhang {letter}",
            "groups": [text_group(f"mtr-{key}", f"Appendix {letter} body.")],
        }
        filename = f"appendix-{letter.lower()}.yaml"
        files.append(filename)
        (tmp_path / filename).write_text(
            yaml.safe_dump({"chapter": chapter}, sort_keys=False), encoding="utf-8"
        )
        sections.append(
            OfficialSection(
                key,
                f"Appendix {letter}",
                [OfficialUnit("paragraph", f"Appendix {letter} body.", (20,))],
            )
        )

    manifest = {
        "schemaVersion": 1,
        "document": {
            "id": "mtr",
            "sourceLanguage": "en",
            "targetLanguage": "de-DE",
            "title": {"en": "Magic Tournament Rules", "translation": "Turnierregeln"},
            "version": "20260227",
            "effectiveDate": "2026-02-27",
        },
        "files": files,
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    project = load_project(manifest_path, SOURCE_SCHEMA, require_complete=True)
    candidate = tmp_path / "candidate"

    report = rebase_project(
        project,
        OfficialDocument("a" * 64, sections),
        effective_date="2026-09-25",
        pdf_url="https://media.wizards.com/MTR.pdf",
        output_dir=candidate,
        report_path=tmp_path / "comparison/update.json",
    )

    validated = load_project(candidate / "manifest.yaml", SOURCE_SCHEMA, require_complete=True)
    changed = validated.chapters[1]["sections"][0]["groups"][0]["blocks"][0]
    assert changed["translation"] == "Translated Body 1.1."
    assert changed["extras"][0]["translation"] == "Translated annotation."
    assert report["summary"] == {"error": 0, "warning": 1, "info": 0}
    assert "similarity" not in (tmp_path / "comparison/update.json").read_text(encoding="utf-8")
