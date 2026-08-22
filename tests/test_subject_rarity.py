"""A shared subject is only evidence if it is rare enough to distinguish.

Plain set intersection counted every shared label the same, so "you have read 2
books with these subjects: Speculative" fired for half the catalogue and told
the reader nothing. These lock in the fix and, more importantly, the honesty
rule that came with it: when the only thing in common is a shelf-wide label,
the answer is "no meaningful overlap", not a match.
"""
import math

import taste_profile as tp

# Shares taken from the 238-book catalogue these rules were derived on. The
# shelf is 60 hand-tagged books now, so treat this as a fixture with realistic
# proportions rather than a description of today's data.
#
# The labels are written in their CANONICAL form, because that is what
# normalize_subjects produces and what assess() will look up. "Sci-fi" and
# "Science fiction" both resolve to "sci-fi" now -- keying this fixture on the
# pre-map spelling made every count invisible and four of these tests failed
# with subjects that should have been disqualified sailing through as rare.
CATALOGUE = 238
COUNTS = {
    "sci-fi": 130,                # 54.6% — cannot distinguish
    "children's literature": 89,  # 37.4% — cannot distinguish
    "fantasy": 73,                # 30.7% — weak but kept
    "horror": 54,                 # 22.7%
    "time travel": 2,             # 0.8%  — strong
    "vampire": 2,
}


def book(*subjects):
    return "; ".join(subjects)


def read(title, *subjects):
    return {"title": title, "categories": book(*subjects),
            "reading_status": "finished", "is_favorite": 0}


def assess(subjects, history, title="Scanned Book"):
    return tp.assess(book(*subjects), history, title,
                     subject_counts=COUNTS, catalogue_size=CATALOGUE)


# ----- the weight itself -----

def test_a_rare_subject_outweighs_a_common_one():
    rare = tp.subject_weight("time travel", COUNTS, CATALOGUE)
    common = tp.subject_weight("sci-fi", COUNTS, CATALOGUE)
    assert rare > common
    assert math.isclose(rare, math.log(CATALOGUE / 2), rel_tol=1e-6)


def test_only_the_shelf_wide_labels_are_disqualified():
    """The cut is a third, and it lands where the data already thins out."""
    assert tp.too_common_to_be_evidence("sci-fi", COUNTS, CATALOGUE)
    assert tp.too_common_to_be_evidence("children's literature", COUNTS, CATALOGUE)
    assert not tp.too_common_to_be_evidence("fantasy", COUNTS, CATALOGUE)
    assert not tp.too_common_to_be_evidence("time travel", COUNTS, CATALOGUE)


# ----- what the reader is told -----

def test_the_rare_subject_leads_even_when_a_common_one_covers_more_books():
    """Ranking by how many of the reader's books carry a label always surfaced
    the commonest one, because a label common across the catalogue is common
    inside any one library too."""
    history = [
        read("The Hobbit", "sci-fi", "fantasy"),
        read("Harry Potter", "sci-fi", "fantasy"),
        read("The Time Machine", "sci-fi", "time travel"),
    ]
    result = assess(["sci-fi", "fantasy", "time travel"], history)
    assert result["state"] == tp.STATE_MATCH
    assert result["subjects"][0] == "Time Travel"
    assert "Sci-fi" not in result["subjects"]


def test_a_shelf_wide_label_alone_is_not_a_match():
    """Kindred and The Hobbit share exactly one label — the shelf-wide one. One
    is a novel about slavery and time, the other a children's quest. Calling
    that a match is true and useless."""
    history = [read("The Hobbit", "sci-fi", "fantasy",
                    "children's literature")]
    result = assess(["sci-fi", "horror"], history, "Kindred")
    assert result["state"] == tp.STATE_NO_MATCH
    assert result["examples"] == []


def test_the_examples_come_from_the_rare_subject():
    history = [
        read("The Hobbit", "sci-fi", "fantasy"),
        read("The Time Machine", "sci-fi", "time travel"),
        read("Kindred", "sci-fi", "time travel"),
    ]
    result = assess(["sci-fi", "time travel"], history, "11/22/63")
    assert result["subjects"] == ["Time Travel"]
    assert set(result["examples"]) == {"The Time Machine", "Kindred"}
    assert "The Hobbit" not in result["examples"]


def test_a_common_label_still_describes_the_book_even_though_it_is_not_evidence():
    """Describing a book and judging a reader are different jobs.

    Hiding "children's literature" from the description was tried, because the
    catalogue applies it wrongly to To the Lighthouse and One Flew Over the
    Cuckoo's Nest. Measured, it left 17 books describing themselves as nothing
    at all — Charlotte's Web, Little Women, Anne of Green Gables, Goodnight
    Moon — where the label is exactly right. Two bad rows are a data problem;
    a frequency threshold cannot tell them from the seventeen good ones.
    """
    history = [read("Something Else", "crime")]
    result = assess(["children's literature", "sci-fi"], history,
                    "Charlotte's Web")
    assert result["state"] == tp.STATE_NO_MATCH        # not evidence
    assert "Children's Literature" in result["subjects"]  # still described


def test_without_counts_the_old_behaviour_is_unchanged():
    """Callers that do not pass a catalogue still work — the weighting is an
    argument, not a hidden dependency, which is what keeps this module
    testable without a database."""
    history = [read("The Hobbit", "sci-fi", "fantasy")]
    result = tp.assess(book("sci-fi"), history, "Anything")
    assert result["state"] == tp.STATE_MATCH


def test_a_subject_the_catalogue_has_never_seen_still_counts():
    # Deliberately a label with no synonym entry. "cyberpunk" used to sit here
    # and now resolves to sci-fi, which made this test assert that an unseen
    # subject came back as one the catalogue knows well.
    history = [read("Some Book", "steampunk")]
    result = assess(["steampunk"], history)
    assert result["state"] == tp.STATE_MATCH
    assert result["subjects"] == ["Steampunk"]


# ----- one vocabulary, two speakers -----
#
# The 60 verified books were re-tagged by hand into labels that each mean one
# thing. Providers were never consulted about that, and they answer in their
# own words -- "Thrillers", "Suspense", "Space opera", "Self-Help". Measured,
# the share of scanned books sharing any subject with the shelf fell from 82%
# to 53% the day the re-tag landed, and back to 90% once these synonyms existed.

def test_provider_words_land_on_the_catalogue_s_labels():
    assert tp.normalize_subjects("Thrillers, Suspense") == {"thriller"}
    assert tp.normalize_subjects("Space Opera") == {"sci-fi"}
    assert tp.normalize_subjects("Self-Help, Motivational") == {"self-improvement"}
    assert tp.normalize_subjects("Biography & Autobiography") == {"memoir"}


def test_a_provider_saying_science_means_science_fiction():
    """The collision this map exists to prevent, and the second time this exact
    shape has appeared.

    A provider returns Dune as "Science, Space Opera". The catalogue means
    Cosmos when it says science. Left alone, a reader holding Dune would be
    offered a book about astronomy -- which is what happened when A Game of
    Thrones was tagged "Power" and the shelf answered with The 48 Laws of Power.

    So the catalogue label is "Popular science" and the provider's bare
    "science" resolves to sci-fi. These must never meet.
    """
    assert tp.normalize_subjects("Science, Space Opera") == {"sci-fi"}
    assert tp.normalize_subjects("Science Fiction") == {"sci-fi"}
    assert tp.normalize_subjects("Popular science") == {"popular science"}
    assert not (tp.normalize_subjects("Science, Space Opera")
                & tp.normalize_subjects("Non-fiction; Popular science"))


def test_only_true_synonyms_are_merged():
    """A psychological novel and a thriller are not the same promise, and
    merging them would buy coverage by destroying the distinction the whole
    rarity weighting exists to measure."""
    assert tp.normalize_subjects("Psychological") == {"psychological"}
    assert tp.normalize_subjects("Psychological") != tp.normalize_subjects("Thrillers")
    assert tp.normalize_subjects("Horror") == {"horror"}
    assert tp.normalize_subjects("Gothic") == {"gothic"}
