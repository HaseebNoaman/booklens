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


# ----- the starter shelf: the way out that does not leave the card -----
#
# The cold-start copy used to sit above a link to #browse, which navigated away
# from the result the reader had just photographed. These cover the panel that
# replaced it, and the rule underneath it: the shelf chooses which QUESTION to
# ask, never what the answer is.

SHELF = [
    ("The Time Machine", "Time travel; Speculative"),
    ("Kindred", "Time travel; Speculative"),
    ("Hyperion", "Space opera; Speculative"),
    ("Dune", "Space opera; Speculative"),
    ("Carrie", "Horror; Speculative"),
    ("Pet Sematary", "Horror; Speculative"),
    ("Emma", "Romance"),
    ("Persuasion", "Romance"),
    ("Gone Girl", "Psychological"),
    ("Sharp Objects", "Psychological"),
    ("In Cold Blood", "True crime"),
    ("Rebecca", "Gothic"),
]


def seed_shelf():
    """Twelve books, and the proportions are the point.

    "Speculative" is on 6 of the 12 -- past COMMON_SUBJECT_SHARE, so it can
    describe a book but never be evidence about a reader. Everything else is on
    one or two. A three-book fixture cannot test any of this: with so few rows
    every subject looks shelf-wide, and the panel would correctly refuse to
    offer anything.
    """
    return {title: add_catalogue_book(title, genres) for title, genres in SHELF}


def ask(client, token, **body):
    body.setdefault("title", "")
    return client.post("/api/for-you", headers=auth(token), json=body).get_json()


def subjects_of(book):
    return tp.normalize_subjects(book["categories"])


# ----- which books get offered -----

def test_every_offered_book_shares_a_distinguishing_subject(client):
    token = register_and_login(client)
    seed_shelf()
    answer = ask(client, token, title="11/22/63",
                 categories="Time travel; Speculative")

    assert answer["for_you"]["state"] == tp.STATE_COLD_START
    assert answer["targeted"] is True
    assert answer["starters"], "a cold-start card with no way out is the bug"
    for book in answer["starters"]:
        # Not "speculative" -- that one is on half the shelf, so tapping it
        # could never turn this section into a real answer.
        assert "time travel" in subjects_of(book), book["title"]


def test_the_scanned_book_is_never_offered_back(client):
    """Matched on title, not id: the same work legitimately exists as more than
    one row, and offering the reader the book in their hand looks broken."""
    token = register_and_login(client)
    seed_shelf()
    answer = ask(client, token, title="Kindred",
                 categories="Time travel; Speculative")
    titles = [b["title"] for b in answer["starters"]]
    assert "Kindred" in [t for t, _ in SHELF]      # it IS on the shelf
    assert "Kindred" not in titles                  # and still not offered


def test_a_book_the_reader_already_marked_is_not_offered(client):
    token = register_and_login(client)
    ids = seed_shelf()
    client.post("/api/catalogue/%d/read" % ids["The Time Machine"],
                headers=auth(token), json={"status": "finished"})
    answer = ask(client, token, title="11/22/63",
                 categories="Time travel; Speculative")
    titles = [b["title"] for b in answer["starters"]]
    assert "The Time Machine" not in titles


def test_the_six_do_not_all_share_one_reason(client):
    """Without the round robin the shelf is six books answering the same
    question -- and a reader who has read none of them is stuck."""
    token = register_and_login(client)
    seed_shelf()
    answer = ask(client, token, title="A Wide Book",
                 categories="Time travel; Horror; Romance")
    reasons = [tuple(sorted(subjects_of(b))) for b in answer["starters"]]
    assert len(set(reasons)) >= 3


# ----- the honest edges -----

def test_a_book_tagged_only_with_shelf_wide_labels_says_so(client):
    """18% of the real catalogue is like this. The shelf still appears, because
    the profile outlives the scan -- but targeted=False tells the card not to
    promise it will answer THIS book."""
    token = register_and_login(client)
    seed_shelf()
    answer = ask(client, token, title="Some Novel", categories="Speculative")
    assert answer["targeted"] is False
    assert len(answer["starters"]) == 6


def test_a_book_with_no_subjects_gets_no_shelf_at_all(client):
    """The publisher's gap, not the reader's. No amount of reading history
    fixes it, so offering books to tap would promise something untrue."""
    token = register_and_login(client)
    seed_shelf()
    answer = ask(client, token, title="Unlabelled", categories="")
    assert answer["for_you"]["state"] == tp.STATE_NO_SUBJECTS
    assert answer["starters"] == []


def test_a_reader_with_a_library_is_not_offered_a_starter_shelf(client):
    token = register_and_login(client)
    ids = seed_shelf()
    client.post("/api/catalogue/%d/read" % ids["The Time Machine"],
                headers=auth(token), json={"status": "finished"})
    answer = ask(client, token, title="11/22/63",
                 categories="Time travel; Speculative")
    assert answer["for_you"]["state"] == tp.STATE_MATCH
    assert answer["starters"] == []


def test_the_next_six_are_different_books(client):
    """The escape hatch has to actually move: a reader who has read none of
    the first six gets six others, not the same grid again."""
    token = register_and_login(client)
    seed_shelf()
    first = ask(client, token, title="Some Novel", categories="Speculative")
    shown = [b["id"] for b in first["starters"]]
    second = ask(client, token, title="Some Novel", categories="Speculative",
                 exclude_ids=shown)
    assert second["starters"]
    assert not set(b["id"] for b in second["starters"]) & set(shown)


# ----- the loop the panel exists for -----

def test_one_tap_turns_cold_start_into_a_real_answer(client):
    """Measured before this was built: one tap does this for 196 of the 238
    catalogue books that carry subjects. MIN_PROFILE_BOOKS is 1 for a reason --
    a panel demanding three would contradict the module it feeds."""
    token = register_and_login(client)
    seed_shelf()
    before = ask(client, token, title="11/22/63",
                 categories="Time travel; Speculative")
    assert before["for_you"]["state"] == tp.STATE_COLD_START

    tapped = before["starters"][0]
    client.post("/api/catalogue/%d/read" % tapped["id"], headers=auth(token),
                json={"status": "finished"})

    after = ask(client, token, title="11/22/63",
                categories="Time travel; Speculative")
    assert after["for_you"]["state"] == tp.STATE_MATCH
    assert after["for_you"]["subjects"] == ["Time Travel"]
    assert after["for_you"]["examples"] == [tapped["title"]]
    assert after["starters"] == []


def test_a_mistaken_tap_can_be_taken_back(client):
    """The shelf writes to the reader's real library from a single tap, so it
    has to be undoable -- with the history route that already exists."""
    token = register_and_login(client)
    seed_shelf()
    answer = ask(client, token, title="11/22/63",
                 categories="Time travel; Speculative")
    tapped = answer["starters"][0]
    marked = client.post("/api/catalogue/%d/read" % tapped["id"],
                         headers=auth(token),
                         json={"status": "finished"}).get_json()

    removed = client.delete("/api/history/%d" % marked["history_id"],
                            headers=auth(token))
    assert removed.status_code == 200

    after = ask(client, token, title="11/22/63",
                categories="Time travel; Speculative")
    assert after["for_you"]["state"] == tp.STATE_COLD_START
    assert after["starters"]


def test_the_shelf_requires_signing_in(client):
    seed_shelf()
    assert client.post("/api/for-you", json={"title": "x"}).status_code in (401, 403)


# ----- the census the weighting reads -----

def test_adding_a_catalogue_book_changes_the_weighting_immediately(client):
    """The census is cached for the life of the process, and nothing dropped
    the cache when the catalogue changed. An admin could add ten books and
    every card would keep weighting subjects against the shelf as it was at
    boot."""
    seed_shelf()
    counts, total = database.catalogue_subject_counts()
    assert counts["gothic"] == 1 and total == 12
    add_catalogue_book("Another Gothic", "Gothic")
    counts, total = database.catalogue_subject_counts()
    assert counts["gothic"] == 2 and total == 13
