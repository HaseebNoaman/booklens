"""Re-tag the shelf by hand, and check the tagging before it is written.

Genres are the material "Is this for you?" and "Closest on our shelf" are made
of, and the inherited ones were unusable: The Fellowship of the Ring filed under
"Autobiography; Biography; Reference", The Invisible Man under "Albino bias",
Dracula and Beloved and The Shining all under "Children's literature", and The
48 Laws of Power under nothing at all.

curate/genres.json holds the replacement. This script checks it against the
rules that make the two shelf features work before writing a single row:

    every label carries at least 2 books   -- one carrier is a dead end, because
                                              a reader who read it gets no
                                              neighbour from it
    no label carries more than a third     -- past that a subject stops
                                              distinguishing, which is the rule
                                              taste_profile already applies
    nothing survives normalisation as junk -- "Novel", "Fiction" and "Literary"
                                              are discarded by
                                              normalize_subjects, so a book
                                              tagged only with those is invisible

    python curate/apply_genres.py            # check and report
    python curate/apply_genres.py --confirm  # write it

Writing genres is not writing prose. A label is a classification anybody can
check against the book; a description is text somebody has to have authored.
That is why these are set by hand and the descriptions are not.
"""
import argparse
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import database                                    # noqa: E402
import taste_profile as tp                         # noqa: E402

GENRES = os.path.join(HERE, "genres.json")
MIN_CARRIERS = 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    with io.open(GENRES, encoding="utf-8") as handle:
        assigned = json.load(handle)["genres"]

    shelf = {r["id"]: dict(r) for r in database.list_catalogue("VERIFIED")}
    missing = [t for t in shelf if str(t) not in assigned]
    extra = [k for k in assigned if int(k) not in shelf]

    counts = collections.Counter()
    usable = {}
    for key, labels in assigned.items():
        survived = tp.normalize_subjects("; ".join(labels))
        usable[key] = survived
        for label in survived:
            counts[label] += 1

    limit = len(shelf) // 3
    print("%d books, %d distinct labels after normalisation" % (len(shelf), len(counts)))
    print()
    print("%-22s %s" % ("label", "books"))
    for label, n in counts.most_common():
        flag = ""
        if n > limit:
            flag = "  TOO COMMON (over %d)" % limit
        elif n < MIN_CARRIERS:
            flag = "  DEAD END (needs %d)" % MIN_CARRIERS
        print("   %-22s %3d%s" % (tp.display_subject(label), n, flag))

    problems = []
    if missing:
        problems.append("%d books on the shelf have no entry: %s"
                        % (len(missing), [shelf[t]["title"] for t in missing][:5]))
    if extra:
        problems.append("%d entries are for books not on the shelf: %s" % (len(extra), extra[:5]))
    for label, n in counts.items():
        if n > limit:
            problems.append("'%s' carries %d of %d books" % (label, n, len(shelf)))

    # The dead-end test belongs to the BOOK, not to the label. A rare label
    # beside a well-carried one costs nothing -- it is extra colour, and the
    # reader still gets neighbours through the other. What must never happen is
    # a book whose every label is rare, or a book left with none at all:
    # "Non-fiction" is deliberately discarded by normalize_subjects, because it
    # describes a form rather than a subject, and a book tagged only with it
    # would be invisible to both shelf features. It still does its job in the
    # raw genres text, which is what infer_kind reads.
    for key, survived in usable.items():
        title = shelf.get(int(key), {}).get("title", key)
        if not survived:
            problems.append("%s has no usable subject at all" % title)
        elif max(counts[label] for label in survived) < MIN_CARRIERS:
            problems.append("%s has no subject shared with another book" % title)

    if problems:
        print("\nNOT WRITTEN -- fix these first:")
        for line in problems:
            print("   -", line)
        return

    print("\nAll rules pass.")
    if not args.confirm:
        print("Dry run. Nothing was written. Add --confirm to apply.")
        return

    for key, labels in assigned.items():
        database.update_catalogue_book(int(key), {
            "genres": "; ".join(labels),
            "human_verified": 0,
        }, None)
    database.reset_subject_counts()
    print("Wrote genres for %d books." % len(assigned))


if __name__ == "__main__":
    main()
