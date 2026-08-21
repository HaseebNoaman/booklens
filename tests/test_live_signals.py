"""What Open Library says today, and the guard that keeps it honest.

The whole value of a rating on the card is that the number can be trusted. Open
Library's search always answers — it ranks whatever it has and returns a first
result even for a query it does not recognise — so the dangerous failure is not
"no rating", it is "a confident rating belonging to a different book".
"""
import livesignals as ls


# ----- the guard -----

def test_a_sequel_is_not_the_book_we_asked_about():
    """The exact trap: token_set_ratio scores "Dune" against "Dune Messiah" at
    100, because one title's words are a subset of the other. Sorting compares
    the whole strings, so the extra meaningful word counts against it."""
    assert not ls.titles_agree("Dune", "Dune Messiah")
    assert not ls.titles_agree("The Hobbit", "The Hobbit Strategy Guide")


def test_a_subtitle_or_imprint_is_still_the_same_book():
    assert ls.titles_agree("Life of Pi", "Life of Pi: A Novel")
    assert ls.titles_agree("The Great Gatsby", "The Great Gatsby (Penguin Classics)")
    assert ls.titles_agree("1984", "1984")


def test_an_appended_author_name_is_still_the_same_book():
    """Open Library files Becoming as "Becoming Michelle Obama". Appending the
    author does not make it a different book. Measured: this was 1 of only 2
    rejections across 70 books, and the fix cannot reopen the sequel hole,
    because "Messiah" is not Frank Herbert's name."""
    assert ls.titles_agree("Becoming", "Becoming Michelle Obama", "Michelle Obama")
    assert ls.titles_agree("Brave New World", "Brave new world /by Aldous Huxley",
                           "Aldous Huxley")
    assert not ls.titles_agree("Dune", "Dune Messiah", "Frank Herbert")


def test_a_biography_of_the_author_is_not_the_book():
    """The failure the 100-cover run surfaced three times: The Shining came
    back as "Stephen King" by Bev Vincent, because the largest text on the
    cover is the author's name."""
    assert not ls.titles_agree("The Shining", "Stephen King", "Stephen King")


def test_another_language_s_edition_is_rejected():
    """Its rating belongs to a different readership, so it is not this book's
    rating even though it is this book's story."""
    assert not ls.titles_agree("The Alchemist", "O Alquimista", "Paulo Coelho")


def test_an_unrelated_book_is_rejected():
    assert not ls.titles_agree("It", "The Shining")
    assert not ls.titles_agree("", "Anything")


def test_a_placeholder_title_cannot_acquire_a_rating(monkeypatch):
    """A book we know almost nothing about must not inherit a real book's
    numbers just because Open Library was willing to guess."""
    monkeypatch.setattr(ls, "_fetch", lambda title, author="": {
        "title": "The Great Gatsby", "ratings_average": 3.97,
        "ratings_count": 243, "readinglog_count": 3291,
        "number_of_pages_median": 185})
    assert ls.fetch_live_signals("Untitled Document", "") is None


# ----- what comes back -----

def test_the_signals_are_read_off_the_document(monkeypatch):
    monkeypatch.setattr(ls, "_fetch", lambda title, author="": {
        "title": "Life of Pi", "ratings_average": 3.9634,
        "ratings_count": 151, "readinglog_count": 1113,
        "want_to_read_count": 900, "number_of_pages_median": 349})
    signals = ls.fetch_live_signals("Life of Pi", "Yann Martel")
    assert signals["rating"] == 3.96
    assert signals["n_ratings"] == 151
    assert signals["on_shelves"] == 1113
    assert signals["page_count"] == 349


def test_an_absurd_page_count_is_dropped(monkeypatch):
    """A 7-page record is an audiobook stub or a box-set entry, not a book, and
    it is how "about 1.5 hours" came to be printed under a 350-page novel."""
    for pages in (7, 12, 39, 2001, 5000):
        monkeypatch.setattr(ls, "_fetch", lambda title, author="", p=pages: {
            "title": "Life of Pi", "number_of_pages_median": p})
        assert ls.fetch_live_signals("Life of Pi")["page_count"] == 0


def test_a_provider_outage_costs_the_reader_nothing(monkeypatch):
    def explode(title, author=""):
        raise RuntimeError("Open Library is down")
    monkeypatch.setattr(ls, "_fetch", explode)
    assert ls.fetch_live_signals("Anything") is None


def test_nothing_known_is_not_an_error(monkeypatch):
    monkeypatch.setattr(ls, "_fetch", lambda title, author="": None)
    assert ls.fetch_live_signals("A Book Published Yesterday") is None


# ----- what the card is allowed to see -----

def test_quality_and_demand_stay_separate():
    """A book everyone means to read is not a book everyone enjoyed. The two
    are separate fields so a template cannot blur them by accident."""
    payload = ls.for_client({"rating": 4.1, "n_ratings": 140,
                             "on_shelves": 2890, "page_count": 370,
                             "fetched_at": 1, "age_seconds": 3600})
    assert payload["rating"] == 4.1
    assert payload["n_ratings"] == 140
    assert payload["on_shelves"] == 2890
    assert "score" not in payload and "popularity" not in payload


def test_a_thin_rating_is_flagged_not_hidden():
    payload = ls.for_client({"rating": 5.0, "n_ratings": 2,
                             "rating_is_thin": True, "fetched_at": 1,
                             "age_seconds": 10})
    assert payload["rating"] == 5.0
    assert payload["rating_is_thin"] is True


def test_freshness_is_described_not_asserted():
    assert ls.freshness_phrase({"fetched_at": 1, "age_seconds": 3600}) == "checked today"
    assert ls.freshness_phrase({"fetched_at": 1, "age_seconds": 3 * 86400}) == "checked 3 days ago"
    assert ls.freshness_phrase(None) is None
    assert ls.freshness_phrase({}) is None
