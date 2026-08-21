"""Readers can see the verified books without scanning one.

The 250 verified records existed only behind a cover photo or an exact typed
title, which made the most trustworthy data in the product unreachable. These
routes expose it -- and the thing they must never expose is the review pipeline
that produced it.
"""
import database

from test_api_flows import auth, client, register_and_login  # noqa: F401

# Fields that describe the REVIEW process, not the book. A reader must never
# see these; if one appears, the allow-list in catalogue_for_reader has been
# widened by accident.
INTERNAL_FIELDS = (
    "verification_status", "source_dataset", "source_summary",
    "verified_summary", "short_summary_status", "normalized_title",
    "normalized_author", "human_verified", "machine_verified",
)


_next_id = [0]


def add_book(title="The Silent Patient", author="Alex Michaelides",
             genres="Psychological; Thrillers", status="VERIFIED"):
    # Identifiers must be distinct: catalogue_books has a unique index on them,
    # so reusing one ISBN across two fixtures fails on insert rather than in
    # the assertion.
    _next_id[0] += 1
    unique = _next_id[0]
    database.create_catalogue_book({
        "title": title, "author": author, "genres": genres,
        "isbn_13": "978125030%04d" % unique,
        "open_library_edition_id": "OL%05dM" % unique,
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified summary long enough for the test to use.",
        "short_summary": "Alicia shot her husband and has not spoken since.",
        "short_summary_status": "ok",
        "verification_status": status})


def test_browse_lists_verified_books(client):
    token = register_and_login(client)
    add_book()
    data = client.get("/api/catalogue", headers=auth(token)).get_json()
    assert data["total"] >= 1
    assert any(b["title"] == "The Silent Patient" for b in data["books"])


def test_browse_never_leaks_the_review_pipeline(client):
    token = register_and_login(client)
    add_book()
    book = client.get("/api/catalogue", headers=auth(token)).get_json()["books"][0]
    for field in INTERNAL_FIELDS:
        assert field not in book, "%s leaked to a reader" % field


def test_unverified_books_are_not_browsable(client):
    token = register_and_login(client)
    add_book(title="Not Reviewed Yet", status="PENDING")
    data = client.get("/api/catalogue", headers=auth(token)).get_json()
    assert all(b["title"] != "Not Reviewed Yet" for b in data["books"])


def test_search_filters_the_list(client):
    token = register_and_login(client)
    add_book()
    add_book(title="Wuthering Heights", author="Emily Bronte", genres="Classics")
    hits = client.get("/api/catalogue?q=Wuthering",
                      headers=auth(token)).get_json()["books"]
    assert len(hits) == 1
    assert hits[0]["title"] == "Wuthering Heights"


def test_a_browsed_book_carries_the_same_taste_evidence(client):
    # The point of browsing: the reader gets the same answer they would get
    # from a scan, without holding the book.
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

    add_book()
    # The shelf has to be bigger than the book being asked about. A subject is
    # only evidence if it is rare, so on a one-book catalogue "psychological"
    # is on 100% of the shelf and the honest answer is no_match. This test used
    # to pass on a census cached by whichever test file ran before it.
    for title, genres in (("Emma", "Romance"), ("Dune", "Space opera"),
                          ("Carrie", "Horror"), ("Rebecca", "Gothic"),
                          ("Hyperion", "Science")):
        add_book(title=title, genres=genres)
    listed = client.get("/api/catalogue", headers=auth(token)).get_json()["books"]
    record = next(b for b in listed if b["title"] == "The Silent Patient")

    detail = client.get("/api/catalogue/%d" % record["id"],
                        headers=auth(token)).get_json()
    assert detail["for_you"]["state"] == "match"
    assert detail["for_you"]["book_count"] == 3
    assert detail["book"]["summary"]


def test_browsing_writes_nothing_to_the_library(client):
    # Looking at a book is not reading it. Nothing may enter history.
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    add_book()
    record = client.get("/api/catalogue", headers=auth(token)).get_json()["books"][0]
    client.get("/api/catalogue/%d" % record["id"], headers=auth(token))
    assert database.get_user_history(user["id"]) == []


def test_a_missing_or_unverified_record_is_a_404(client):
    token = register_and_login(client)
    assert client.get("/api/catalogue/999999", headers=auth(token)).status_code == 404


def test_browsing_requires_a_signed_in_reader(client):
    assert client.get("/api/catalogue").status_code in (401, 403)
    assert client.get("/api/catalogue/1").status_code in (401, 403)


def test_a_missing_cover_is_healed_on_reselection(client):
    # Rows saved before catalogue records carried a cover keep an empty
    # thumbnail, and a re-scan reuses the cached row rather than rebuilding it.
    # Without healing, every book already in a library stays coverless.
    book_id = database.save_book({
        "title": "Old Row", "author": "A", "description": "", "ai_summary": "",
        "thumbnail": "", "page_count": 0, "publisher": "", "published_date": "",
        "categories": "Classics", "confidence": "high"})
    assert database.backfill_book_thumbnail(book_id, "https://example.test/c.jpg")
    assert database.get_book_by_id(book_id)["thumbnail"] == "https://example.test/c.jpg"


def test_healing_never_replaces_an_existing_cover(client):
    # "Only when empty" is what makes this safe to run on every selection.
    book_id = database.save_book({
        "title": "Has Cover", "author": "A", "description": "", "ai_summary": "",
        "thumbnail": "https://example.test/original.jpg", "page_count": 0,
        "publisher": "", "published_date": "", "categories": "Classics",
        "confidence": "high"})
    assert not database.backfill_book_thumbnail(book_id, "https://example.test/other.jpg")
    assert database.get_book_by_id(book_id)["thumbnail"] == "https://example.test/original.jpg"


def test_reconfirming_a_cached_book_heals_its_cover(client, monkeypatch):
    # End to end, through the confirm route: the first selection saves the book,
    # the second reuses the cached row. Without healing the second would leave
    # the empty thumbnail in place forever.
    import app as app_module

    token = register_and_login(client)
    candidate = {
        "title": "Dracula", "author": "Bram Stoker", "categories": "Horror",
        "page_count": 0, "publisher": "", "published_date": "1897",
        "thumbnail": "", "provider": "google", "google_books_id": "dracula-1",
        "isbn_13": "9780000000001", "score": 90.0,
        "decision": app_module.NEEDS_CONFIRMATION,
        "reasons": [], "score_breakdown": {}}

    def confirm_once():
        monkeypatch.setattr(app_module, "retrieve_tiered_candidates",
                            lambda *a, **k: {"decision": app_module.NEEDS_CONFIRMATION,
                                             "candidates": [dict(candidate)]})
        found = client.post("/api/search-by-title", headers=auth(token),
                            json={"title": "Dracula"}).get_json()
        return client.post("/api/identify/confirm", headers=auth(token), json={
            "attempt_id": found["attempt_id"],
            "candidate_id": found["candidates"][0]["candidate_id"]}).get_json()

    first = confirm_once()
    book_id = first["book"]["id"]
    assert not (database.get_book_by_id(book_id)["thumbnail"] or "").strip()

    # The source now supplies a cover, as catalogue rows began doing.
    candidate["thumbnail"] = "https://covers.openlibrary.org/b/olid/OL1M-M.jpg"
    second = confirm_once()

    assert second["book"]["id"] == book_id, "should have reused the cached row"
    assert database.get_book_by_id(book_id)["thumbnail"] == candidate["thumbnail"]
