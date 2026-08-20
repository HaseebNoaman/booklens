"""A new account must get something.

"Is this for you?" answers from books the reader engaged with, so a fresh
account had nothing to answer from -- and the cold-start state was a dead end:
it asked the reader to save books while giving them no way to do it.

Three changes, and the ordering between them is the part that matters. Books
someone actually read always outrank interests they merely ticked, because the
value of this section is the surprise of "you have read four like this" and an
echo of your own declaration carries none.
"""
import database
import taste_profile as tp

from test_api_flows import auth, client, register_and_login  # noqa: F401


def row(title, categories, status="finished"):
    return {"title": title, "categories": categories,
            "is_favorite": 0, "reading_status": status}


# ----- one book is enough -----

def test_a_single_book_now_earns_real_evidence():
    # Was three. The "one book is noise" objection applies to predicting
    # enjoyment; this states a checkable fact instead.
    result = tp.assess("Crime, Suspense", [row("In Cold Blood", "Crime, True Crime")])
    assert result["state"] == tp.STATE_MATCH
    assert result["book_count"] == 1
    assert result["examples"] == ["In Cold Blood"]
    assert tp.MIN_PROFILE_BOOKS == 1


# ----- interests are the weaker signal, and stay that way -----

def test_interests_answer_only_when_there_are_no_books():
    result = tp.assess("Crime, Suspense", [], "", "Crime, Fantasy")
    assert result["state"] == tp.STATE_INTEREST_MATCH
    assert result["subjects"] == ["Crime"]


def test_a_real_book_outranks_a_ticked_interest():
    # The ordering guarantee. If this inverts, the section starts telling
    # readers that a thriller is a thriller.
    result = tp.assess("Crime", [row("In Cold Blood", "Crime")], "", "Crime")
    assert result["state"] == tp.STATE_MATCH
    assert result["examples"] == ["In Cold Blood"]


def test_interests_rescue_a_thin_profile_that_misses():
    # One book, but about something else. Interests can still speak.
    result = tp.assess("Fantasy", [row("In Cold Blood", "Crime")], "", "Fantasy")
    assert result["state"] == tp.STATE_INTEREST_MATCH


def test_no_books_and_no_interests_is_still_cold_start():
    assert tp.assess("Crime", [], "", "")["state"] == tp.STATE_COLD_START


def test_interest_match_never_claims_books_were_read():
    result = tp.assess("Crime", [], "", "Crime")
    assert result["examples"] == []
    assert result["book_count"] == 0


_seq = [0]


def add_catalogue_book(title="Gone Girl", genres="Psychological; Thrillers"):
    _seq[0] += 1
    return database.create_catalogue_book({
        "title": title, "author": "Gillian Flynn", "genres": genres,
        "isbn_13": "97800000%05d" % _seq[0],
        "open_library_edition_id": "OL%04dM" % _seq[0],
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified summary long enough for the test.",
        "short_summary": "A stored overview.", "short_summary_status": "ok",
        "verification_status": "VERIFIED"})


# ----- choosing and editing interests -----

def test_interests_can_be_saved_and_changed(client):
    token = register_and_login(client)
    # The picker is built from the catalogue's own shelves, so there has to be
    # a catalogue. An empty one correctly offers nothing to choose.
    add_catalogue_book(title="Gone Girl", genres="Psychological; Thrillers")
    add_catalogue_book(title="Dracula", genres="Horror; Gothic")
    available = client.get("/api/interests",
                           headers=auth(token)).get_json()["available"]
    assert available, "the picker must offer subjects the catalogue actually uses"

    first, second = available[0], available[1]
    saved = client.post("/api/profile/interests", headers=auth(token),
                        json={"interests": [first]}).get_json()
    assert saved["interests"] == [first]

    # Changed later from account settings -- taste moves, and a signup choice
    # must not be permanent.
    changed = client.post("/api/profile/interests", headers=auth(token),
                          json={"interests": [second]}).get_json()
    assert changed["interests"] == [second]
    assert client.get("/api/profile", headers=auth(token)).get_json()["interests"] == second


def test_interests_can_be_cleared(client):
    token = register_and_login(client)
    add_catalogue_book(title="Gone Girl", genres="Psychological; Thrillers")
    available = client.get("/api/interests", headers=auth(token)).get_json()["available"]
    client.post("/api/profile/interests", headers=auth(token),
                json={"interests": [available[0]]})
    cleared = client.post("/api/profile/interests", headers=auth(token),
                          json={"interests": []}).get_json()
    assert cleared["interests"] == []


def test_only_real_catalogue_subjects_are_accepted(client):
    # Otherwise this becomes free-text storage, and a chosen interest could
    # never match anything.
    token = register_and_login(client)
    saved = client.post("/api/profile/interests", headers=auth(token),
                        json={"interests": ["Steampunk Zeppelins", "<script>"]}).get_json()
    assert saved["interests"] == []


def test_interests_are_capped(client):
    token = register_and_login(client)
    response = client.post("/api/profile/interests", headers=auth(token),
                           json={"interests": ["a"] * 9})
    assert response.status_code == 400


# ----- marking a browsed book read: the way out of cold start -----

def test_marking_a_browsed_book_read_builds_the_profile(client):
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    record_id = add_catalogue_book()

    assert database.get_taste_profile_books(user["id"]) == []
    result = client.post("/api/catalogue/%d/read" % record_id,
                         headers=auth(token), json={"status": "finished"}).get_json()
    assert result["reading_status"] == "finished"
    assert result["profile_books"] == 1
    assert len(database.get_taste_profile_books(user["id"])) == 1


def test_a_book_marked_read_immediately_answers_a_scan(client):
    # The whole loop: nothing -> mark one book -> the next scan has evidence.
    token = register_and_login(client)
    user = database.get_user_by_email("reader@example.com")
    record_id = add_catalogue_book()
    client.post("/api/catalogue/%d/read" % record_id, headers=auth(token),
                json={"status": "finished"})

    history = [dict(r) for r in database.get_taste_profile_books(user["id"])]
    result = tp.assess("Psychological, Suspense", history)
    assert result["state"] == tp.STATE_MATCH
    assert result["examples"] == ["Gone Girl"]


def test_marking_read_rejects_a_bad_status(client):
    token = register_and_login(client)
    record_id = add_catalogue_book()
    assert client.post("/api/catalogue/%d/read" % record_id, headers=auth(token),
                       json={"status": "devoured"}).status_code == 400


def test_marking_read_requires_signing_in(client):
    record_id = add_catalogue_book()
    assert client.post("/api/catalogue/%d/read" % record_id).status_code in (401, 403)
