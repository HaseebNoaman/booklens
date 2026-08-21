"""How many people have actually read each catalogue book.

"Keep the famous ones" is only a rule if famous is a number. Open Library
publishes how many readers have shelved a work, and `livesignals` already
fetches it, already guards it with `titles_agree` so a sequel's or a biography's
numbers cannot be attributed to this book, and already caches through
`disk_cache`.

This is the popularity data the starter shelf did NOT need -- there the
catalogue turned out to be famous books already. Here it decides which books
stay, so it has to be measured rather than assumed.

    python curate/audit_fame.py

A rejection by `titles_agree` is a RESULT, not an error: it means Open Library
answered with something that is not this book. Those are recorded with what came
back instead, because a wrong rejection would quietly cost a good book its place.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

# Must be unset, or every fetch below returns nothing and the whole shelf looks
# equally unread.
os.environ.pop("BOOKLENS_NO_LIVE_FETCH", None)

# The providers need their keys, and only app.py ever loaded .env. Without this
# every Google Books lookup returns "API key not set" and the audit silently
# measures Open Library alone -- which is not where most publisher blurbs live.
from dotenv import load_dotenv                       # noqa: E402
load_dotenv(os.path.join(APP, ".env"))

import database                                    # noqa: E402
import livesignals                                 # noqa: E402

OUT = os.path.join(HERE, "fame_audit.json")


def what_open_library_returned(title, author):
    """For the audit trail: the record the guard rejected, if any."""
    try:
        document = livesignals._fetch(title, author)
    except Exception:
        return ""
    return (document or {}).get("title", "") if document else ""


def main():
    rows = [dict(r) for r in database.list_catalogue("VERIFIED")]
    done = []
    for index, row in enumerate(rows, 1):
        title, author = row["title"], row.get("author") or ""
        try:
            signals = livesignals.fetch_live_signals(title, author)
        except Exception:
            signals = None

        if signals:
            record = {"id": row["id"], "title": title, "author": author,
                      "on_shelves": signals.get("on_shelves") or 0,
                      "n_ratings": signals.get("n_ratings") or 0,
                      "rating": signals.get("rating"),
                      "matched_title": title, "rejected": False}
        else:
            record = {"id": row["id"], "title": title, "author": author,
                      "on_shelves": None, "n_ratings": None, "rating": None,
                      "matched_title": what_open_library_returned(title, author),
                      "rejected": True}

        done.append(record)
        print("%3d/%d  %-38s %s" % (index, len(rows), title[:38],
                                    "REJECTED (%s)" % record["matched_title"][:28]
                                    if record["rejected"]
                                    else "%d shelves" % record["on_shelves"]),
              flush=True)
        if index % 25 == 0:
            save(done)

    save(done)
    report(done)


def save(done):
    with io.open(OUT, "w", encoding="utf-8") as handle:
        json.dump(done, handle, indent=2, ensure_ascii=False)


def report(done):
    import statistics
    print("\n--- fame audit ---")
    got = [r for r in done if not r["rejected"]]
    rejected = [r for r in done if r["rejected"]]
    print("numbers for %d of %d; guard rejected %d" % (len(got), len(done),
                                                       len(rejected)))
    if got:
        shelves = sorted(r["on_shelves"] for r in got)
        print("on_shelves: min %d  q1 %d  median %d  q3 %d  max %d"
              % (shelves[0], shelves[len(shelves)//4], statistics.median(shelves),
                 shelves[3*len(shelves)//4], shelves[-1]))
        top = sorted(got, key=lambda r: -r["on_shelves"])[:30]
        print("\nmost read:")
        for r in top:
            print("   %6d  %s" % (r["on_shelves"], r["title"][:44]))
        bottom = sorted(got, key=lambda r: r["on_shelves"])[:30]
        print("\nleast read:")
        for r in bottom:
            print("   %6d  %s" % (r["on_shelves"], r["title"][:44]))
    print("\nrejected by the title guard (check these -- a wrong rejection "
          "costs a good book its place):")
    for r in rejected:
        print("   %-40s open library said: %s" % (r["title"][:40],
                                                  r["matched_title"][:40]))


if __name__ == "__main__":
    main()
