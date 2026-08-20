"""Is this for you? — the evidence rules.

The claims this section makes appear on screen in front of an examiner, so each
one is pinned here: what counts as a signal, what the count means, and which of
the four states appears when.
"""
import pytest

from taste_profile import (
    MIN_PROFILE_BOOKS,
    STATE_COLD_START,
    STATE_MATCH,
    STATE_NO_MATCH,
    STATE_NO_SUBJECTS,
    assess,
    build_profile,
    is_profile_signal,
    normalize_subjects,
)


def row(title, categories, favorite=0, status="finished"):
    return {"title": title, "categories": categories,
            "is_favorite": favorite, "reading_status": status}


# ----- what counts as a signal -----

def test_a_bare_scan_is_not_taste():
    # The circularity guard. Every identified cover writes a history row, so if
    # "identified" counted, the profile would describe the camera, not the user.
    assert not is_profile_signal(row("X", "Thrillers", status="identified"))


def test_wanting_to_read_is_an_intention_not_an_experience():
    assert not is_profile_signal(row("X", "Thrillers", status="want_to_read"))


@pytest.mark.parametrize("favorite,status", [
    (1, "identified"),      # favourited without being read is still deliberate
    (0, "finished"),
    (0, "reading"),
])
def test_deliberate_signals_count(favorite, status):
    assert is_profile_signal(row("X", "Thrillers", favorite, status))


# ----- subject normalisation -----

def test_provider_shapes_become_comparable():
    # Google uses slashes, Open Library uses commas. They must meet.
    google = normalize_subjects("Fiction / Thrillers / Psychological")
    openlib = normalize_subjects("Psychological fiction, Thrillers, Murder")
    assert "thrillers" in google and "thrillers" in openlib
    assert "psychological" in google & openlib


def test_the_local_catalogue_uses_semicolons():
    # catalogue_books.genres is semicolon-joined and is copied straight into
    # books.categories. Missing this separator would collapse the whole field
    # into one label and break the feature on the offline Tier-1 path -- the
    # one demonstrated with the network switched off.
    subjects = normalize_subjects("Speculative fiction; Fantasy; Horror")
    assert subjects == {"speculative", "fantasy", "horror"}


def test_all_three_provider_shapes_agree():
    google = normalize_subjects("Fiction / Fantasy / Epic")
    openlib = normalize_subjects("Fantasy fiction, Epic, Quests")
    catalogue = normalize_subjects("Speculative fiction; Fantasy; Epic")
    assert "fantasy" in google & openlib & catalogue
    assert "epic" in google & openlib & catalogue


def test_uninformative_subjects_are_dropped():
    # Almost every novel is "Fiction"; matching on it would match everything.
    assert normalize_subjects("Fiction, General, Literature") == set()


def test_subject_field_may_be_missing():
    for empty in (None, "", [], "   "):
        assert normalize_subjects(empty) == set()


# ----- the four states -----

def test_book_without_subjects_is_not_the_users_fault():
    # ~25% of books arrive with no usable subject. Telling a well-read user to
    # "build your reading profile" because the PUBLISHER omitted subjects would
    # be wrong, so the book check comes first.
    history = [row("A", "Thrillers"), row("B", "Thrillers"), row("C", "Crime")]
    result = assess("Fiction, General", history)
    assert result["state"] == STATE_NO_SUBJECTS


def test_new_account_gets_the_cold_start_state():
    result = assess("Thrillers, Crime", [])
    assert result["state"] == STATE_COLD_START
    assert result["book_count"] == 0


def test_a_thin_profile_still_states_only_what_is_true():
    # This used to assert that two books were "too thin" and returned cold
    # start, on the reasoning that a small profile is noise. That objection
    # applies to PREDICTING enjoyment; this module reports a checkable fact
    # instead, so the threshold moved to one and the honesty guarantee moved
    # here: whatever the profile size, the count must equal the books actually
    # named, and nothing may be implied beyond them.
    history = [row("A", "Thrillers"), row("B", "Thrillers")]
    result = assess("Thrillers", history)
    assert result["state"] == STATE_MATCH
    assert result["book_count"] == 2
    assert set(result["examples"]) == {"A", "B"}
    assert MIN_PROFILE_BOOKS == 1


def test_an_empty_profile_is_still_cold_start():
    assert assess("Thrillers", [])["state"] == STATE_COLD_START


def test_overlap_reports_books_and_names_them():
    history = [
        row("Gone Girl", "Thrillers, Psychological"),
        row("Sharp Objects", "Thrillers, Crime"),
        row("Before I Go to Sleep", "Psychological, Thrillers"),
    ]
    result = assess("Psychological, Thrillers, Crime", history)
    assert result["state"] == STATE_MATCH
    assert result["book_count"] == 3
    assert set(result["examples"]) == {"Gone Girl", "Sharp Objects",
                                       "Before I Go to Sleep"}


def test_the_count_is_books_not_subject_hits():
    # One book matching three subjects is ONE book. An examiner will count the
    # titles on screen, and the number beside them has to agree.
    history = [
        row("Gone Girl", "Thrillers, Psychological, Crime"),
        row("Dune", "Science, Space"),
        row("Neuromancer", "Science, Cyberpunk"),
    ]
    result = assess("Thrillers, Psychological, Crime", history)
    assert result["state"] == STATE_MATCH
    assert result["book_count"] == 1
    assert result["examples"] == ["Gone Girl"]


def test_no_overlap_is_reported_honestly():
    history = [row("Dune", "Science"), row("Neuromancer", "Cyberpunk"),
               row("Snow Crash", "Cyberpunk")]
    result = assess("Romance, Regency", history)
    assert result["state"] == STATE_NO_MATCH
    assert result["book_count"] == 3      # the profile exists, it just misses


def test_the_card_never_shows_more_than_three_of_anything():
    history = [row("B%d" % i, "Thrillers, Crime, Psychological, Mystery, Horror")
               for i in range(8)]
    result = assess("Thrillers, Crime, Psychological, Mystery, Horror", history)
    assert len(result["subjects"]) <= 3
    assert len(result["examples"]) <= 3


def test_no_percentage_is_ever_produced():
    # Evidence, never a verdict. If a score appears here, the claim has
    # outgrown the method.
    history = [row("A", "Thrillers"), row("B", "Thrillers"), row("C", "Crime")]
    result = assess("Thrillers", history)
    assert not any("score" in k or "percent" in k or "match_pct" in k
                   for k in result)


def test_duplicate_scans_do_not_inflate_the_profile():
    # Defence in depth: the SQL groups by book id, and the count here is of
    # distinct titles, so a book scanned twice cannot be counted twice.
    history = [row("Gone Girl", "Thrillers"), row("Gone Girl", "Thrillers"),
               row("Sharp Objects", "Thrillers"), row("Dune", "Science")]
    assert build_profile(history)["subjects"]["thrillers"] == [
        "Gone Girl", "Sharp Objects"]
