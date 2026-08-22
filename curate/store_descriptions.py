"""Write the better of the two descriptions into the shelf, once, with its source.

A catalogue card used to show whatever the CMU corpus happened to say, because
the provider path was closed to catalogue books -- a leftover from when a model
consumed that text. Meanwhile the publisher's own blurb was often the better
sentence and nobody had ever compared them.

So both texts are scored by the same gate and the winner is stored, along with
WHERE IT CAME FROM. After this a catalogue card needs no network for its
description either: the text is in the database next to the cover on disk.

    python curate/store_descriptions.py            # show what would change
    python curate/store_descriptions.py --confirm  # write it

THE TEXT IS NEVER WRITTEN BY US. Every stored description is the publisher's
words or the corpus's, trimmed to the window the gate chose, and the source is
recorded beside it so the card can say which. Selecting between two real sources
is editorial work; composing a third would be inventing provenance, and the one
claim this project cannot afford to lose is that nothing on the card is made up.
"""
import argparse
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from dotenv import load_dotenv                     # noqa: E402
load_dotenv(os.path.join(APP, ".env"))

import database                                    # noqa: E402

AUDIT = os.path.join(HERE, "description_audit.json")

# What the card is allowed to say about where a description came from. The keys
# are the audit's source names; the values go in short_summary_method and are
# turned into a sentence by the UI.
SOURCE_LABELS = {
    "openlibrary_work": "publisher_openlibrary",
    "openlibrary_edition": "publisher_openlibrary",
    "google_volume": "publisher_google",
    "": "catalogue_corpus",
}


def chosen_text(record, row):
    """The winner, its source, and why -- or None when neither is good enough."""
    provider = record.get("provider") or {}
    stored = record.get("stored") or {}
    provider_ready = provider.get("status") == "ready"
    stored_ready = stored.get("status") == "ready"

    if provider_ready and stored_ready:
        if (provider.get("score") or 0) > (stored.get("score") or 0):
            return provider.get("text"), provider.get("source", ""), "provider scored higher"
        return stored.get("text"), "", "stored summary scored higher"
    if provider_ready:
        return provider.get("text"), provider.get("source", ""), "only the provider passed"
    if stored_ready:
        return stored.get("text"), "", "only the stored summary passed"

    # Neither passed the accept gate. The stored summary is what the card shows
    # today, so leave it exactly as it is rather than replacing it with nothing.
    return None, None, "neither passed; left untouched"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    with io.open(AUDIT, encoding="utf-8") as handle:
        audit = {row["id"]: row for row in json.load(handle)}

    shelf = [dict(r) for r in database.list_catalogue("VERIFIED")]
    changed = kept = 0
    for row in shelf:
        record = audit.get(row["id"])
        if not record:
            continue
        text, source, why = chosen_text(record, row)
        if text is None:
            kept += 1
            print("   %-38s LEFT AS IS  (%s)" % (row["title"][:38], why))
            continue
        text = " ".join(text.split())
        if text == " ".join((row.get("short_summary") or "").split()):
            kept += 1
            continue
        method = SOURCE_LABELS.get(source or "", "catalogue_corpus")
        print("   %-38s <- %-22s %s" % (row["title"][:38], method, text[:60]))
        changed += 1
        if args.confirm:
            database.update_catalogue_book(row["id"], {
                "short_summary": text,
                "short_summary_status": "ok",
                "short_summary_method": method,
                "short_summary_source_sha256":
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }, None)

    print("\n%d rewritten, %d already correct or left alone" % (changed, kept))
    if not args.confirm:
        print("Dry run. Nothing was written. Add --confirm to apply.")


if __name__ == "__main__":
    main()
