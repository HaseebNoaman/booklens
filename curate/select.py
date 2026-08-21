"""Choose the books the catalogue keeps, and prove the shelf still works.

The catalogue was 250 records inherited from a dataset. Roughly a third of the
covers did not load, a fifth of the books carried no subject specific enough to
say anything about a reader, and nobody had checked whether the stored summary
was the best text available. A reader meeting one of those books got a card that
looked unfinished.

This script decides which books stay. It writes nothing to the database -- run
`curate/apply.py` for that -- so the decision can be re-run, argued with, and
re-run again.

    python curate/select.py            # decide and report
    python curate/select.py --size 120 # try a different target

THE ONE THING THIS SCRIPT EXISTS TO PREVENT. Cutting 250 books to 100 by any
obvious rule -- keep the newest, keep the most popular, keep the first hundred --
throws away a third of the subjects, and the subjects are what "is this for you?"
and "closest on our shelf" are made of. Measured on the naive cut: 82 subjects
become 50 and the books that can be evidence at all drop from 181 to 77.

So the fill order is by the THINNEST subject, not by fame. Fame only breaks ties.
Measured that way, 100 books keep all 82 subjects and match the 250-book shelf to
within a point -- because 69 of the 250 contribute nothing to either feature.

The acceptance gate at the bottom is not decoration. If the chosen set fails it,
the script says so and refuses to recommend that size.
"""
import argparse
import collections
import csv
import io
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import database                    # noqa: E402
import taste_profile as tp         # noqa: E402

MANIFEST = os.path.join(APP, "bench", "manifest.csv")
AUDITS = os.environ.get("BOOKLENS_AUDIT_DIR", HERE)

# The gate. These are the numbers the 250-book shelf achieves today; the chosen
# shelf has to stay level with them, not merely "not collapse".
MIN_SUBJECTS = 80          # of 82 today
MAX_EMPTY_AT_TWO = 0       # percent of 2-book profiles with nothing to offer
MIN_ONE_TAP = 90           # percent of usable books where one tap converts
SAMPLES = 400


def load_audit(name):
    path = os.path.join(AUDITS, name)
    if not os.path.exists(path):
        return None
    return {row["id"]: row for row in json.load(io.open(path, encoding="utf-8"))}


def benchmark_titles():
    """The covers the identification number is measured on.

    These are mandatory keeps. Tier-1 lookup reads the catalogue, so dropping
    one of them changes what the benchmark measures, and the headline figure
    stops describing the build it is quoted against.
    """
    with io.open(MANIFEST, encoding="utf-8") as handle:
        return {(row["title"] or "").strip().lower()
                for row in csv.DictReader(handle)}


def census(rows):
    """Exactly what database.catalogue_subject_counts() computes.

    The denominator is books with genre TEXT, not books whose genres survive
    normalisation -- 238 of today's 250, not 217. Getting this wrong moves the
    "too common" line: at 217 the share of `fantasy` is 33.6% and it is
    disqualified, at 238 it is 30.7% and it is evidence. The selection would
    then be optimising against a rule the running app does not use.
    """
    counts = collections.Counter()
    for row in rows:
        for label in tp.normalize_subjects(row["genres"]):
            counts[label] += 1
    return counts, sum(1 for r in rows if (r["genres"] or "").strip())


def distinguishing(raw, counts, total):
    return {s for s in tp.normalize_subjects(raw)
            if not tp.too_common_to_be_evidence(s, counts, total)}


def evaluate(rows, label):
    """The two shipped features, measured against a candidate shelf.

    Both read the catalogue: the starter shelf asks a new reader about books
    that share a distinguishing subject with the one they scanned, and the
    closest shelf offers neighbours of what they have read. Neither can work on
    subjects that only one book carries.
    """
    counts, total = census(rows)
    useful = {r["id"]: distinguishing(r["genres"], counts, total) for r in rows}
    pool = [r for r in rows if useful[r["id"]]]
    if not pool:
        return {"label": label, "books": len(rows), "usable": 0, "subjects": 0,
                "empty": {1: 100, 2: 100, 3: 100}, "thin": {1: 100, 2: 100, 3: 100},
                "one_tap": 0, "one_tap_of": 0}

    def neighbours(profile):
        support = set()
        for r in profile:
            support |= useful[r["id"]]
        read = {r["title"].strip().lower() for r in profile}
        return sum(1 for r in rows
                   if r["title"].strip().lower() not in read
                   and (useful[r["id"]] & support))

    random.seed(19)          # same draw every run, so two shelves are comparable
    empty, thin = {}, {}
    for size in (1, 2, 3):
        none_at_all = fewer_than_four = 0
        for _ in range(SAMPLES):
            found = neighbours(random.sample(pool, min(size, len(pool))))
            if found == 0:
                none_at_all += 1
            if found < 4:
                fewer_than_four += 1
        empty[size] = none_at_all * 100 // SAMPLES
        thin[size] = fewer_than_four * 100 // SAMPLES

    converts = 0
    for r in pool:
        want = useful[r["id"]]
        if any(o["id"] != r["id"] and (useful[o["id"]] & want) for o in rows):
            converts += 1

    carriers = [n for s, n in counts.items()
                if not tp.too_common_to_be_evidence(s, counts, total)]
    return {"label": label, "books": len(rows), "usable": len(pool),
            "subjects": len(carriers),
            "median_carriers": statistics.median(carriers) if carriers else 0,
            "empty": empty, "thin": thin,
            "one_tap": converts, "one_tap_of": len(pool)}


def show(result):
    print("%-26s books=%3d  usable=%3d  subjects=%3d  median carriers=%s"
          % (result["label"], result["books"], result["usable"],
             result["subjects"], result.get("median_carriers", "-")))
    print("     nothing to offer at profile 1/2/3 = %d%% / %d%% / %d%%"
          % (result["empty"][1], result["empty"][2], result["empty"][3]))
    print("     fewer than four neighbours       = %d%% / %d%% / %d%%"
          % (result["thin"][1], result["thin"][2], result["thin"][3]))
    if result["one_tap_of"]:
        print("     one tap converts %d of %d usable (%d%%)"
              % (result["one_tap"], result["one_tap_of"],
                 result["one_tap"] * 100 // result["one_tap_of"]))


def choose(rows, size, covers, descriptions, fame, counts, total):
    """Benchmark books first, then fill by whichever subject is thinnest.

    Fame ranks, density constrains -- and that order is the whole design. Sorted
    by fame alone the shelf fills with famous books that all share the same three
    subjects, and the fourth famous book in a subject adds nothing a reader can
    use while a subject with one carrier stays a dead end.
    """
    mandatory = benchmark_titles()
    keep, notes = [], {}

    def shelves(row):
        record = (fame or {}).get(row["id"]) or {}
        return record.get("on_shelves") or 0

    for row in rows:
        if row["title"].strip().lower() in mandatory:
            keep.append(row)
            notes[row["id"]] = "benchmark"

    kept_ids = {r["id"] for r in keep}
    useful = {r["id"]: distinguishing(r["genres"], counts, total) for r in rows}

    def qualifies(row):
        why = []
        if covers is not None:
            route = (covers.get(row["id"]) or {}).get("route", "none")
            if route == "none":
                why.append("no cover")
        if descriptions is not None:
            winner = (descriptions.get(row["id"]) or {}).get("winner", "neither")
            if winner == "neither":
                why.append("no usable description")
        if not useful[row["id"]]:
            why.append("no distinguishing subject")
        return why

    candidates = []
    for row in rows:
        if row["id"] in kept_ids:
            continue
        why = qualifies(row)
        if why:
            notes[row["id"]] = "; ".join(why)
            continue
        candidates.append(row)

    per_subject = collections.Counter()
    for row in keep:
        for label in useful[row["id"]]:
            per_subject[label] += 1

    while len(keep) < size and candidates:
        # thinnest subject this book carries, then fame, then title for stability
        candidates.sort(key=lambda r: (min(per_subject[s] for s in useful[r["id"]]),
                                       -shelves(r), r["title"]))
        pick = candidates.pop(0)
        keep.append(pick)
        notes[pick["id"]] = "fills %s" % min(useful[pick["id"]],
                                             key=lambda s: per_subject[s])
        for label in useful[pick["id"]]:
            per_subject[label] += 1

    return keep, notes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--out", default=os.path.join(HERE, "selection.json"))
    args = parser.parse_args()

    rows = [dict(r) for r in database.list_catalogue("VERIFIED")]
    counts, total = census(rows)
    covers = load_audit("cover_audit.json")
    descriptions = load_audit("description_audit.json")
    fame = load_audit("fame_audit.json")

    for name, data in (("cover", covers), ("description", descriptions),
                       ("fame", fame)):
        print("%-12s audit: %s" % (name, "%d books" % len(data) if data
                                   else "MISSING -- that bar is not applied"))
    print()

    today = evaluate(rows, "TODAY (%d)" % len(rows))
    show(today)
    print()

    keep, notes = choose(rows, args.size, covers, descriptions, fame,
                         counts, total)
    chosen = evaluate(keep, "CHOSEN (%d)" % len(keep))
    show(chosen)
    print()

    failures = []
    if chosen["subjects"] < MIN_SUBJECTS:
        failures.append("subjects %d < %d" % (chosen["subjects"], MIN_SUBJECTS))
    if chosen["empty"][2] > MAX_EMPTY_AT_TWO:
        failures.append("2-book profiles with nothing: %d%% > %d%%"
                        % (chosen["empty"][2], MAX_EMPTY_AT_TWO))
    rate = (chosen["one_tap"] * 100 // chosen["one_tap_of"]
            if chosen["one_tap_of"] else 0)
    if rate < MIN_ONE_TAP:
        failures.append("one-tap conversion %d%% < %d%%" % (rate, MIN_ONE_TAP))

    if failures:
        print("ACCEPTANCE GATE FAILED at size %d:" % args.size)
        for line in failures:
            print("   -", line)
        print("Raise --size until it passes rather than shipping this shelf.")
    else:
        print("ACCEPTANCE GATE PASSED at size %d." % args.size)

    # Which subjects change status because the denominator changed. A label that
    # was too common on 250 books can become evidence on 100, and one that was
    # evidence can become too common -- both silently change what the card says.
    new_counts, new_total = census(keep)
    flipped = []
    for label in set(counts) | set(new_counts):
        was = tp.too_common_to_be_evidence(label, counts, total)
        now = tp.too_common_to_be_evidence(label, new_counts, new_total)
        if was != now and new_counts.get(label):
            flipped.append((label, was, now, new_counts[label], new_total))
    if flipped:
        print("\nSubjects whose status changes with the smaller shelf:")
        for label, was, now, n, t in sorted(flipped):
            print("   %-26s %s -> %s   (%d of %d = %.0f%%)"
                  % (label, "too common" if was else "evidence",
                     "too common" if now else "evidence", n, t, 100.0 * n / t))

    dropped = [r for r in rows if r["id"] not in {k["id"] for k in keep}]
    payload = {
        "size": len(keep),
        "gate_passed": not failures,
        "gate_failures": failures,
        "today": today, "chosen": chosen,
        "keep": [{"id": r["id"], "title": r["title"], "why": notes.get(r["id"], "")}
                 for r in keep],
        "drop": [{"id": r["id"], "title": r["title"], "why": notes.get(r["id"], "not needed to cover a subject")}
                 for r in dropped],
    }
    with io.open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    print("\nkeeping %d, dropping %d -> %s" % (len(keep), len(dropped), args.out))


if __name__ == "__main__":
    main()
