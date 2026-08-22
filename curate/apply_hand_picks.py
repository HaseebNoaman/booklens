"""Put the hand-chosen sentences on the shelf.

Some books defeated the automatic gate from both sides: the publisher had no
usable description and the corpus summary opened on the book's structure rather
than its story. Their cards were still showing FLAN-T5 output -- the model was
removed from the product months ago, but its writing stayed in the database.

curate/hand_picked.json names, for each of those books, WHICH SENTENCES of its
own source summary to show. By index, not by text: the sentences are read out of
the database at run time, so nothing here can quietly differ from what the
corpus actually says. That is the whole point -- a person chose the sentences,
and a person did not write them.

    python curate/apply_hand_picks.py            # show what would change
    python curate/apply_hand_picks.py --confirm  # write it
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

import database                                    # noqa: E402
import whatitsabout_heuristic as wia               # noqa: E402

PICKS = os.path.join(HERE, "hand_picked.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    with io.open(PICKS, encoding="utf-8") as handle:
        picks = json.load(handle)["picks"]

    shelf = {r["id"]: dict(r) for r in database.list_catalogue("VERIFIED")}
    applied = 0
    for key, indices in picks.items():
        row = shelf.get(int(key))
        if row is None:
            print("   id %s is not on the shelf any more -- skipped" % key)
            continue
        sentences = wia.split_sentences(row["verified_summary"] or "")
        if max(indices) >= len(sentences):
            print("   %-38s index out of range (%d sentences)"
                  % (row["title"][:38], len(sentences)))
            continue
        text = " ".join(" ".join(sentences[i].split()) for i in indices)
        print("   %-38s %s" % (row["title"][:38], text[:70]))
        applied += 1
        if args.confirm:
            database.update_catalogue_book(int(key), {
                "short_summary": text,
                "short_summary_status": "ok",
                "short_summary_method": "catalogue_corpus",
                "short_summary_model": "",
                "short_summary_source_sha256":
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }, None)

    print("\n%d books" % applied)
    if not args.confirm:
        print("Dry run. Nothing was written. Add --confirm to apply.")


if __name__ == "__main__":
    main()
