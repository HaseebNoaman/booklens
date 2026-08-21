"""Re-score images100.json without re-running anything.

Two things were wrong with the first pass and both were mine, not the app's:

1. manifest.csv quotes titles that contain commas ("Thinking, fast and slow").
   Splitting on "," turned the title into `"Thinking` and the author into
   ` fast and slow",Daniel Kahneman`, so two covers the app got RIGHT were
   scored as failures. Parsed with the csv module now.

2. A single fuzzy ratio cannot separate "same book, extra words" from
   "different book, similar words":
       "Brave new world /by Aldous Huxley"  -> same book (extra = the author)
       "The Alchemist Cocktail Book"        -> different book
   token_set_ratio calls both 100; token_sort_ratio calls both ~65. So neither
   verdict is automatic. Everything ambiguous is put in REVIEW and read by
   hand rather than being silently bucketed to make a number look better.
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
IMAGES = os.path.join(HERE, "covers")
MANIFEST = os.path.join(HERE, "manifest.csv")
sys.path.insert(0, APP)
from rapidfuzz import fuzz



def manifest():
    rows = {}
    with open(MANIFEST, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows[row["file"]] = (row["title"], row["author"])
    return rows


def main_title(value):
    return (value or "").split(":")[0].strip().lower()


def score(shown, expected):
    e, g = main_title(expected), main_title(shown)
    strict = max(fuzz.ratio(e, g), fuzz.token_sort_ratio(e, g))
    loose = fuzz.token_set_ratio(e, g)
    # A colon-suffixed title ("It's Trevor Noah: Born a Crime") loses the real
    # title to main_title(), so compare the whole strings too.
    whole = fuzz.token_set_ratio((expected or "").lower(), (shown or "").lower())
    return strict, max(loose, whole)


def verdict_for(row, expected_title, expected_author):
    if not row.get("shown"):
        return "refused", 0, 0
    strict, loose = score(row.get("top"), expected_title)
    if strict >= 80:
        return "correct", strict, loose
    if loose >= 80:
        return "review", strict, loose
    return "wrong", strict, loose


def main():
    wanted = manifest()
    label = sys.argv[1] if len(sys.argv) > 1 else "images100"
    with open(os.path.join(HERE, label + ".json"), encoding="utf-8") as fh:
        rows = json.load(fh)

    out = []
    for row in rows:
        exp_t, exp_a = wanted[row["file"]]
        v, strict, loose = verdict_for(row, exp_t, exp_a)
        row = dict(row)
        row["expected_title"] = exp_t
        row["expected_author"] = exp_a
        row["verdict"] = v
        row["strict"] = round(strict, 1)
        row["loose"] = round(loose, 1)
        row["best_in_list"] = round(max(
            (score(t, exp_t)[1] for t in (row.get("shown") or [])), default=0), 1)
        out.append(row)

    with open(os.path.join(HERE, label + "_rescored.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    counts = {}
    for row in out:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print("after fixing the manifest parse:")
    for k in ("correct", "review", "wrong", "refused"):
        print("   %-8s %d" % (k, counts.get(k, 0)))
    print()
    print("REVIEW -- these need a human decision:")
    for row in out:
        if row["verdict"] == "review":
            print("  %-16s expected %-34s" % (row["file"], row["expected_title"][:34]))
            print("      card shows : %s   [by %s]" % (
                row["top"], (row.get("shown_authors") or [None])[0]))
            print("      strict=%s loose=%s" % (row["strict"], row["loose"]))
    print()
    print("WRONG but the right book WAS in the offered list:")
    for row in out:
        if row["verdict"] == "wrong" and row["best_in_list"] >= 80:
            print("  %-16s expected %-30s card=%s" % (
                row["file"], row["expected_title"][:30], row["top"]))


if __name__ == "__main__":
    main()
