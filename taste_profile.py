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
import math

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


# Weighting a shared subject by how rare it is.
#
# WHY THIS EXISTS. Plain set intersection counted every shared label the same,
# so "you have read 2 books with these subjects: Speculative" fired for half the
# catalogue and told the reader nothing. Kindred and The Hobbit share exactly one
# label -- speculative -- and one is a novel about slavery and time, the other a
# children's quest. Reporting that as a match is technically true and completely
# useless.
#
# Measured over the 238 catalogue books that carry any subject at all (12 of
# the 250 have none, and those can never match on subjects whatever the reader
# has read): 85 subjects, median subject in 2 books, and only FOUR carried by
# more than a fifth of the shelf -- speculative 54.6%, children's literature
# 37.4%, fantasy 30.7%, science 22.7%.
#
# The weight is log(total / count) -- inverse document frequency, the standard
# measure of how much a term distinguishes. It separates where it matters:
# speculative scores 0.65, time travel 4.83.
#
# A subject on more than a THIRD of the shelf cannot distinguish at all: sharing
# it puts the reader in a group larger than a third of everything, which is what
# most books have rather than something the reader chose. Only speculative
# (54.6%) and children's literature (37.4%) exceed it -- exactly the pair that
# produced the useless matches -- while fantasy at 30.7% stays. The cut sits
# where the data already thins out, not on a round number picked for tidiness.
COMMON_SUBJECT_SHARE = 1.0 / 3.0


def subject_weight(label, counts, total):
    """How much a shared subject distinguishes. Higher means rarer."""
    if not counts or not total:
        return 1.0
    seen = counts.get(label, 0)
    if seen <= 0:
        # Absent from the catalogue means it is a provider label we have never
        # shelved. Treat it as informative rather than throwing it away.
        return math.log(total)
    return math.log(total / float(seen))


def too_common_to_be_evidence(label, counts, total):
    if not counts or not total:
        return False
    return counts.get(label, 0) / float(total) > COMMON_SUBJECT_SHARE


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


def assess(book_subjects_raw, history_rows, book_title="", interests="",
           subject_counts=None, catalogue_size=0):
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

    # Describing the book is NOT the same job as being evidence about the
    # reader, and the two need different rules.
    #
    # A rarity cut was tried here and reverted after measuring it. Hiding
    # "children's literature" removed a label that is genuinely wrong on To
    # the Lighthouse and One Flew Over the Cuckoo's Nest -- but it also left
    # 17 books describing themselves as nothing at all, including Charlotte's
    # Web, Little Women, Anne of Green Gables and Goodnight Moon, where the
    # label is exactly right. Two bad rows are a data problem; a frequency
    # threshold cannot tell them from the seventeen good ones.
    #
    # So: a common label may still DESCRIBE a book. It may not be EVIDENCE
    # that the book suits this reader -- that rule is applied below, where it
    # belongs.
    shown_subjects = [display_subject(s) for s in
                      sorted(book_subjects)[:MAX_SUBJECTS_SHOWN]]

    overlap = book_subjects & set(profile["subjects"])

    if not overlap:
        # Books the reader actually read could not answer. Fall back to what
        # they told us -- explicitly labelled, never dressed up as reading.
        chosen = book_subjects & normalize_subjects(interests)
        # The same rarity rule as a real match. A reader who ticked a label
        # half the catalogue carries learns nothing from being told this book
        # carries it too. Measured: only 2 of the 24 offered interests are
        # common enough to be dropped here, so this rarely costs anything.
        chosen = {c for c in chosen
                  if not too_common_to_be_evidence(c, subject_counts, catalogue_size)}
        if chosen:
            ranked_interests = sorted(
                chosen,
                key=lambda s: (-subject_weight(s, subject_counts, catalogue_size), s))
            return {
                "state": STATE_INTEREST_MATCH,
                "subjects": [display_subject(s) for s in
                             ranked_interests[:MAX_SUBJECTS_SHOWN]],
                "matched_subjects": [display_subject(s) for s in ranked_interests],
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

    # Rank by how much each shared subject DISTINGUISHES, then by how many of
    # the reader's books carry it. Ranking by count alone always surfaced the
    # commonest label, because a label common across the catalogue is common
    # inside any one library too.
    ranked = sorted(overlap,
                    key=lambda s: (-subject_weight(s, subject_counts, catalogue_size),
                                   -len(profile["subjects"][s]), s))

    # If everything shared is a shelf-wide label, there is no evidence here.
    # Saying "no meaningful overlap" is the honest answer; claiming a match on
    # a label half the catalogue carries is how this section lost its meaning.
    if all(too_common_to_be_evidence(s, subject_counts, catalogue_size)
           for s in ranked):
        return {
            "state": STATE_NO_MATCH,
            "subjects": shown_subjects,
            "matched_subjects": [],
            "book_count": profile["book_count"],
            "examples": [],
        }

    ranked = [s for s in ranked
              if not too_common_to_be_evidence(s, subject_counts, catalogue_size)]
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


# ----- the starter shelf -----
#
# WHY THIS EXISTS. A reader with an empty library scans a cover and gets
# STATE_COLD_START: "nothing to compare this with yet". That state was honest
# but it was also a dead end -- the only way out was a link that navigated away
# from the result they had just taken a photograph of.
#
# So the panel asks a question instead: "have you read any of these six?" The
# answer is a fact the reader supplies, and one answer is enough (see
# MIN_PROFILE_BOOKS above) to turn the section into a real one.
#
# WHICH six is the whole design. Offering six books at random mostly asks about
# books that cannot answer anything: measured over the 238 catalogue books that
# carry subjects, a book shares a DISTINGUISHING subject with a median of 40
# others, so the informative peers exist and picking them is worth doing.
#
# This is not rigging the answer. The panel asks whether the reader has read a
# book; it never assumes they have, and a tap is never read as a "like". What
# the selection chooses is which QUESTION is worth asking -- the one whose
# answer changes what the card can say. The truth of "you have read 2 books
# tagged Time travel" does not depend on which books we asked about.
#
# Measured before building: one tap produces a real match for 196 of the 238
# catalogue books, and for 23 of the 31 real scanned books that carry subjects.
# The other 42 carry only shelf-wide labels, and for those no peer can ever be
# evidence -- see targeted=False at the call site, where the copy says so.


def starter_candidates(book_subjects_raw, catalogue_rows, exclude_titles=(),
                       limit=6, subject_counts=None, catalogue_size=0):
    """Six books worth asking a new reader about, given the book in their hand.

    catalogue_rows are passed in rather than queried, for the same reason
    subject_counts is: this module must stay testable without a database. Each
    row needs a "title" and a "categories" (or "genres") field.

    Returns (rows, targeted). targeted is False when the scanned book has no
    subject rare enough to be evidence -- the caller still shows a shelf, but
    must not promise it will answer THIS book.
    """
    wanted = {s for s in normalize_subjects(book_subjects_raw)
              if not too_common_to_be_evidence(s, subject_counts, catalogue_size)}

    skip = {" ".join((t or "").strip().lower().split()) for t in exclude_titles}
    skip.discard("")

    def subjects_of(row):
        return normalize_subjects(row.get("categories") or row.get("genres"))

    def usable(row):
        title = " ".join((row.get("title") or "").strip().lower().split())
        return bool(title) and title not in skip

    rows = [r for r in catalogue_rows if usable(r)]

    if not wanted:
        # Nothing about this book can be matched, so ask about the books that
        # will distinguish the reader MOST for whatever they scan next. The
        # profile outlives the scan; that is the honest thing this can offer.
        return _spread_across_subjects(rows, subjects_of, subject_counts,
                                       catalogue_size, limit), False

    # Bucket by the rarest subject each candidate shares with the book in hand,
    # then take one from each bucket in turn. Without the round robin the six
    # are six copies of the same reason -- every Christie novel, and nothing to
    # tap for a reader who has read none of them.
    buckets = {}
    for row in rows:
        shared = subjects_of(row) & wanted
        if not shared:
            continue
        lead = max(shared, key=lambda s: subject_weight(s, subject_counts,
                                                        catalogue_size))
        buckets.setdefault(lead, []).append(row)

    if not buckets:
        return _spread_across_subjects(rows, subjects_of, subject_counts,
                                       catalogue_size, limit), False

    return _round_robin(buckets, subject_counts, catalogue_size, limit), True


def _round_robin(buckets, subject_counts, catalogue_size, limit):
    order = sorted(buckets, key=lambda s: (-subject_weight(s, subject_counts,
                                                           catalogue_size), s))
    picked, index = [], 0
    while len(picked) < limit and any(buckets[s] for s in order):
        label = order[index % len(order)]
        if buckets[label]:
            picked.append(buckets[label].pop(0))
        index += 1
    return picked


def _spread_across_subjects(rows, subjects_of, subject_counts, catalogue_size,
                            limit):
    """The fallback shelf: books that between them cover the most ground."""
    buckets = {}
    for row in rows:
        usable_subjects = [s for s in subjects_of(row)
                           if not too_common_to_be_evidence(s, subject_counts,
                                                            catalogue_size)]
        if not usable_subjects:
            continue
        lead = max(usable_subjects,
                   key=lambda s: subject_weight(s, subject_counts, catalogue_size))
        buckets.setdefault(lead, []).append(row)
    return _round_robin(buckets, subject_counts, catalogue_size, limit)


# ----- the same score, run backwards -----
#
# WHY THIS EXISTS. When identification refuses, the reader leaves with nothing
# at all -- which is the honest answer to "what book is this", and a poor
# answer to "I am standing in a shop". The profile that answers "is this for
# you?" can also answer "what on our shelf is closest to what you have read",
# and it is the same arithmetic pointed the other way.
#
# It is called "closest on our shelf", never "recommendations". Two reasons,
# both measured:
#
#   1. There are 250 books. A recommender chooses from everything published.
#   2. The catalogue's genre lists come from a source that mixes a book's
#      primary genre with its peripheral ones. House of Leaves -- a horror
#      novel -- is filed under "Romance novel" and "Chivalric romance", so a
#      reader of Emma and Pride and Prejudice was offered it, first.
#
# THE TWO RULES THAT MAKE IT WORK, and the measurements behind them:
#
#   Lead with the reason the MOST of the reader's books support. Ranking by
#   rarity alone put "Mockingjay, because adventure novel" above "A Feast for
#   Crows, because fantasy" for someone who had read The Hobbit and A Game of
#   Thrones -- rarer, and plainly worse.
#
#   Then prefer the book whose OWN subjects that reason actually accounts for.
#   A record claiming seven genres matches half the shelf; it is a hub, not a
#   neighbour. Dracula lists 7 subjects and House of Leaves 5, against a median
#   of 2. Dividing by the candidate's own subject count is what removed House
#   of Leaves from a Jane Austen reader's list and put Sense and Sensibility
#   and The Age of Innocence there instead.
#
# A stricter rule was tried first and measured: requiring two shared subjects
# instead of one. It cost 34 points of coverage (44% of one-book profiles left
# with nothing, against 10%) and it did NOT fix House of Leaves, which shares
# two. Rejected on the numbers.
#
# Coverage, over 500 random profiles at each size: one book 10% get nothing,
# two books 1%, three books 0%. When there is nothing, the caller shows
# nothing -- an empty panel promising neighbours is worse than no panel.
#
# NOT collaborative filtering, and it must not become it: 10 users, 23 history
# rows, no ratings table. Nothing here is derived from another reader.

CLOSEST_PER_REASON = 2      # never four books for one reason


def closest_from_shelf(history_rows, catalogue_rows, limit=4,
                       per_reason=CLOSEST_PER_REASON,
                       subject_counts=None, catalogue_size=0):
    """Books on our own shelf nearest to what this reader has read.

    Returns a list of {"book", "reason", "because"} -- the catalogue row, the
    subject that earned it a place, and the reader's own titles carrying that
    subject. The reason travels WITH the book because the reader has to be able
    to check it: "because you read The Shining and It" is auditable, a score
    out of five is not.

    catalogue_rows are passed in, like subject_counts, so this module keeps
    working without a database.
    """
    profile = build_profile(history_rows)
    support = {label: titles for label, titles in profile["subjects"].items()
               if not too_common_to_be_evidence(label, subject_counts,
                                                catalogue_size)}
    if not support:
        return []

    already = {" ".join((r.get("title") or "").strip().lower().split())
               for r in history_rows}
    already.discard("")

    ranked = []
    for row in catalogue_rows:
        title = " ".join((row.get("title") or "").strip().lower().split())
        if not title or title in already:
            continue
        own = {s for s in normalize_subjects(row.get("categories") or
                                            row.get("genres"))
               if not too_common_to_be_evidence(s, subject_counts,
                                                catalogue_size)}
        shared = own & set(support)
        if not shared:
            continue
        reason = max(shared, key=lambda s: (len(support[s]),
                                            subject_weight(s, subject_counts,
                                                           catalogue_size), s))
        ranked.append((
            -len(support[reason]),                  # backed by most of your books
            -len(shared) / float(len(own)),         # and it is what this book IS
            -subject_weight(reason, subject_counts, catalogue_size),
            row.get("title") or "",
            row, reason))

    ranked.sort(key=lambda item: item[:4])

    picked, used = [], {}
    for item in ranked:
        row, reason = item[4], item[5]
        # per_reason=None means "order everything, cap nothing" -- what Browse
        # needs, where the whole shelf is on screen and variety in the first
        # four is not the point.
        if per_reason is not None and used.get(reason, 0) >= per_reason:
            continue
        used[reason] = used.get(reason, 0) + 1
        picked.append({"book": row,
                       "reason": display_subject(reason),
                       "because": support[reason][:MAX_EXAMPLES_SHOWN]})
        if limit and len(picked) == limit:
            break
    return picked
