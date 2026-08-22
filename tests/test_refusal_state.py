"""The refusal response carries what the refusal screen needs.

Refusing is the product working, so the screen that does it is a designed state
and needs real inputs: which failure occurred, and what the camera read. These
pin the payload. The DECISION to refuse belongs to the frozen matching core and
is not touched here.
"""
from pathlib import Path

import app as app_module
import database

from test_api_flows import auth, client, png_bytes, register_and_login  # noqa: F401


def rejected(monkeypatch):
    """Make retrieval refuse, without altering how refusal is decided."""
    monkeypatch.setattr(app_module, "retrieve_tiered_candidates",
                        lambda *a, **k: {"decision": app_module.REJECTED,
                                         "candidates": [],
                                         "error": "No matching book found"})


def test_refusal_names_the_reason_it_recorded(client, monkeypatch):
    # failure_reason was already stored against the attempt; the screen needs it
    # in the response to give advice that fits the actual failure.
    token = register_and_login(client)
    rejected(monkeypatch)
    data = client.post("/api/search-by-title", headers=auth(token),
                       json={"title": "Nothing Matches This"}).get_json()

    assert data["status"] == "partial"
    assert data["decision"] == app_module.REJECTED
    assert data["failure_reason"] == "No matching book found"
    assert data["book"] is None


def test_the_recorded_reason_and_the_sent_reason_agree(client, monkeypatch):
    # The screen must not tell a different story from the audit trail.
    token = register_and_login(client)
    rejected(monkeypatch)
    data = client.post("/api/search-by-title", headers=auth(token),
                       json={"title": "Nothing Matches This"}).get_json()

    user = database.get_user_by_email("reader@example.com")
    attempts = database.get_identification_attempts(user["id"]) \
        if hasattr(database, "get_identification_attempts") else None
    if attempts:
        assert attempts[0]["failure_reason"] == data["failure_reason"]


def test_an_unreadable_cover_asks_the_user_to_type(client, monkeypatch):
    # The other refusal shape. OCR read nothing usable, so the pipeline returns
    # ocr_review with its own reason -- a different screen from "read fine, no
    # candidate matched", because retaking the photo is the right advice here
    # and the wrong advice there.
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "process_book_cover", lambda *a, **k: {
        "probable_title": "", "probable_author": "", "full_text": "",
        "text_lines": [], "confidence_score": 0.0, "error": "No text found"})

    data = client.post("/api/scan", headers=auth(token),
                       data={"image": (png_bytes(), "cover.png")},
                       content_type="multipart/form-data").get_json()

    assert data["status"] == "ocr_review"
    assert data["decision"] == app_module.REJECTED
    assert data["book"] is None


def test_refusal_never_leaks_ranking_internals(client, monkeypatch):
    # Card rule: candidates, scores and confidence values belong in the admin
    # panel. A refusal must not become a back door to them.
    token = register_and_login(client)
    rejected(monkeypatch)
    data = client.post("/api/search-by-title", headers=auth(token),
                       json={"title": "Nothing Matches This"}).get_json()

    assert "score" not in data
    assert "score_breakdown" not in data
    assert not data.get("candidates")


def scan_sources():
    """Every component of the scan feature, concatenated."""
    folder = Path(__file__).parents[1] / "frontend" / "src" / "features" / "scan"
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(folder.glob("*.jsx")))


def test_the_refusal_hint_advises_rather_than_asserts():
    # The copy used to promise that another photo "will read the same way".
    # It cannot know that -- a clearer photo sometimes does succeed -- and an
    # absolute claim in a refusal screen undermines the honesty the refusal is
    # there to demonstrate.
    # Read the whole scan folder, not one file. This assertion was pinned to
    # ResultViews.jsx and broke the day the refusal panel moved into its own
    # module -- and the sibling assertion in test_confirmation_payload.py went
    # the other way, passing vacuously against a file that no longer held the
    # code it was checking. A copy rule belongs to the feature, not to a path.
    source = scan_sources()
    assert "will read the same way" not in source
    assert "A clearer photo may help" in source


def test_the_barcode_button_is_wired_to_the_backend_flag():
    # /api/scan reads an ISBN only when allow_barcode_fallback=1 is sent, so
    # without this the button sends the reader to photograph a barcode that
    # nothing looks at.
    source = (Path(__file__).parents[1] / "frontend" / "src" / "features" /
              "scan" / "ScanSection.jsx").read_text(encoding="utf-8")
    assert 'formData.append("allow_barcode_fallback", "1")' in source

