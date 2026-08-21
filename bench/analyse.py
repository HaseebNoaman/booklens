"""Sort every failure into a cause, so "is it fixable?" has an answer per cover.

Reads images100.json and prints, for each cover that did not show the right
book, what the OCR actually read and where the funnel went. The causes are
deliberately coarse -- they map onto things that could actually be changed:

  ocr_blind        nothing usable came off the image at either recogniser.
                   No query strategy helps; only a better detector would.
  title_not_read   the recognisers read text, but the title words are not in
                   it. Same conclusion as above.
  provider_missing the title WAS read, and the providers still did not return
                   the book. Fixable in the query, not the image.
  ranking          the right book was in the candidate list but not first.
                   Fixable in ranking or by showing the list.
  edition          right work, wrong edition or language.
  scorer           the app was right and this measurement was wrong.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
IMAGES = os.path.join(HERE, "covers")
MANIFEST = os.path.join(HERE, "manifest.csv")
sys.path.insert(0, APP)
from rapidfuzz import fuzz



def title_words_present(expected, text):
    """Did the recogniser actually read the title off the cover?"""
    words = [w for w in expected.lower().replace("-", " ").split()
             if len(w) > 3]
    if not words:
        words = expected.lower().split()
    text = (text or "").lower()
    hits = sum(1 for w in words
               if w in text or fuzz.partial_ratio(w, text) >= 85)
    return hits, len(words)


def classify(row):
    expected = row["expected_title"]
    all_text = " ".join((p.get("full") or "") + " " + (p.get("title") or "")
                        for p in row.get("ocr_passes", []))
    hits, total = title_words_present(expected, all_text)

    if row["best_in_list"] >= 80 and row["verdict"] != "correct":
        return "ranking", hits, total
    if not all_text.strip():
        return "ocr_blind", hits, total
    if total and hits / float(total) < 0.5:
        return "title_not_read", hits, total
    if row["verdict"] == "refused":
        return "provider_missing", hits, total
    return "wrong_book_shown", hits, total


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "images100"
    with open(os.path.join(HERE, label + ".json"), encoding="utf-8") as fh:
        rows = json.load(fh)

    buckets = {}
    for row in rows:
        if row["verdict"] == "correct":
            continue
        cause, hits, total = classify(row)
        buckets.setdefault(cause, []).append((row, hits, total))

    print("=" * 78)
    print("FAILURES BY CAUSE  (%d covers, %d correct)" % (
        len(rows), sum(r["verdict"] == "correct" for r in rows)))
    print("=" * 78)
    for cause in sorted(buckets, key=lambda c: -len(buckets[c])):
        items = buckets[cause]
        print()
        print("--- %s : %d ---" % (cause.upper(), len(items)))
        for row, hits, total in items:
            print("  %-16s %-8s expected %-30s" % (
                row["file"], row["verdict"], row["expected_title"][:30]))
            print("      shown : %s" % (row["top"] or "(nothing)"))
            print("      title words read: %d/%d | top_score %s | best_in_list %s"
                  % (hits, total, row["top_score"], row["best_in_list"]))
            for p in row.get("ocr_passes", []):
                print("      ocr[%s] %s | title=%r" % (
                    p["tier"], p["status"], p["title"][:70]))
            if len(row.get("shown") or []) > 1:
                print("      list  : %s" % row["shown"][:5])


if __name__ == "__main__":
    main()
