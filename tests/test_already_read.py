"""The card line that is a record rather than an inference.

Everything else on the result card is a judgement — the matcher's, or the taste
profile's — and can be wrong. "You have read this" cannot, and only stays true
because of the two rules exercised here.
"""
from werkzeug.security import generate_password_hash

import database
from test_api_flows import auth, client, register_and_login  # noqa: F401


def reader(email="shelf@example.com"):
    return database.create_user("Reader", email, generate_password_hash("x"),
                                email_verified=1)


def log(user_id, title, author, status="identified", favourite=0):
    book_id = database.save_book({"title": title, "author": author,
                                  "categories": "Fiction"})
    history_id = database.save_history(user_id, book_id)
    if status != "identified":
        database.update_history_reading(history_id, user_id, status, "")
    if favourite:
        database.toggle_favorite(history_id, user_id)
    return book_id


def test_a_finished_book_is_reported(client):
    user = reader()
    log(user, "Gone Girl", "Gillian Flynn", status="finished")
    record = database.prior_engagement(user, "Gone Girl", "Gillian Flynn")
    assert record and record["status"] == "finished"


def test_a_book_that_was_only_scanned_is_not(client):
    """The rule that keeps this honest instead of circular.

    Identifying a cover writes a history row. If every row counted, the second
    scan of a book would announce "you have read this" purely because of the
    first — the app congratulating itself for its own memory.
    """
    user = reader()
    log(user, "Dune", "Frank Herbert")          # scanned, never marked
    assert database.prior_engagement(user, "Dune", "Frank Herbert") is None


def test_currently_reading_counts(client):
    user = reader()
    log(user, "The Secret History", "Donna Tartt", status="reading")
    record = database.prior_engagement(user, "The Secret History", "Donna Tartt")
    assert record and record["status"] == "reading"


def test_a_favourite_counts_even_without_a_reading_status(client):
    user = reader()
    log(user, "Beloved", "Toni Morrison", favourite=1)
    record = database.prior_engagement(user, "Beloved", "Toni Morrison")
    assert record and record["is_favorite"]


def test_a_different_edition_of_the_same_book_still_counts(client):
    """Why this matches on title and author rather than on book id.

    The same work legitimately exists as several rows — another edition, or a
    second provider's record — each with its own id. Matching by id would miss
    exactly the case the feature exists for: you own one printing and are
    holding another.
    """
    user = reader()
    log(user, "Nineteen Eighty-Four", "George Orwell", status="finished")
    record = database.prior_engagement(user, "nineteen eighty-four",
                                       "George  Orwell")
    assert record is not None


def test_a_different_book_by_the_same_author_does_not_count(client):
    user = reader()
    log(user, "Sharp Objects", "Gillian Flynn", status="finished")
    assert database.prior_engagement(user, "Gone Girl", "Gillian Flynn") is None


def test_the_same_title_by_a_different_author_does_not_count(client):
    """"The Hobbit" names both Tolkien's novel and a strategy guide."""
    user = reader()
    log(user, "The Hobbit", "Prima Games", status="finished")
    assert database.prior_engagement(user, "The Hobbit", "J.R.R. Tolkien") is None


def test_another_reader_s_history_is_never_used(client):
    someone_else = reader("other@example.com")
    log(someone_else, "Kindred", "Octavia E. Butler", status="finished")
    me = reader("me@example.com")
    assert database.prior_engagement(me, "Kindred", "Octavia E. Butler") is None


def test_it_actually_reaches_the_client(client):
    """A field that never leaves the database layer helps nobody.

    Driven through the catalogue detail route, which builds the same card
    payload as a scan does but needs no network.
    """
    token = register_and_login(client)
    record_id = database.create_catalogue_book({
        "title": "Kindred", "author": "Octavia E. Butler",
        "genres": "Science; Speculative", "isbn_13": "9780807083697",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified summary long enough for the test.",
        "short_summary": "A stored overview.", "short_summary_status": "ok",
        "verification_status": "VERIFIED"})

    before = client.get("/api/catalogue/%d" % record_id, headers=auth(token))
    assert before.status_code == 200
    assert before.get_json()["already_read"] is None

    marked = client.post("/api/catalogue/%d/read" % record_id, headers=auth(token))
    assert marked.status_code == 200

    after = client.get("/api/catalogue/%d" % record_id, headers=auth(token))
    record = after.get_json()["already_read"]
    assert record is not None
    assert record["status"] in ("finished", "reading")
