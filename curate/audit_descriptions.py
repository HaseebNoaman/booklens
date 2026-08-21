"""Two candidate descriptions per book, judged by the same gate.

Every catalogue book carries a `short_summary` derived from the CMU Book Summary
Corpus, and nobody ever checked it against what the provider record says.
Treasure Island's entire 1,402-word CMU summary never mentions pirates or
treasure -- the premise sentence simply is not in the source, so no selection
rule and no model could have found it. The publisher's blurb says it in a line.

So: run BOTH texts through `select_what_its_about()` -- the same windowing, the
same quality gate, the same scoring the app already uses on provider text -- and
record which one wins and by how much. Nothing here decides anything on its own;
the winners get read by hand afterwards, because step 6 is the standing reminder
that an automatic winner can be House of Leaves.

    python curate/audit_descriptions.py                  # one process
    python curate/audit_descriptions.py --shard 0 --of 5  # x5, in parallel
    python curate/audit_descriptions.py --merge           # stitch the shards
"""
import argparse
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

# The providers need their keys, and only app.py ever loaded .env. Without this
# every Google Books lookup returns "API key not set" and the audit silently
# measures Open Library alone -- which is not where most publisher blurbs live.
from dotenv import load_dotenv                       # noqa: E402
load_dotenv(os.path.join(APP, ".env"))

import database                                    # noqa: E402
import whatitsabout_heuristic as wia               # noqa: E402

OUT = os.path.join(HERE, "description_audit.json")

# Sharding exists because this is the slow audit: every book costs several
# provider round-trips, and one process needed the better part of three hours.
# Workers take every Nth book, so each shard covers the whole shelf rather than
# one alphabetical corner, and disk_cache writes one file per key, so concurrent
# workers cannot collide.
PART = os.path.join(HERE, "description_audit.part%d.json")


def as_book(row):
    """The catalogue row in the shape collect_exact_provider_sources reads.

    Catalogue rows store `open_library_work_id`; the collector looks for
    `open_library_key` as well, and reaches Google through the ISBN because no
    catalogue row carries a Google volume id.
    """
    return {
        "title": row["title"],
        "author": row["author"],
        "isbn_13": row.get("isbn_13") or "",
        "isbn_10": row.get("isbn_10") or "",
        "google_books_id": row.get("google_volume_id") or "",
        "open_library_edition_id": row.get("open_library_edition_id") or "",
        "open_library_work_id": row.get("open_library_work_id") or "",
        "open_library_key": row.get("open_library_work_id") or "",
        "categories": row.get("genres") or "",
    }


def judge_stored(row):
    """The stored summary through the provider pipeline, so both are comparable.

    Calling score_candidate() directly would score the whole blob; the app never
    does that. It cleans, splits, windows, and scores each window. Feeding the
    stored text in as a source is the only way the two numbers mean the same
    thing.
    """
    text = (row.get("short_summary") or "").strip()
    if not text:
        return {"status": "unavailable", "reason": "no_stored_summary",
                "text": "", "score": None, "words": 0}
    result = wia.select_what_its_about(
        [{"text": text, "source": "catalogue_short_summary",
          "verification": "catalogue_verified"}],
        title=row["title"], categories=row.get("genres") or "")
    return {"status": result.get("status"),
            "reason": result.get("reason", ""),
            "text": result.get("overview", ""),
            "full_text": text,
            "score": result.get("score"),
            "words": result.get("word_count", 0),
            "why_won": result.get("why_won", "")}


def judge_provider(book):
    try:
        result = wia.build_external_overview(book)
    except Exception as error:
        return {"status": "error", "reason": type(error).__name__,
                "text": "", "score": None, "words": 0}
    return {"status": result.get("status"),
            "reason": result.get("reason", ""),
            "source": result.get("source", ""),
            "text": result.get("overview", ""),
            "full_text": result.get("source_text", ""),
            "score": result.get("score"),
            "words": result.get("word_count", 0),
            "why_won": result.get("why_won", "")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--of", type=int, default=1)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()

    if args.merge:
        return merge()

    everything = [dict(r) for r in database.list_catalogue("VERIFIED")]
    rows = [r for i, r in enumerate(everything) if i % args.of == args.shard]
    out = OUT if args.of == 1 else PART % args.shard
    done = []
    for index, row in enumerate(rows, 1):
        stored = judge_stored(row)
        provider = judge_provider(as_book(row))

        ready = {"stored": stored["status"] == "ready",
                 "provider": provider["status"] == "ready"}
        if ready["stored"] and ready["provider"]:
            winner = ("provider" if (provider["score"] or 0) > (stored["score"] or 0)
                      else "stored")
            margin = abs((provider["score"] or 0) - (stored["score"] or 0))
        elif ready["stored"]:
            winner, margin = "stored", 0
        elif ready["provider"]:
            winner, margin = "provider", 0
        else:
            winner, margin = "neither", 0

        done.append({"id": row["id"], "title": row["title"],
                     "stored": stored, "provider": provider,
                     "winner": winner, "margin": margin})
        print("%3d/%d  %-36s %-8s (stored %s / provider %s)"
              % (index, len(rows), row["title"][:36], winner,
                 stored["score"], provider["score"]), flush=True)
        if index % 10 == 0:
            save(done, out)

    save(done, out)
    if args.of == 1:
        report(done)
    else:
        print("shard %d of %d done: %d books -> %s"
              % (args.shard, args.of, len(done), out))


def save(done, path=OUT):
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(done, handle, indent=2, ensure_ascii=False)


def merge():
    """Stitch the shards back together, in catalogue order."""
    merged = []
    for path in sorted(glob.glob(os.path.join(HERE,
                                              "description_audit.part*.json"))):
        with io.open(path, encoding="utf-8") as handle:
            merged.extend(json.load(handle))
    order = {r["id"]: i for i, r in
             enumerate(dict(x) for x in database.list_catalogue("VERIFIED"))}
    merged.sort(key=lambda r: order.get(r["id"], 10 ** 6))
    save(merged, OUT)
    print("merged %d books -> %s" % (len(merged), OUT))
    report(merged)


def report(done):
    import collections
    print("\n--- description audit ---")
    print("winners:", dict(collections.Counter(r["winner"] for r in done)))
    reasons = collections.Counter(r["provider"].get("reason") or "-"
                                  for r in done if r["provider"]["status"] != "ready")
    print("provider unavailable, by reason:", dict(reasons))
    swings = sorted((r for r in done if r["winner"] == "provider" and r["margin"]),
                    key=lambda r: -r["margin"])[:20]
    print("\nbiggest provider wins:")
    for r in swings:
        print("   +%-3s %-30s %s" % (r["margin"], r["title"][:30],
                                     r["provider"]["text"][:78]))
    holds = sorted((r for r in done if r["winner"] == "stored" and r["margin"]),
                   key=lambda r: -r["margin"])[:20]
    print("\nbiggest stored wins:")
    for r in holds:
        print("   +%-3s %-30s %s" % (r["margin"], r["title"][:30],
                                     r["stored"]["text"][:78]))


if __name__ == "__main__":
    main()
