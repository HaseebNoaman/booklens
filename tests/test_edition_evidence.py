"""Identity and page-count provenance are two different facts.

Knowing WHICH copy the reader holds does not tell you what a record's page
count describes. These pin that separation, because collapsing the two is how
the card ends up printing a cross-edition median as "about 9 hours, your copy".
"""
from app import edition_evidence


def google(pages=352, isbn_13="9781250301697"):
    return {"google_books_id": "silent-patient", "isbn_13": isbn_13,
            "page_count": pages}


def open_library(pages=352, isbn_13="9781250301697"):
    # api.py sets page_count from number_of_pages_median and picks isbn_13 as
    # "the first 13-digit ISBN, in no useful order" across ALL the work's
    # editions. Both facts matter to this file.
    return {"open_library_work_id": "/works/OL123W", "isbn_13": isbn_13,
            "page_count": pages}


def catalogue(pages=0):
    return {"catalogue_id": 47, "isbn_13": "9780671801519", "page_count": pages}


# ----- identity -----

def test_scanned_isbn_matching_the_record_confirms_identity():
    assert edition_evidence(google(), "9781250301697")["identity"] == "isbn_confirmed"


def test_a_different_isbn_confirms_nothing():
    assert edition_evidence(google(), "9780141439518")["identity"] == "unconfirmed"


def test_no_scanned_isbn_confirms_nothing():
    # A record carrying an ISBN says nothing about the object in the reader's
    # hands. This is the original defect.
    assert edition_evidence(google(), "")["identity"] == "unconfirmed"


def test_the_frozen_cores_own_answer_is_used_when_given():
    # score_candidate() already decides this; we must not disagree with it.
    result = edition_evidence(google(), "", exact_isbn=True)
    assert result["identity"] == "isbn_confirmed"


def test_isbn_comparison_ignores_formatting():
    assert edition_evidence(google(), "978-1-250-30169-7")["identity"] == "isbn_confirmed"


# ----- page-count provenance -----

def test_google_volume_with_the_readers_isbn_is_edition_exact():
    # parse_book() reads pageCount and industryIdentifiers from the same volume
    # record, so here the page count really does belong to that ISBN.
    assert edition_evidence(google(), "9781250301697")["page_basis"] == "isbn_edition"


def test_google_volume_without_a_scanned_isbn_is_only_a_provider_edition():
    assert edition_evidence(google(), "")["page_basis"] == "google_volume"


def test_open_library_stays_a_median_even_when_the_isbn_matches():
    # THE REGRESSION THIS FILE EXISTS FOR.
    #
    # Open Library's isbn_13 is an arbitrary ISBN from across the work's
    # editions, and its page_count is number_of_pages_median. So the reader's
    # ISBN can match while the page count is still an average of other
    # printings. Identity is confirmed; the page count is NOT exact.
    result = edition_evidence(open_library(), "9781250301697")
    assert result["identity"] == "isbn_confirmed"
    assert result["page_basis"] == "ol_work_median"
    assert result["page_basis"] != "isbn_edition"


def test_open_library_without_a_scanned_isbn_is_also_a_median():
    assert edition_evidence(open_library(), "")["page_basis"] == "ol_work_median"


def test_catalogue_rows_are_never_called_a_median():
    # catalogue_books has no page-count column, so a Tier-1 row supplies
    # nothing here. Labelling it "median across editions" would invent a
    # provenance it never had.
    assert edition_evidence(catalogue(), "")["page_basis"] == "unknown"
    assert edition_evidence(catalogue(pages=300), "")["page_basis"] == "catalogue_record"


def test_a_missing_page_count_has_no_basis_to_report():
    assert edition_evidence(google(pages=0), "9781250301697")["page_basis"] == "unknown"


def test_the_four_provenances_stay_distinct():
    # Each source keeps its own label; none collapses into another.
    seen = {
        edition_evidence(google(), "9781250301697")["page_basis"],
        edition_evidence(google(), "")["page_basis"],
        edition_evidence(open_library(), "")["page_basis"],
        edition_evidence(catalogue(pages=300), "")["page_basis"],
    }
    assert seen == {"isbn_edition", "google_volume", "ol_work_median",
                    "catalogue_record"}


# ----- the rule the card applies -----

def reading_time_shown(evidence, pages):
    """Mirror of the card's condition in ResultViews.jsx."""
    return (evidence["identity"] == "isbn_confirmed"
            and evidence["page_basis"] == "isbn_edition" and pages > 0)


def test_reading_time_needs_both_facts():
    assert reading_time_shown(edition_evidence(google(), "9781250301697"), 352)
    # Identity alone is not enough when the number is a median.
    assert not reading_time_shown(edition_evidence(open_library(), "9781250301697"), 352)
    # A specific volume alone is not enough without the reader's ISBN.
    assert not reading_time_shown(edition_evidence(google(), ""), 352)


def test_no_page_count_threshold_survives_anywhere():
    # The old rule hid reading time below 80 pages. A confirmed 60-page edition
    # is a real 60-page edition and reports its time.
    short = edition_evidence(google(pages=60), "9781250301697")
    assert reading_time_shown(short, 60)
