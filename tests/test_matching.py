from matching import (rank_candidates, recover_ocr_candidates,
                      HIGH_CONFIDENCE, NEEDS_CONFIRMATION,
                      REJECTED, valid_isbn)


def book(title, author, **extra):
    return {"title": title, "author": author, "page_count": 300,
            "google_books_id": extra.pop("google_books_id", title), **extra}


def test_exact_title_and_author_is_high_confidence():
    result = rank_candidates([book("The Great Gatsby", "F. Scott Fitzgerald")],
                             "The Great Gatsby", "F Scott Fitzgerald")
    assert result["decision"] == HIGH_CONFIDENCE
    assert result["candidates"][0]["score_breakdown"]["title_similarity"] >= 90


def test_wrong_author_is_rejected():
    result = rank_candidates([book("The Hobbit", "Jeff Barton")],
                             "The Hobbit", "J. R. R. Tolkien")
    assert result["decision"] == REJECTED


def test_box_set_cannot_beat_individual_book():
    results = [
        book("Harry Potter Complete Box Set", "J. K. Rowling", google_books_id="set"),
        book("Harry Potter and the Cursed Child", "J. K. Rowling", google_books_id="book"),
    ]
    ranked = rank_candidates(results, "Harry Potter and the Cursed Child", "J K Rowling")
    assert ranked["candidates"][0]["google_books_id"] == "book"
    assert all(c["google_books_id"] != "set" for c in ranked["candidates"])


def test_study_guide_is_rejected():
    result = rank_candidates(
        [book("The Great Gatsby Study Guide and Workbook", "Teaching Press")],
        "The Great Gatsby", "F Scott Fitzgerald")
    assert result["decision"] == REJECTED


def test_title_only_requires_confirmation():
    result = rank_candidates([book("Dune", "Frank Herbert")], "Dune")
    assert result["decision"] == NEEDS_CONFIRMATION


def test_close_candidates_require_confirmation():
    result = rank_candidates([
        book("Dune", "Frank Herbert", google_books_id="a", isbn_13="9780441172719"),
        book("Dune", "Frank Herbert", google_books_id="b", isbn_13="9780593099322"),
    ], "Dune", "Frank Herbert")
    assert result["decision"] == NEEDS_CONFIRMATION


def test_exact_isbn_is_strongest_evidence():
    assert valid_isbn("9780441172719")
    result = rank_candidates([
        book("Dune", "Frank Herbert", isbn_13="9780441172719")
    ], "Dune", "", "9780441172719")
    assert result["decision"] == HIGH_CONFIDENCE
    assert result["candidates"][0]["score"] == 100


def test_similar_title_and_correct_author_is_not_rejected():
    result = rank_candidates([book("The Fellowship of the Ring", "J. R. R. Tolkien")],
                             "Fellowship Ring", "Tolkien")
    assert result["decision"] in {HIGH_CONFIDENCE, NEEDS_CONFIRMATION}


def test_complete_collection_is_rejected():
    result = rank_candidates([
        book("The Complete Sherlock Holmes Collection", "Arthur Conan Doyle")
    ], "A Study in Scarlet", "Arthur Conan Doyle")
    assert result["decision"] == REJECTED


def test_no_acceptable_result_is_rejected():
    result = rank_candidates([book("Cooking for Beginners", "A Chef")],
                             "The Martian", "Andy Weir")
    assert result["decision"] == REJECTED


def test_raw_ocr_recovery_handles_merged_and_scrambled_title_words():
    candidates = [
        book("The Horse and His Boy", "C. S. Lewis", google_books_id="horse"),
        book("The Graveyard Book", "Neil Gaiman", google_books_id="graveyard"),
    ]
    result = recover_ocr_candidates(
        candidates,
        probable_title="C.S.LEWIS ANDHIS BOY THE HORSE",
        probable_author="C-0ONOT",
        full_text="C.S. LEWIS THE HORSE ANDHIS BOY C-0ONOT",
        text_lines=["C.S.LEWIS", "ANDHIS BOY", "THE HORSE", "C-0ONOT"])
    assert result["decision"] == NEEDS_CONFIRMATION
    assert result["candidates"][0]["google_books_id"] == "horse"
    assert result["candidates"][0]["score_breakdown"]["ocr_recovery"] is True


def test_raw_ocr_recovery_ignores_illustrator_credit_and_word_order():
    candidates = [
        book("Coraline", "Neil Gaiman", google_books_id="coraline"),
        book("The Graveyard Book", "Neil Gaiman", google_books_id="graveyard"),
    ]
    result = recover_ocr_candidates(
        candidates,
        probable_title="Graveyard GAIMAN NEIL Book THE",
        probable_author="With illustrations by DAvE MieKEAN",
        full_text=("Graveyard GAIMAN NEIL Book THE "
                   "With illustrations by DAvE MieKEAN"),
        text_lines=["Graveyard", "GAIMAN NEIL", "Book", "THE",
                    "With illustrations by DAvE MieKEAN"])
    assert result["decision"] == NEEDS_CONFIRMATION
    assert result["candidates"][0]["google_books_id"] == "graveyard"


def test_raw_ocr_recovery_rejects_unrelated_cover_text():
    result = recover_ocr_candidates(
        [book("The Martian", "Andy Weir")],
        probable_title="A Culinary Journey",
        probable_author="Jane Cook",
        full_text="Recipes stories and a culinary journey by Jane Cook")
    assert result["decision"] == REJECTED


def test_short_generic_title_requires_author_evidence():
    result = recover_ocr_candidates(
        [book("It", "Stephen King")],
        probable_title="IT",
        full_text="IT A NOVEL NOW A MAJOR MOTION PICTURE")
    assert result["decision"] == REJECTED
