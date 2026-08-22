"""A single-candidate confirmation must carry the card's content.

The interface no longer shows a chooser when there is exactly one candidate; it
shows the result card with the confirmation folded in. That only works if the
response already carries what the card renders -- and it must do so WITHOUT
writing anything, because the reader has not agreed to anything yet.
"""
import app as app_module
import database

from test_api_flows import auth, client, register_and_login  # noqa: F401


def one_candidate(monkeypatch, categories="Psychological, Thrillers"):
    monkeypatch.setattr(app_module, "retrieve_tiered_candidates",
                        lambda *a, **k: {
                            "decision": app_module.NEEDS_CONFIRMATION,
                            "candidates": [{
                                "title": "The Silent Patient",
                                "author": "Alex Michaelides",
                                "categories": categories,
                                "page_count": 336, "publisher": "Celadon",
                                "published_date": "2019", "thumbnail": "",
                                "provider": "google",
                                "google_books_id": "silent-patient",
                                "isbn_13": "9781250301697",
                                "score": 78.0,
                                "decision": app_module.NEEDS_CONFIRMATION,
                                "reasons": ["Title is an exact or near-exact match"],
                                "score_breakdown": {"title_similarity": 100}}]})


def two_candidates(monkeypatch):
    def make(title, gid):
        return {"title": title, "author": "A. Author", "categories": "Fantasy",
                "page_count": 300, "publisher": "", "published_date": "2020",
                "thumbnail": "", "provider": "google", "google_books_id": gid,
                "isbn_13": "", "score": 70.0,
                "decision": app_module.NEEDS_CONFIRMATION,
                "reasons": [], "score_breakdown": {}}
    monkeypatch.setattr(app_module, "retrieve_tiered_candidates",
                        lambda *a, **k: {"decision": app_module.NEEDS_CONFIRMATION,
                                         "candidates": [make("Book One", "g1"),
                                                        make("Book Two", "g2")]})


def search(client, token, title="The Silent Patient", isbn=None):
    payload = {"title": title}
    if isbn:
        payload["isbn"] = isbn
    return client.post("/api/search-by-title", headers=auth(token),
                       json=payload).get_json()


def test_single_candidate_carries_the_taste_evidence(client, monkeypatch):
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    for title in ("Gone Girl", "Sharp Objects", "Before I Go to Sleep"):
        book_id = database.save_book({
            "title": title, "author": "A", "description": "", "ai_summary": "",
            "thumbnail": "", "page_count": 0, "publisher": "",
            "published_date": "", "categories": "Psychological, Thrillers",
            "confidence": "high"})
        hid = database.save_history(user["id"], book_id)
        database.update_history_reading(user["id"], hid, "finished", "")

    one_candidate(monkeypatch)
    data = search(client, token)

    assert data["status"] == "needs_confirmation"
    top = data["candidates"][0]
    assert top["for_you"]["state"] == "match"
    assert top["for_you"]["book_count"] == 3


def test_single_candidate_carries_edition_evidence(client, monkeypatch):
    token = register_and_login(client)
    one_candidate(monkeypatch)
    top = search(client, token)["candidates"][0]
    ev = top["edition_evidence"]
    assert ev["identity"] == "unconfirmed"
    assert ev["page_basis"] == "google_volume"


def test_a_typed_isbn_is_honoured_before_confirmation(client, monkeypatch):
    token = register_and_login(client)
    one_candidate(monkeypatch)
    top = search(client, token, isbn="9781250301697")["candidates"][0]
    assert top["edition_evidence"]["identity"] == "isbn_confirmed"
    assert top["edition_evidence"]["page_basis"] == "isbn_edition"


def test_nothing_is_saved_until_the_reader_confirms(client, monkeypatch):
    # The whole point of folding confirmation into the card. Showing the book
    # must not put it in anyone's library.
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    one_candidate(monkeypatch)
    data = search(client, token)

    assert data.get("history_id") is None
    assert database.get_user_history(user["id"]) == []

    confirm = client.post("/api/identify/confirm", headers=auth(token),
                          json={"attempt_id": data["attempt_id"],
                                "candidate_id": data["candidates"][0]["candidate_id"]})
    assert confirm.status_code == 200
    assert len(database.get_user_history(user["id"])) == 1


def test_two_candidates_still_go_to_the_chooser(client, monkeypatch):
    token = register_and_login(client)
    two_candidates(monkeypatch)
    data = search(client, token, title="Book")
    assert data["status"] == "needs_confirmation"
    assert len(data["candidates"]) == 2


def test_the_consumer_ui_no_longer_prints_scores_or_ranking_reasons():
    # A percentage invites trust the method cannot justify, and the reasons
    # describe the algorithm rather than the book.
    # The whole scan folder: the chooser this guards moved out of
    # ResultViews.jsx into CandidateSelection.jsx, and reading one file meant
    # the assertions passed without looking at the code they describe.
    from pathlib import Path
    folder = (Path(__file__).parents[1] / "frontend" / "src" / "features" / "scan")
    source = "\n".join(path.read_text(encoding="utf-8")
                       for path in sorted(folder.glob("*.jsx")))
    assert "confidence-score" not in source
    assert "candidate-reasons" not in source
    assert "{candidate.score}%" not in source
