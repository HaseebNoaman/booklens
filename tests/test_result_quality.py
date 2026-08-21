"""Three defects found by photographing 100 real covers, and their fixes.

Each of these was measured before it was written, and the number that justifies
it is in the comment. None of them is a style preference.
"""
import app as app_module
from matching import UNSAFE_EDITION_RE, rank_candidates, REJECTED


def candidate(title, author, score=70.0, **extra):
    row = {"title": title, "author": author, "score": score, "reasons": []}
    row.update(extra)
    return row


# ----- the chooser used to repeat the same book -----
# Measured: 34 of 100 choosers repeated a title. The worst offered Rich Dad
# Poor Dad five times, because Google gives every printing its own volume id.

def test_the_same_book_is_offered_once():
    shown = app_module.collapse_duplicate_editions([
        candidate("Rich Dad, Poor Dad", "Robert T. Kiyosaki", 93.0,
                  google_books_id="a"),
        candidate("Rich Dad Poor Dad", "Robert T. Kiyosaki", 91.0,
                  google_books_id="b"),
        candidate("Rich Dad Poor Dad", "Robert T. Kiyosaki", 88.0,
                  google_books_id="c"),
        candidate("Cashflow Quadrant", "Robert T. Kiyosaki", 80.0,
                  google_books_id="d"),
    ])
    assert [c["title"] for c in shown] == ["Rich Dad, Poor Dad", "Cashflow Quadrant"]


def test_the_best_scoring_edition_is_the_one_kept():
    shown = app_module.collapse_duplicate_editions([
        candidate("Dune", "Frank Herbert", 95.0, google_books_id="a"),
        candidate("Dune", "Frank Herbert", 70.0, google_books_id="b"),
    ])
    assert len(shown) == 1
    assert shown[0]["score"] == 95.0


def test_different_books_are_never_merged():
    shown = app_module.collapse_duplicate_editions([
        candidate("The Shining", "Stephen King", 90.0),
        candidate("The Stand", "Stephen King", 85.0),
    ])
    assert len(shown) == 2


def test_one_person_spelled_three_ways_is_still_one_person():
    """Providers disagree about initials, so an exact author key left 15 of the
    100 choosers still repeating a title."""
    shown = app_module.collapse_duplicate_editions([
        candidate("The Great Gatsby", "F. Scott Fitzgerald", 95.0),
        candidate("The Great Gatsby", "F Scott Fitzgerald", 90.0),
        candidate("The Great Gatsby", "Francis Scott Fitzgerald", 85.0),
    ])
    assert len(shown) == 1


def test_a_shared_title_by_a_different_author_is_a_different_book():
    """The reason the author has to agree at all.

    "The Hobbit" names both Tolkien's novel and a video-game strategy guide,
    and "Stephen King" is both an author and the title of a book about him.
    Collapsing on title alone would hide one of each pair.
    """
    hobbit = app_module.collapse_duplicate_editions([
        candidate("The Hobbit", "J.R.R. Tolkien", 95.0),
        candidate("The Hobbit", "Prima Games", 80.0),
    ])
    assert len(hobbit) == 2

    king = app_module.collapse_duplicate_editions([
        candidate("Stephen King", "Bev Vincent", 90.0),
        candidate("Stephen King", "Stephen King", 85.0),
    ])
    assert len(king) == 2


# ----- a junk author was worse than no author -----
# The author guess is the first text block smaller than the title, which on a
# cover reading "The ALCHEMIST ... PAULO COELHO" is the word "The". Measured:
# 18 of 100 covers produced an author like this.

def test_cover_words_that_are_not_names_are_dropped():
    for junk in ("The", "THE", "and", "a", "T", "s", "ur o", "NEW", "FOR",
                 "31867 00082 4651", ""):
        assert app_module.usable_ocr_author(junk) == "", junk


def test_real_names_survive():
    for name in ("Paulo Coelho", "J. K. Rowling", "Andy Weir", "bell hooks",
                 "J.R.R. Tolkien"):
        assert app_module.usable_ocr_author(name) == name


def test_a_junk_author_would_otherwise_throw_away_the_right_book():
    """The reason this matters, stated as a test rather than a comment.

    rank_candidates rejects outright when an author is supplied and scores
    under 35, so "The" does not merely fail to help -- it removes Coelho's
    novel from the results entirely, and the reader gets a cocktail recipe
    book instead.
    """
    book = {"title": "The Alchemist", "author": "Paulo Coelho",
            "isbn_13": "9780061122415", "google_books_id": "g1",
            "page_count": 197}

    with_junk = rank_candidates([dict(book)], "ALCHEMIST", "The", "")
    assert with_junk["decision"] == REJECTED

    cleaned = rank_candidates([dict(book)], "ALCHEMIST",
                              app_module.usable_ocr_author("The"), "")
    assert cleaned["decision"] != REJECTED
    assert cleaned["candidates"][0]["title"] == "The Alchemist"


# ----- study guides were being shown as the novel -----
# The worst error the product can make: the reader is told it IS the book.

def test_derived_products_are_recognised():
    for title in ("Pride and Prejudice [adaptation]",
                  "The 7 Habits of Highly Effective People in 30 Minutes",
                  "The Great Gatsby: the Expert Guide",
                  "SparkNotes The Great Gatsby",
                  "Bloom's Modern Critical Interpretations",
                  "A Study Guide for Beloved"):
        assert UNSAFE_EDITION_RE.search(title), title


def test_a_summary_is_dropped_when_the_real_book_is_also_on_offer():
    """score_candidate rejects these, but rank_candidates is not the only route
    to the screen: when it rejects everything, retrieve_ranked_candidates falls
    back to recover_ocr_candidates, which applies no derived-edition gate. That
    is how "Summary: Atomic Habits by James Clear" reached a chooser sitting
    next to the real Atomic Habits."""
    shown = app_module.drop_derived_products([
        candidate("Atomic Habits", "James Clear", 95.0),
        candidate("Summary: Atomic Habits by James Clear", "Dean's Library", 70.0),
    ])
    assert [c["title"] for c in shown] == ["Atomic Habits"]


def test_an_offer_made_only_of_guides_is_not_an_answer():
    """Found by running the fix, not by reasoning about it.

    Verity's cover was read as "HOOVER COLLEEN" -- the author's name, with the
    title never read at all -- and the single candidate that came back was a
    Colleen Hoover ebook bundle. Before, a junk author had rejected it and the
    reader got an honest refusal; cleaning the author let the bundle through.
    A chooser containing nothing but derived editions is a wrong answer wearing
    a chooser, so the funnel asks for an empty list and refuses instead.
    """
    only_guides = [
        candidate("Summary: Atomic Habits", "X", 70.0),
        candidate("Colleen Hoover: A 3 Ebook Collection", "Colleen Hoover", 65.0),
    ]
    assert app_module.drop_derived_products(
        only_guides, keep_when_empty=False) == []


def test_the_two_derived_edition_lists_agree_with_each_other():
    """DERIVED_EDITION_RE already listed "ebook collection"; UNSAFE_EDITION_RE
    did not, and \\bbooks?\\b cannot match inside "Ebook". That one-word gap is
    what put a Colleen Hoover bundle in front of a reader holding Verity."""
    assert UNSAFE_EDITION_RE.search("Colleen Hoover: A 3 Ebook Collection")


def test_real_novels_are_not_mistaken_for_derived_products():
    """Every pattern was checked against all 250 catalogue titles and all 100
    benchmark titles before being added; these are the ones most at risk."""
    for title in ("Pride and Prejudice", "The Road", "The Alchemist",
                  "Bridget Jones's Diary", "Brave new world /by Aldous Huxley",
                  "The Alchemist Cocktail Book", "Sophie's Choice",
                  "A Brief History of Time"):
        assert not UNSAFE_EDITION_RE.search(title), title
