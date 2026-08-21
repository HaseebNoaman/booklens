"""Which catalogue books actually have a cover, and by which route.

`catalogue_cover()` asks Open Library for the cover of the EDITION we stored.
Measured on 40 books, that URL 404s for 35% of the shelf -- so a third of Browse
and a third of the starter shelf showed "Cover unavailable". Six of those
fourteen have a perfectly good cover filed under their ISBN instead, including
The Da Vinci Code. Dropping a book for a URL we were asking for wrongly would
have been the wrong call, which is why this audit runs before any book is cut.

    python curate/audit_covers.py

A route counts only if the bytes coming back are a real image. HEAD is useless
here: Open Library answers 200 with no body for a missing cover, which is how an
earlier probe concluded that 0 of 40 covers loaded.
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

# The providers need their keys, and only app.py ever loaded .env. Without this
# every Google Books lookup returns "API key not set" and the audit silently
# measures Open Library alone -- which is not where most publisher blurbs live.
from dotenv import load_dotenv                       # noqa: E402
load_dotenv(os.path.join(APP, ".env"))

import database                                    # noqa: E402

OUT = os.path.join(HERE, "cover_audit.json")
COVER = "https://covers.openlibrary.org/b/%s/%s-M.jpg?default=false"
AGENT = "BookLens/1.0 catalogue-audit"
MIN_BYTES = 2000


def fetches(url):
    """True only for a real image body, with its size."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(8192)
            magic = body[:3] == b"\xff\xd8\xff" or body[:4] == b"\x89PNG"
            if response.status == 200 and magic and len(body) >= MIN_BYTES:
                return True, len(body)
            return False, len(body)
    except urllib.error.HTTPError as error:
        return False, -error.code
    except Exception:
        return False, 0


def google_thumbnail(isbn):
    """The third route. Needs a real lookup, so its URL has to be STORED --
    a card cannot make a network call per cover while rendering a grid."""
    if not isbn:
        return ""
    try:
        from api import search_by_isbn
        book = search_by_isbn(isbn)
    except Exception:
        return ""
    if not isinstance(book, dict) or book.get("error"):
        return ""
    return (book.get("thumbnail") or "").strip()


def main():
    rows = [dict(r) for r in database.list_catalogue("VERIFIED")]
    done = []
    for index, row in enumerate(rows, 1):
        isbn = (row.get("isbn_13") or "").strip()
        olid = (row.get("open_library_edition_id") or "").strip()
        record = {"id": row["id"], "title": row["title"], "isbn_13": isbn,
                  "olid": olid, "route": "none", "url": "", "bytes": 0}

        for route, url in (("olid", COVER % ("olid", olid) if olid else ""),
                           ("isbn", COVER % ("isbn", isbn) if isbn else "")):
            if not url:
                continue
            ok, size = fetches(url)
            if ok:
                record.update(route=route, url=url, bytes=size)
                break

        if record["route"] == "none":
            thumbnail = google_thumbnail(isbn)
            if thumbnail:
                ok, size = fetches(thumbnail)
                if ok:
                    record.update(route="google", url=thumbnail, bytes=size)

        done.append(record)
        print("%3d/%d  %-38s %s" % (index, len(rows), row["title"][:38],
                                    record["route"]), flush=True)
        if index % 25 == 0:
            save(done)
        time.sleep(0.15)          # be a polite guest on somebody else's server

    save(done)
    report(done)


def save(done):
    with io.open(OUT, "w", encoding="utf-8") as handle:
        json.dump(done, handle, indent=2, ensure_ascii=False)


def report(done):
    print("\n--- cover audit ---")
    for route in ("olid", "isbn", "google", "none"):
        n = sum(1 for r in done if r["route"] == route)
        print("   %-7s %3d  (%d%%)" % (route, n, n * 100 // max(len(done), 1)))
    missing = [r["title"] for r in done if r["route"] == "none"]
    print("\nno cover at all (%d):" % len(missing))
    for title in missing:
        print("   ", title)


if __name__ == "__main__":
    main()
