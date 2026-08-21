"""The same rarity score, run backwards, over our own 250 books.

Identification refusing is the product working, but it leaves the reader
holding nothing. These cover what the app is allowed to say instead -- and,
more importantly, the two things it must not do: dress 250 books up as
recommendations, or make a claim the reader cannot check.
"""
import database
import taste_profile as tp

from test_api_flows import auth, client, register_and_login  # noqa: F401
from test_cold_start import add_catalogue_book, seed_shelf, SHELF  # noqa: F401


def read(*rows):
    return [{"title": title, "categories": genres,
             "reading_status": "finished", "is_favorite": 0}
            for title, genres in rows]


def catalogue():
    return [dict(r) for r in database.list_catalogue("VERIFIED")]


def closest(history, limit=4):
    counts, size = database.catalogue_subject_counts()
    return tp.closest_from_shelf(history, catalogue(), limit=limit,
                                 subject_counts=counts, catalogue_size=size)


def titles(picked):
    return [item["book"]["title"] for item in picked]


# ----- what earns a place -----

def test_it_answers_from_a_single_book(client):
    seed_shelf()
    picked = closest(read(("The Time Machine", "Time travel; Speculative")))
    assert "Kindred" in titles(picked)          # the other time-travel book


def test_a_shelf_wide_label_is_not_a_reason(client):
    """The rule that runs through the whole feature: "speculative" is on half
    the catalogue, so having read one of those says nothing about the rest."""
    seed_shelf()
    picked = closest(read(("Some Book", "Speculative")))
    assert picked == []


def test_a_book_the_reader_has_read_is_never_offered_back(client):
    seed_shelf()
    picked = closest(read(("The Time Machine", "Time travel; Speculative"),
                          ("Kindred", "Time travel; Speculative")))
    assert "Kindred" not in titles(picked)
    assert "The Time Machine" not in titles(picked)


def test_a_scan_is_not_a_reason_to_suggest_anything(client):
    """Only deliberate signals build the profile. A cover the camera happened
    to identify is not a statement about taste, and the backwards score must
    honour that too or it recommends from whatever was photographed."""
    scanned = [{"title": "The Time Machine", "categories": "Time travel",
                "reading_status": "identified", "is_favorite": 0}]
    seed_shelf()
    assert closest(scanned) == []


# ----- the reason travels with the book -----

def test_every_book_names_the_reason_and_the_reader_s_own_titles(client):
    """A suggestion the reader cannot check is exactly the kind of claim this
    product does not make. "Because you read The Time Machine" is auditable;
    a score out of five is not."""
    seed_shelf()
    picked = closest(read(("The Time Machine", "Time travel; Speculative")))
    assert picked
    for item in picked:
        assert item["reason"]
        assert item["because"] == ["The Time Machine"]
        # and the reason really is shared with the book being offered
        assert item["reason"].lower() in \
            {s for s in tp.normalize_subjects(item["book"]["genres"])}


def test_the_reason_backed_by_more_of_the_reader_s_books_leads(client):
    """Measured on the real catalogue: ranking by rarity alone put "Mockingjay,
    because adventure novel" above "A Feast for Crows, because fantasy" for a
    reader of The Hobbit and A Game of Thrones. Rarer, and plainly worse."""
    seed_shelf()
    add_catalogue_book("Another Horror", "Horror")
    picked = closest(read(("Carrie", "Horror; Speculative"),
                          ("Pet Sematary", "Horror; Speculative"),
                          ("The Time Machine", "Time travel; Speculative")))
    # horror is backed by two of the reader's books, time travel by one
    assert picked[0]["reason"] == "Horror"


def test_no_single_reason_fills_the_whole_list(client):
    seed_shelf()
    for i in range(6):
        add_catalogue_book("Horror %d" % i, "Horror")
    picked = closest(read(("Carrie", "Horror; Speculative"),
                          ("The Time Machine", "Time travel; Speculative")))
    reasons = [item["reason"] for item in picked]
    assert reasons.count("Horror") <= tp.CLOSEST_PER_REASON


def test_a_record_claiming_everything_does_not_win(client):
    """The hub. A catalogue record listing seven genres matches half the shelf,
    which is what put House of Leaves -- a horror novel filed under "Romance
    novel" -- at the top of a Jane Austen reader's list. Dividing by the
    candidate's own subject count is what removed it."""
    seed_shelf()
    # One subject only these two carry, so the ranking between them is the
    # only thing under test -- not the per-reason cap above it.
    add_catalogue_book("Everything At Once",
                       "Kraken; Romance; Comedy; Gothic; True crime; Horror")
    add_catalogue_book("Only A Kraken Book", "Kraken")
    picked = closest(read(("Sea Tales", "Kraken")))
    assert titles(picked) == ["Only A Kraken Book", "Everything At Once"]


# ----- through the route -----

def test_the_route_answers_with_the_reason_attached(client):
    token = register_and_login(client)
    ids = seed_shelf()
    client.post("/api/catalogue/%d/read" % ids["The Time Machine"],
                headers=auth(token), json={"status": "finished"})

    answer = client.get("/api/closest", headers=auth(token)).get_json()
    assert answer["profile_books"] == 1
    assert answer["books"]
    first = answer["books"][0]
    assert first["reason"] and first["because"] == ["The Time Machine"]
    assert "The Time Machine" not in [b["title"] for b in answer["books"]]


def test_a_reader_with_no_library_is_told_nothing_rather_than_anything(client):
    """An empty panel headed "closest to what you have read" is worse than no
    panel. The component renders nothing when this list is empty."""
    token = register_and_login(client)
    seed_shelf()
    assert client.get("/api/closest", headers=auth(token)).get_json()["books"] == []


def test_one_reader_s_shelf_never_leaks_into_another_s(client):
    """Not collaborative filtering, and it must not quietly become it: 10
    users, 23 history rows, no ratings table."""
    ids = seed_shelf()
    mine = register_and_login(client)
    client.post("/api/catalogue/%d/read" % ids["Carrie"], headers=auth(mine),
                json={"status": "finished"})

    stranger = register_and_login(client, email="stranger@example.com")
    answer = client.get("/api/closest", headers=auth(stranger)).get_json()
    assert answer["books"] == []
    assert answer["profile_books"] == 0


def test_the_route_requires_signing_in(client):
    seed_shelf()
    assert client.get("/api/closest").status_code in (401, 403)


# ----- the same order, applied to Browse -----

def test_browse_opens_nearest_to_the_reader_first(client):
    """Browse opened in updated_at order -- the order the review pipeline
    happened to touch the records in, which is nothing to anybody."""
    token = register_and_login(client)
    ids = seed_shelf()
    client.post("/api/catalogue/%d/read" % ids["Carrie"], headers=auth(token),
                json={"status": "finished"})

    listed = client.get("/api/catalogue", headers=auth(token)).get_json()["books"]
    assert listed[0]["title"] == "Pet Sematary"      # the other horror book


def test_a_search_is_never_reordered_by_taste(client):
    """Someone typing "Emma" wants Emma, not something like it."""
    token = register_and_login(client)
    ids = seed_shelf()
    client.post("/api/catalogue/%d/read" % ids["Carrie"], headers=auth(token),
                json={"status": "finished"})

    found = client.get("/api/catalogue?q=Emma", headers=auth(token)).get_json()
    assert [b["title"] for b in found["books"]] == ["Emma"]


def test_nothing_is_dropped_from_the_shelf_by_reordering(client):
    token = register_and_login(client)
    ids = seed_shelf()
    client.post("/api/catalogue/%d/read" % ids["Carrie"], headers=auth(token),
                json={"status": "finished"})

    listed = client.get("/api/catalogue", headers=auth(token)).get_json()
    assert listed["total"] == len(SHELF)
    assert len(listed["books"]) == len(SHELF)
