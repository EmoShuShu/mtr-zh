from __future__ import annotations

from mtr_reference.diff import diff_documents, exact_text_changes, render_exact_changes
from mtr_reference.models import OfficialDocument, OfficialSection, OfficialUnit


def _document(texts: list[str]) -> OfficialDocument:
    return OfficialDocument(
        "a" * 64,
        [
            OfficialSection(
                "1.1",
                "Test",
                [OfficialUnit("paragraph", text, (5,)) for text in texts],
            )
        ],
    )


def test_exact_diff_is_reversible_and_has_no_similarity_score():
    changes = exact_text_changes("Draw one card.", "Immediately draw two cards.")
    old = "".join(change["text"] for change in changes if change["op"] != "insert")
    new = "".join(change["text"] for change in changes if change["op"] != "delete")
    assert old == "Draw one card."
    assert new == "Immediately draw two cards."
    rendered = render_exact_changes(changes)
    assert "[-Draw-]" in rendered
    assert "{+Immediately+}" in rendered
    assert "{+draw+}" in rendered
    assert "{+two cards+}" in rendered


def test_document_diff_reports_replacement_and_insertion():
    changes = diff_documents(_document(["Old text."]), _document(["New text.", "Added."]))
    assert [change.operation for change in changes] == ["replace", "insert"]
    assert changes[0].old_text == "Old text."
    assert changes[0].new_text == "New text."
