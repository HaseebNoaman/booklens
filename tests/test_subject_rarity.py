"""A shared subject is only evidence if it is rare enough to distinguish.

Plain set intersection counted every shared label the same, so "you have read 2
books with these subjects: Speculative" fired for half the catalogue and told
the reader nothing. These lock in the fix and, more importantly, the honesty
rule that came with it: when the only thing in common is a shelf-wide label,
the answer is "no meaningful overlap", not a match.
"""
import math

import taste_profile as tp

# Shares taken from the real catalogue: 238 books carry any subject at all.
CATALOGUE = 238
COUNTS = {
    "speculative": 130,           # 54.6% — cannot distinguish
    "children's literature": 89,  # 37.4% — cannot distinguish
    "fantasy": 73,                # 30.7% — weak but kept
    "science": 54,                # 22.7%
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
    common = tp.subject_weight("speculative", COUNTS, CATALOGUE)
    assert rare > common
    assert math.isclose(rare, math.log(CATALOGUE / 2), rel_tol=1e-6)


def test_only_the_shelf_wide_labels_are_disqualified():
    """The cut is a third, and it lands where the data already thins out."""
    assert tp.too_common_to_be_evidence("speculative", COUNTS, CATALOGUE)
    assert tp.too_common_to_be_evidence("children's literature", COUNTS, CATALOGUE)
    assert not tp.too_common_to_be_evidence("fantasy", COUNTS, CATALOGUE)
    assert not tp.too_common_to_be_evidence("time travel", COUNTS, CATALOGUE)


# ----- what the reader is told -----

def test_the_rare_subject_leads_even_when_a_common_one_covers_more_books():
    """Ranking by how many of the reader's books carry a label always surfaced
    the commonest one, because a label common across the catalogue is common
    inside any one library too."""
    history = [
        read("The Hobbit", "speculative", "fantasy"),
        read("Harry Potter", "speculative", "fantasy"),
        read("The Time Machine", "speculative", "time travel"),
    ]
    result = assess(["speculative", "fantasy", "time travel"], history)
    assert result["state"] == tp.STATE_MATCH
    assert result["subjects"][0] == "Time Travel"
    assert "Speculative" not in result["subjects"]


def test_a_shelf_wide_label_alone_is_not_a_match():
    """Kindred and The Hobbit share exactly one label — speculative. One is a
    novel about slavery and time, the other a children's quest. Calling that a
    match is true and useless."""
    history = [read("The Hobbit", "speculative", "fantasy",
                    "children's literature")]
    result = assess(["speculative", "science"], history, "Kindred")
    assert result["state"] == tp.STATE_NO_MATCH
    assert result["examples"] == []


def test_the_examples_come_from_the_rare_subject():
    history = [
        read("The Hobbit", "speculative", "fantasy"),
        read("The Time Machine", "speculative", "time travel"),
        read("Kindred", "speculative", "time travel"),
    ]
    result = assess(["speculative", "time travel"], history, "11/22/63")
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
    result = assess(["children's literature", "speculative"], history,
                    "Charlotte's Web")
    assert result["state"] == tp.STATE_NO_MATCH        # not evidence
    assert "Children's Literature" in result["subjects"]  # still described


def test_without_counts_the_old_behaviour_is_unchanged():
    """Callers that do not pass a catalogue still work — the weighting is an
    argument, not a hidden dependency, which is what keeps this module
    testable without a database."""
    history = [read("The Hobbit", "speculative", "fantasy")]
    result = tp.assess(book("speculative"), history, "Anything")
    assert result["state"] == tp.STATE_MATCH


def test_a_subject_the_catalogue_has_never_seen_still_counts():
    history = [read("Some Book", "cyberpunk")]
    result = assess(["cyberpunk"], history)
    assert result["state"] == tp.STATE_MATCH
    assert result["subjects"] == ["Cyberpunk"]
