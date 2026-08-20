# taste_profile.py
#
# "Is this for you?" — the evidence behind that question.
#
# WHAT THIS IS: string overlap between the subject labels of the scanned book
# and the subject labels of books the user has actually engaged with. It is not
# semantic understanding, it does not model taste, and it cannot tell you
# whether you will enjoy a book. Defend it as exactly that.
#
# The output is deliberately EVIDENCE, never a verdict: "you have read or saved
# 4 books with these subjects", never "87% match". A percentage would claim a
# confidence the method does not have.
#
# WHY A BARE SCAN DOES NOT COUNT
# A history row is created every time the camera identifies a cover, so counting
# every history row would make the feature circular -- it would report that you
# like the book you are currently pointing the camera at. Only deliberate
# signals count: finished, currently reading, or favourited. Scanning something
# in a shop says nothing about taste, and "want to read" is an intention rather
# than an experience, so neither enters the profile.

# Subject labels that carry no discriminating information. Almost every novel
# is "Fiction", so matching on it would make every book look like every other
# book. Measured on the catalogue: dropping these leaves ~75% of books with at
# least one usable subject, which is why the "no subject data" state exists.
UNINFORMATIVE_SUBJECTS = {
    "fiction", "nonfiction", "non-fiction", "general", "literature",
    "literary", "books", "book", "ebook", "e-book", "text", "novel",
    "english fiction", "american fiction", "juvenile fiction",
    "fiction / general", "reading", "miscellanea", "collections",
    "large type books", "accessible book", "protected daisy", "in library",
    "overdrive", "internet archive wishlist",
}

# One book is enough to say something true.
#
# This was 3, on the reasoning that a profile of one or two books is "noise".
# That objection applies to PREDICTING enjoyment, and this module does not
# predict -- it reports a fact the reader can check: "you have read 1 book with
# these subjects: In Cold Blood." True, verifiable, and useful. Demanding three
# books made a new reader wait three times longer than the claim required.
MIN_PROFILE_BOOKS = 1

# How many subjects and example titles the card is allowed to show. The card
# shows evidence, not a data dump.
MAX_SUBJECTS_SHOWN = 3
MAX_EXAMPLES_SHOWN = 3

# The states this module can return.
STATE_NO_SUBJECTS = "no_subject_data"   # the BOOK has no usable subjects
STATE_COLD_START = "cold_start"         # the USER has nothing to compare with
STATE_MATCH = "match"                   # overlap with books actually read
STATE_INTEREST_MATCH = "interest_match"  # overlap with CHOSEN interests only
STATE_NO_MATCH = "no_match"             # something to compare with, no overlap

# Chosen interests are a weaker signal than a book someone actually read, and
# they are treated that way: they are consulted only when the reader's own
# books cannot answer, and the card must word them differently. A reader who
# ticked "Thrillers" learns nothing from being told a thriller is a thriller;
# the value of this section is the surprise of "you have read four like this".


def normalize_subjects(raw):
    """Turn one book's subject field into a set of comparable labels.

    Three shapes reach this function, and all three must end up comparable:
      Google Books      "Fiction / Thrillers / Psychological"
      Open Library      "Psychological fiction, Thrillers, Murder"
      local catalogue   "Speculative fiction; Fantasy; Fiction"

    The semicolon is not optional. Catalogue rows store genres semicolon-joined
    (database.py copies catalogue_books.genres into books.categories), so
    missing it would collapse a Tier-1 hit into one meaningless label and the
    feature would fail on exactly the offline path.
    """
    if not raw:
        return set()
    items = raw if isinstance(raw, (list, tuple, set)) else [raw]
    parts = []
    for item in items:
        normalized = str(item).replace("/", ",").replace(";", ",")
        parts.extend(normalized.split(","))

    subjects = set()
    for part in parts:
        label = " ".join(part.strip().lower().split())
        if not label or label in UNINFORMATIVE_SUBJECTS:
            continue
        # "Psychological fiction" and "Psychological" are the same shelf.
        if label.endswith(" fiction"):
            label = label[: -len(" fiction")].strip()
            if not label or label in UNINFORMATIVE_SUBJECTS:
                continue
        if len(label) > 40:      # provider junk, not a shelf label
            continue
        subjects.add(label)
    return subjects


def display_subject(label):
    """Title-case a normalised label for the card."""
    return " ".join(word.capitalize() for word in label.split())


def is_profile_signal(row):
    """Does this history row represent real engagement, not just a scan?

    See the module docstring: identified/want_to_read are excluded on purpose.
    """
    if row.get("is_favorite"):
        return True
    return (row.get("reading_status") or "") in ("finished", "reading")


def build_profile(history_rows):
    """Collapse the user's engaged-with books into subject -> example titles."""
    subjects = {}
    counted = 0
    for row in history_rows:
        if not is_profile_signal(row):
            continue
        counted += 1
        title = (row.get("title") or "").strip()
        for label in normalize_subjects(row.get("categories")):
            subjects.setdefault(label, [])
            if title and title not in subjects[label]:
                subjects[label].append(title)
    return {"subjects": subjects, "book_count": counted}


def assess(book_subjects_raw, history_rows, book_title="", interests=""):
    """Decide which of the four states the card should show, and with what.

    Order matters. A book with no subjects cannot be assessed no matter how
    rich the profile is, so that is checked first: telling a well-read user to
    "build your reading profile" because the PUBLISHER omitted subjects would be
    both wrong and insulting.
    """
    book_subjects = normalize_subjects(book_subjects_raw)

    # A book is never evidence for itself. The caller already excludes the
    # scanned row by id, but the SAME WORK can exist as two rows -- a different
    # edition, or a second provider's record of it -- and those have different
    # ids. Matching on title as well closes that gap, which matters here
    # because work-vs-edition duplication is normal in this data.
    scanned = " ".join((book_title or "").strip().lower().split())
    if scanned:
        history_rows = [r for r in history_rows
                        if " ".join((r.get("title") or "").strip().lower().split())
                        != scanned]

    profile = build_profile(history_rows)

    if not book_subjects:
        return {
            "state": STATE_NO_SUBJECTS,
            "subjects": [],
            "matched_subjects": [],
            "book_count": profile["book_count"],
            "examples": [],
        }

    shown_subjects = [display_subject(s) for s in
                      sorted(book_subjects)[:MAX_SUBJECTS_SHOWN]]
    overlap = book_subjects & set(profile["subjects"])

    if not overlap:
        # Books the reader actually read could not answer. Fall back to what
        # they told us -- explicitly labelled, never dressed up as reading.
        chosen = book_subjects & normalize_subjects(interests)
        if chosen:
            return {
                "state": STATE_INTEREST_MATCH,
                "subjects": [display_subject(s) for s in sorted(chosen)
                             [:MAX_SUBJECTS_SHOWN]],
                "matched_subjects": [display_subject(s) for s in sorted(chosen)],
                "book_count": profile["book_count"],
                "examples": [],
            }
        if profile["book_count"] < MIN_PROFILE_BOOKS:
            return {
                "state": STATE_COLD_START,
                "subjects": shown_subjects,
                "matched_subjects": [],
                "book_count": profile["book_count"],
                "examples": [],
            }
        return {
            "state": STATE_NO_MATCH,
            "subjects": shown_subjects,
            "matched_subjects": [],
            "book_count": profile["book_count"],
            "examples": [],
        }

    # Show the subjects the user has read most in: the strongest evidence
    # first, so the reason the section appeared is the reason shown.
    ranked = sorted(overlap,
                    key=lambda s: (-len(profile["subjects"][s]), s))
    shown = ranked[:MAX_SUBJECTS_SHOWN]

    examples = []
    matching_titles = set()
    for label in ranked:
        for title in profile["subjects"][label]:
            matching_titles.add(title)
            if title not in examples and len(examples) < MAX_EXAMPLES_SHOWN:
                examples.append(title)

    return {
        "state": STATE_MATCH,
        "subjects": [display_subject(s) for s in shown],
        "matched_subjects": [display_subject(s) for s in ranked],
        # The count is of BOOKS, not of subject hits: "you have read 4 books
        # with these subjects" must survive a examiner counting them by hand.
        "book_count": len(matching_titles),
        "examples": examples,
    }
