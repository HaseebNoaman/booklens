"""Bring the shelf's covers into the repository, once.

Every card, every Browse row and every starter-shelf tile was fetching its
cover from covers.openlibrary.org at render time. That is a request per book
per page view to somebody else's server, for images that never change, and it
fails in three ways we have already seen: the edition URL 404s for a third of
the shelf, the whole service can be slow, and the day Google's quota ran out
was a reminder that a free provider is not a dependency you control.

The verified shelf is small and fixed. So its covers are downloaded once and
committed -- 60 files, well under 2 MB -- and after that a catalogue card needs
no network at all: the description is in the database, the subjects are in the
database, and now the cover is on disk beside them.

    python curate/fetch_covers.py

Covers go to catalogue_covers/<catalogue id>.jpg and are served by app.py's
/covers/<id> route. They are deliberately NOT in frontend/dist -- the Docker
build rebuilds the frontend from source and would delete them.

Books outside the catalogue are unaffected: their covers still come from the
provider that identified them, because there is no fixed set to download.
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

import database                                    # noqa: E402

OUT_DIR = os.path.join(APP, "catalogue_covers")
AUDIT = os.path.join(HERE, "cover_audit.json")
AGENT = "BookLens/1.0 catalogue-cover-fetch"


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read()
    if not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
        raise ValueError("not an image")
    if len(data) < 2000:
        raise ValueError("%d bytes, too small to be a cover" % len(data))
    return data


def work_cover(work_id):
    """The fourth route: the WORK's own cover ids.

    The audit tries the edition OLID, the ISBN, and Google's thumbnail. Two
    books on the shelf -- Pride and Prejudice and The Grapes of Wrath, both of
    them benchmark covers we cannot drop -- failed all three, while Open
    Library's work record lists five cover ids for each. Editions come and go;
    the work outlives them.

    Note the id has to be stripped: it is stored as "/works/OL66554W", and
    pasting that into the URL asks for /works//works/OL66554W.json.
    """
    work_id = (work_id or "").strip().rstrip("/").split("/")[-1]
    if not work_id:
        return b""
    url = "https://openlibrary.org/works/%s.json" % work_id
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        document = json.load(response)
    for cover_id in [c for c in (document.get("covers") or []) if c and c > 0][:3]:
        try:
            return download("https://covers.openlibrary.org/b/id/%d-M.jpg" % cover_id)
        except (urllib.error.URLError, ValueError, OSError):
            continue
    return b""


def main():
    if not os.path.exists(AUDIT):
        sys.exit("No cover_audit.json -- run curate/audit_covers.py first.")
    with io.open(AUDIT, encoding="utf-8") as handle:
        audited = {row["id"]: row for row in json.load(handle)}

    shelf = [dict(r) for r in database.list_catalogue("VERIFIED")]
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    saved = skipped = failed = 0
    missing = []
    for index, row in enumerate(shelf, 1):
        target = os.path.join(OUT_DIR, "%d.jpg" % row["id"])
        if os.path.exists(target) and os.path.getsize(target) > 2000:
            skipped += 1
            continue
        url = (audited.get(row["id"]) or {}).get("url") or ""
        data, how = b"", ""
        if url:
            try:
                data, how = download(url), "audited url"
            except (urllib.error.URLError, ValueError, OSError):
                data = b""
        if not data:
            try:
                data, how = work_cover(row.get("open_library_work_id")), "work record"
            except (urllib.error.URLError, ValueError, OSError, json.JSONDecodeError):
                data = b""
        if not data:
            failed += 1
            missing.append(row["title"])
            print("%3d/%d  %-40s no cover from any route"
                  % (index, len(shelf), row["title"][:40]), flush=True)
            continue
        with io.open(target, "wb") as handle:
            handle.write(data)
        saved += 1
        print("%3d/%d  %-40s %5.1f KB  (%s)"
              % (index, len(shelf), row["title"][:40], len(data) / 1024.0, how),
              flush=True)
        time.sleep(0.15)

    total = sum(os.path.getsize(os.path.join(OUT_DIR, name))
                for name in os.listdir(OUT_DIR) if name.endswith(".jpg"))
    print("\nsaved %d, already had %d, missing %d" % (saved, skipped, failed))
    print("catalogue_covers/ holds %d files, %.1f MB"
          % (len([n for n in os.listdir(OUT_DIR) if n.endswith(".jpg")]),
             total / 1048576.0))
    if missing:
        print("\nno local cover (these fall back to Open Library, then to the "
              "placeholder):")
        for title in missing:
            print("   ", title)


if __name__ == "__main__":
    main()
