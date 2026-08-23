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


def test_a_verified_book_gets_the_same_card_a_scan_gets(client):
    """The 60 books this project vouches for used to show LESS than a book it
    found on the internet: Browse had a simplified card of its own, with no
    reader rating, no edition line and no library controls. The detail route
    now answers with the confirm route's payload, field for field, so both
    render the same component -- and the only difference left is where the data
    came from, which the card's source badge already states."""
    token = register_and_login(client)
    add_book()
    record = client.get("/api/catalogue", headers=auth(token)).get_json()["books"][0]

    card = client.get("/api/catalogue/%d" % record["id"],
                      headers=auth(token)).get_json()
    for field in ("book", "for_you", "already_read", "live", "edition_evidence",
                  "history_id", "is_favorite", "catalogue_status",
                  "summary_trust", "summary_status"):
        assert field in card, "%s missing -- the card cannot render it" % field
    assert card["catalogue_status"] == "VERIFIED"
    assert card["summary_trust"] == "CATALOGUE_VERIFIED"
    # The overview panel reads its source text off catalogue_id +
    # verified_summary, exactly as it does for a scanned catalogue book.
    assert card["book"]["catalogue_id"] == record["id"]
    assert card["book"]["verified_summary"]
    assert card["book"]["id"]         # a books row, so live signals can key on it


def test_browsing_writes_nothing_to_the_library(client):
    # Looking at a book is not reading it. Nothing may enter history.
    #
    # The card does need a books row -- live signals key on books.id and the
    # overview poller asks /api/books/<id>/summary -- but that row is the
    # SHARED cache every path uses, not this reader's library.
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    add_book()
    record = client.get("/api/catalogue", headers=auth(token)).get_json()["books"][0]
    card = client.get("/api/catalogue/%d" % record["id"],
                      headers=auth(token)).get_json()
    assert database.get_user_history(user["id"]) == []
    assert card["history_id"] is None
    assert card["is_favorite"] is False


def test_opening_the_same_book_twice_reuses_one_cached_row(client):
    """save_book() always INSERTs. Before the lookup that backs this, every
    path turning a catalogue record into a books row made a NEW one -- which
    was survivable at one row per "I have read this", and is not survivable at
    one row per view."""
    token = register_and_login(client)
    add_book()
    record = client.get("/api/catalogue", headers=auth(token)).get_json()["books"][0]

    ids = set()
    for _ in range(3):
        card = client.get("/api/catalogue/%d" % record["id"],
                          headers=auth(token)).get_json()
        ids.add(card["book"]["id"])
    client.post("/api/catalogue/%d/read" % record["id"], headers=auth(token))
    ids.add(client.get("/api/catalogue/%d" % record["id"],
                       headers=auth(token)).get_json()["book"]["id"])
    assert len(ids) == 1, "a books row was created per view"


def test_saying_you_have_read_it_lights_up_the_library_controls(client):
    """The card hides its reading status and favourite button on history_id, so
    browsing shows the book without pretending it is in a library it is not in.
    Saying you have read it is what puts it there."""
    token = register_and_login(client)
    add_book()
    record = client.get("/api/catalogue", headers=auth(token)).get_json()["books"][0]
    assert client.get("/api/catalogue/%d" % record["id"],
                      headers=auth(token)).get_json()["history_id"] is None

    client.post("/api/catalogue/%d/read" % record["id"], headers=auth(token))
    after = client.get("/api/catalogue/%d" % record["id"],
                       headers=auth(token)).get_json()
    assert after["history_id"] is not None
    assert after["already_read"] is not None


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


# ----- the cover the reader actually sees -----

def test_a_book_carries_a_second_way_to_ask_for_its_cover(client):
    """Audited over all 250 verified books: the cover of the EDITION we stored
    404s for 90 of them, and 48 of those have a perfectly good cover filed
    under the ISBN -- The Da Vinci Code among them. A third of Browse was
    showing "Cover unavailable" for books whose cover we were asking for the
    wrong way."""
    token = register_and_login(client)
    add_book(title="The Da Vinci Code")
    listed = client.get("/api/catalogue", headers=auth(token)).get_json()["books"]
    book = next(b for b in listed if b["title"] == "The Da Vinci Code")

    # The cover now comes from this repository -- 60 fixed books, downloaded
    # once by curate/fetch_covers.py -- so a catalogue card needs no network.
    assert book["thumbnail"] == "/covers/%d.jpg" % book["id"]
    # Open Library stays behind it as the safety net, and it is the ISBN route
    # rather than the edition one, because the edition URL 404s for a third of
    # the shelf and 48 of those books are filed correctly under their ISBN.
    assert "/b/isbn/" in book["thumbnail_fallback"]
    assert book["isbn_13"] in book["thumbnail_fallback"]


def test_the_shelf_serves_its_own_cover_files(client):
    """The point of committing them: no provider is involved in rendering a
    catalogue card. A missing file must still 404 rather than reach outside the
    covers folder -- the route takes an int, so no request-supplied filename
    ever touches the filesystem."""
    token = register_and_login(client)
    assert client.get("/covers/999999.jpg").status_code == 404
    assert client.get("/covers/../app.py").status_code in (404, 308)


def test_no_isbn_means_no_second_url_rather_than_a_broken_one(client):
    token = register_and_login(client)
    database.create_catalogue_book({
        "title": "No Identifiers Here", "author": "A", "genres": "Fiction",
        "open_library_edition_id": "OL999999M",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified summary long enough for the test to use.",
        "short_summary": "A stored overview.", "short_summary_status": "ok",
        "verification_status": "VERIFIED"})
    listed = client.get("/api/catalogue", headers=auth(token)).get_json()["books"]
    book = next(b for b in listed if b["title"] == "No Identifiers Here")
    assert book["thumbnail_fallback"] == ""
