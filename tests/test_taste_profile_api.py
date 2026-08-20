"""Is this for you? — end to end, through a real identification.

The unit tests in test_taste_profile.py pin the rules. These pin the wiring:
that the block reaches the client at all, that it is built from the signed-in
user's own library, and that one account can never see another's reading.
"""
import database
import app as app_module

from test_api_flows import auth, client, register_and_login  # noqa: F401

HIGH_CONFIDENCE = app_module.HIGH_CONFIDENCE


def add_read_book(user_id, title, categories, favorite=False,
                  status="finished"):
    """Put a book in someone's library the way the app would."""
    book_id = database.save_book({
        "title": title, "author": "A. Author", "description": "",
        "ai_summary": "", "thumbnail": "", "page_count": 300,
        "publisher": "", "published_date": "", "categories": categories,
        "confidence": "high"})
    history_id = database.save_history(user_id, book_id)
    database.update_history_reading(user_id, history_id, status, "")
    if favorite:
        database.toggle_favorite(user_id, history_id)
    return book_id


def accepted(monkeypatch, categories):
    """Force the funnel to accept one candidate outright."""
    monkeypatch.setattr(app_module, "retrieve_tiered_candidates",
                        lambda *a, **k: {
                            "decision": HIGH_CONFIDENCE,
                            "candidates": [{
                                "title": "The Silent Patient",
                                "author": "Alex Michaelides",
                                "categories": categories,
                                "description": "A publisher description.",
                                "page_count": 352, "thumbnail": "",
                                "publisher": "", "published_date": "",
                                "provider": "google",
                                "google_books_id": "silent-patient",
                                "isbn_13": "9781250301697",
                                "score": 95.0, "decision": HIGH_CONFIDENCE,
                                "reasons": [], "score_breakdown": {}}]})


def identify(client, token, title="The Silent Patient"):
    return client.post("/api/search-by-title", headers=auth(token),
                       json={"title": title}).get_json()


def test_result_carries_the_evidence_block(client, monkeypatch):
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    for title in ("Gone Girl", "Sharp Objects", "Before I Go to Sleep"):
        add_read_book(user["id"], title, "Psychological, Thrillers")

    accepted(monkeypatch, "Psychological, Thrillers, Crime")
    data = identify(client, token)

    assert data["for_you"]["state"] == "match"
    assert data["for_you"]["book_count"] == 3
    assert "Gone Girl" in data["for_you"]["examples"]


def test_a_brand_new_account_gets_cold_start_not_a_guess(client, monkeypatch):
    token = register_and_login(client)
    accepted(monkeypatch, "Psychological, Thrillers")
    assert identify(client, token)["for_you"]["state"] == "cold_start"


def test_scanning_alone_never_builds_a_profile(client, monkeypatch):
    # The circularity guard, end to end: three books identified but never
    # marked read or favourited leave the user still in cold start.
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    for title in ("Gone Girl", "Sharp Objects", "Before I Go to Sleep"):
        add_read_book(user["id"], title, "Psychological, Thrillers",
                      status="identified")

    accepted(monkeypatch, "Psychological, Thrillers")
    assert identify(client, token)["for_you"]["state"] == "cold_start"


def test_one_users_reading_is_never_evidence_for_another(client, monkeypatch):
    # The isolation guarantee. Seeded demo data must stay inside the demo
    # account; a normal account must never inherit it.
    first = register_and_login(client, email="reader@example.com")
    reader = database.get_user_by_email("reader@example.com")
    for title in ("Gone Girl", "Sharp Objects", "Before I Go to Sleep"):
        add_read_book(reader["id"], title, "Psychological, Thrillers")

    second = register_and_login(client, email="stranger@example.com")

    accepted(monkeypatch, "Psychological, Thrillers")
    assert identify(client, first)["for_you"]["state"] == "match"
    assert identify(client, second)["for_you"]["state"] == "cold_start"


def test_a_book_is_not_evidence_for_itself(client, monkeypatch):
    # Re-scanning a book you already finished must not report "you have read 1
    # book with these subjects" and then list the book on screen.
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    add_read_book(user["id"], "Gone Girl", "Psychological, Thrillers")
    add_read_book(user["id"], "Sharp Objects", "Psychological, Thrillers")
    add_read_book(user["id"], "The Silent Patient", "Psychological, Thrillers")

    accepted(monkeypatch, "Psychological, Thrillers")
    result = identify(client, token)["for_you"]
    assert "The Silent Patient" not in result["examples"]
