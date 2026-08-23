# livesignals.py
# The things about a book that are TRUE TODAY, from Open Library.
#
# WHY THIS EXISTS. The card could say what a book is and whether it resembles
# what the reader has read, but never whether anyone thought it was any good --
# the question a person in a bookshop actually asks. Open Library carries a
# rating and a reading-log count that update continuously, are free, need no API
# key, and reach books published this year.
#
# MEASURED BEFORE BUILDING, on the 100-cover benchmark set: Open Library knows
# 98 of them and 98 carry a rating, median 117 raters, 96 with at least 20. On
# 100 books published in 2026 it knows 86 and rates 0 -- so a new book has no
# rating anywhere, and the card must say that rather than invent one.
#
# Google's rating was measured too and is NOT used: it exists for 6.7% of books,
# and averaging two different rating populations produces a number that means
# nothing.
#
# THREE SIGNALS, NEVER MERGED INTO ONE NUMBER:
#
#   rating       QUALITY. "4.1 out of 5 from 140 readers."
#   shelf count  DEMAND, NOT QUALITY. A book everyone means to read is not a
#                book everyone enjoyed. Conflating them would be exactly the
#                quiet dishonesty this project avoids, so it is labelled as
#                popularity and shown only when there is no rating -- which for
#                a brand-new book is the only signal that exists.
#   page count   used ONLY when the stored one is missing or absurd. Life of Pi
#                was stored as 81 pages and advertised as "about 1.5 hours" for
#                a 350-page novel, because Google returned whichever edition it
#                happened to hold. A median across editions cannot be moved by
#                one odd record.
#
# Every response goes through the existing disk_cache, so a repeat scan costs
# nothing and a demo with no network still works.
import logging
import os
import re
import time

from thefuzz import fuzz

import database
import disk_cache

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"

LIVE_FIELDS = ("key,title,author_name,first_publish_year,"
               "ratings_average,ratings_count,readinglog_count,"
               "want_to_read_count,already_read_count,number_of_pages_median")

# How closely Open Library's title must agree with the one we asked about.
# A little looser than an exact match because Open Library often carries a
# subtitle we do not.
TITLE_AGREEMENT = 82

# Words a publisher adds without changing which book it is. Stripped from both
# sides, so "Life of Pi" can agree with "Life of Pi: A Novel" without the
# comparison having to be loose in general.
_SUBTITLE_NOISE = {"a", "an", "the", "novel", "book", "edition", "unabridged",
                   "abridged", "illustrated", "classic", "classics",
                   "paperback", "hardcover", "anniversary", "reissue"}

_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_SPACE_RE = re.compile(r"\s+")
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an) ")

# A colon usually separates a title from its subtitle, but only when there is a
# real title in front of it. Splitting "1984: the classic" is right; splitting
# "It: a novel" on a two-character head is not.
MIN_TITLE_BEFORE_COLON = 12

# A stored signal older than this is refetched rather than served. The whole
# value of this data is the claim "checked recently"; serving a month-old row
# under that label would make the freshness stamp a lie.
MAX_AGE_SECONDS = 7 * 24 * 3600

# Below this many raters the average is noise. Still shown, with the count
# beside it, so the reader can judge -- but never leaned on.
THIN_RATING_BELOW = 5

# Outside this range a page count is a data error, not a book: audiobook stubs
# and box sets both live there.
MIN_REAL_PAGES = 40
MAX_REAL_PAGES = 2000


def normalise_title(title):
    """"The Great Gatsby (Penguin Classics)" -> "great gatsby"."""
    text = (title or "").lower()
    text = _BRACKETS_RE.sub(" ", text)
    if ":" in text:
        head = text.split(":", 1)[0].strip()
        if len(head) >= MIN_TITLE_BEFORE_COLON:
            text = head
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    text = _LEADING_ARTICLE_RE.sub("", text)
    return text.strip()


def titles_agree(asked, returned, author=""):
    """Is the book Open Library answered with the book we asked about?

    token_SORT_ratio, not token_SET_ratio. The set variant treats a subset as a
    perfect match, so "Dune" scored 100 against "Dune Messiah" -- a different
    novel in the same series, whose rating would then have been shown as Dune's.
    Sorting compares the whole strings, so extra meaningful words count against
    the match while the noise stripped below stays harmless.
    """
    a, b = normalise_title(asked), normalise_title(returned)
    if not a or not b:
        return False

    # Open Library often files a book under "Title Author Name" -- Becoming is
    # returned as "Becoming Michelle Obama". Appending the author does not make
    # it a different book, so those tokens are dropped from the RETURNED title
    # before comparing. Measured: this was 1 of the 2 rejections in 70 books.
    #
    # This cannot reopen the Dune / Dune Messiah hole, because "Messiah" is not
    # the author's name. Only the author's own tokens are removed, and only
    # from the side that added them.
    # Only the author words Open Library ADDED, though -- not the ones the book
    # is actually called. Stripping the author's name unconditionally rejected
    # "The Autobiography of Benjamin Franklin" against ITSELF: the returned
    # title lost both names and became "the autobiography of", which no longer
    # resembled what we asked for. Any book with its author in the title lost
    # its rating that way.
    author_tokens = set(normalise_title(author).split()) if author else set()
    added_by_provider = author_tokens - set(a.split())
    drop = _SUBTITLE_NOISE | added_by_provider
    strip = lambda t, extra: " ".join(w for w in t.split() if w not in extra)
    return fuzz.token_sort_ratio(strip(a, _SUBTITLE_NOISE),
                                 strip(b, drop)) >= TITLE_AGREEMENT


# How many search results to consider. One is not enough: Open Library holds
# several records for a popular book, and the first is not always the one
# readers actually rated. Asking for Becoming returned a duplicate record with
# 14 shelvings and no rating at all, while Michelle Obama's memoir has
# thousands -- so the card would have said "no rating yet" about one of the
# most-rated memoirs in the catalogue.
SEARCH_RESULTS = 5


def _search(query, cache_key):
    """One Open Library search, cached. The docs list, or empty."""
    data = disk_cache.fetch_json(
        "ol_live", cache_key, OPEN_LIBRARY_SEARCH_URL,
        params={"q": query, "limit": SEARCH_RESULTS, "fields": LIVE_FIELDS},
        timeout=10, headers={"User-Agent": "BookLens/1.0"})
    if not data:
        return []
    return data.get("docs") or []


def _best_agreeing(docs, title, author):
    """Among the records that ARE this book, the one the most readers rated.

    That is the canonical work record; the others are duplicates and single
    editions. Records that are NOT this book are discarded here rather than
    later, so the choice is only ever made between right answers.
    """
    agreeing = [d for d in docs if titles_agree(title, d.get("title", ""), author)]
    if not agreeing:
        return None
    agreeing.sort(key=lambda d: (int(d.get("ratings_count") or 0),
                                 int(d.get("readinglog_count") or 0)),
                  reverse=True)
    return agreeing[0]


def _fetch(title, author="", isbn=""):
    # The test suite must not reach Open Library. Left unguarded it added a
    # real HTTP round-trip to every card built in a test, which is slow,
    # flaky, and depends on somebody else's uptime to decide whether this
    # project's tests pass. Same reason conftest forces the mail provider off.
    if os.environ.get("BOOKLENS_NO_LIVE_FETCH") == "1":
        return None

    # ASK BY ISBN FIRST, WHEN THERE IS ONE.
    #
    # A title query can be answered entirely by books ABOUT the book. Asking
    # for "The 10X Rule Grant Cardone" returns five summary-mill products
    # (pressprint, Bookhabits, Instaread and two more); not one carries a
    # rating, and titles_agree correctly rejects all five -- so the card fell
    # silent about a book Open Library rates with 14 ratings and 447 shelves.
    # An ISBN cannot be answered that way, because it names one edition.
    #
    # FIRST, not INSTEAD. Measured on 20 cached books that have a stored ISBN:
    # title alone found a rating for 17, ISBN alone for 14, ISBN-then-title for
    # 18. ISBN is the better first question and the worse only question -- an
    # ISBN identifies an edition, while the ratings are held on the work.
    #
    # Its answer goes through the same agreement guard as the title path, so a
    # wrong or mis-filed ISBN is rejected exactly as a wrong title is. Only 44
    # of the 155 cached books have an ISBN at all; the rest fall straight
    # through to the query below, unchanged.
    if isbn:
        doc = _best_agreeing(
            _search(isbn, "isbn:%s|n=%d" % (isbn, SEARCH_RESULTS)), title, author)
        if doc is not None:
            return doc

    query = ("%s %s" % (title, author)).strip()
    if not query:
        return None
    # The cache key carries the result count. disk_cache keys on the string it
    # is given, so raising the limit while reusing the old key served the old
    # single-result payload and the change looked like it had done nothing.
    docs = _search(query, "%s|n=%d" % (query, SEARCH_RESULTS))
    doc = _best_agreeing(docs, title, author)
    if doc is not None:
        return doc
    # Hand back the first anyway: fetch_live_signals runs the same check and
    # will reject it, and keeping one path for that decision means the
    # rejection is logged in one place.
    return docs[0] if docs else None


def fetch_live_signals(title, author="", isbn=""):
    """A plain dict, or None when Open Library knows nothing.

    Never raises. A live signal is a bonus; identifying a book must not fail
    because a free third-party service is having a bad day.
    """
    try:
        doc = _fetch(title, author, isbn)
    except Exception:                                          # noqa: BLE001
        logging.warning("Open Library live lookup failed for %r", title)
        return None
    if not doc:
        return None

    # THE RETURNED BOOK MUST ACTUALLY BE THE BOOK WE ASKED FOR.
    #
    # Open Library's search always answers: it ranks whatever it has and hands
    # back a first result even for a query it does not recognise. Without this
    # check, a book we know almost nothing about would acquire a confident
    # "4.2 from 300 readers" belonging to a different title entirely -- the
    # worst possible failure for a feature whose whole point is that its
    # numbers can be trusted.
    if not titles_agree(title, doc.get("title", ""), author):
        logging.info("Open Library returned %r for %r -- rejected as a "
                     "different book", doc.get("title", ""), title)
        return None

    rating = doc.get("ratings_average")
    n_ratings = int(doc.get("ratings_count") or 0)
    pages = int(doc.get("number_of_pages_median") or 0)
    return {
        "rating": round(float(rating), 2) if rating else None,
        "n_ratings": n_ratings,
        "want_to_read": int(doc.get("want_to_read_count") or 0),
        "already_read": int(doc.get("already_read_count") or 0),
        # DEMAND, not quality. Named so the distinction survives being copied
        # into a template by a tired person at midnight.
        "on_shelves": int(doc.get("readinglog_count") or 0),
        "page_count": pages if MIN_REAL_PAGES <= pages <= MAX_REAL_PAGES else 0,
        "source": "openlibrary",
    }


def save(book_id, signals):
    conn = database.get_db()
    conn.execute("""
        INSERT INTO live_signals
            (book_id, rating, n_ratings, want_to_read, already_read,
             on_shelves, page_count, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(book_id) DO UPDATE SET
            rating       = excluded.rating,
            n_ratings    = excluded.n_ratings,
            want_to_read = excluded.want_to_read,
            already_read = excluded.already_read,
            on_shelves   = excluded.on_shelves,
            page_count   = excluded.page_count,
            source       = excluded.source,
            fetched_at   = excluded.fetched_at
    """, (book_id, signals.get("rating"), signals.get("n_ratings", 0),
          signals.get("want_to_read", 0), signals.get("already_read", 0),
          signals.get("on_shelves", 0), signals.get("page_count", 0),
          signals.get("source", "openlibrary"), int(time.time())))
    conn.commit()
    conn.close()


def load(book_id):
    conn = database.get_db()
    try:
        row = conn.execute("SELECT * FROM live_signals WHERE book_id = ?",
                           (book_id,)).fetchone()
    except Exception:                                          # noqa: BLE001
        row = None                       # table not created yet
    conn.close()
    if row is None:
        return None
    out = dict(row)
    age = int(time.time()) - int(out.get("fetched_at") or 0)
    out["age_seconds"] = age
    out["is_stale"] = age > MAX_AGE_SECONDS
    out["rating_is_thin"] = bool(out.get("rating")) and \
        (out.get("n_ratings") or 0) < THIN_RATING_BELOW
    return out


def get(book_id, title, author="", allow_fetch=True, isbn=""):
    """What the rest of the app calls.

    Returns a fresh stored signal, refetches a stale one, and returns None
    rather than raising when there is nothing to say.
    """
    if not book_id:
        return None
    stored = load(book_id)
    if stored is not None and not stored["is_stale"]:
        return stored
    if not allow_fetch:
        return stored
    fresh = fetch_live_signals(title, author, isbn)
    if fresh is None:
        return stored                    # keep the stale one rather than lose it
    save(book_id, fresh)
    return load(book_id)


def freshness_phrase(signals):
    """The wording the card uses, decided in one place."""
    if not signals or not signals.get("fetched_at"):
        return None
    age = signals.get("age_seconds", 0)
    if age < 36 * 3600:
        return "checked today"
    days = max(1, age // 86400)
    return "checked %d day%s ago" % (days, "s" if days != 1 else "")


def for_client(signals):
    """Only what the card is allowed to show, and nothing internal.

    The rating and the shelf count are deliberately separate fields: one is
    quality, the other is demand, and the template must not be able to blur
    them by accident.
    """
    if not signals:
        return None
    rating = signals.get("rating")
    return {
        "rating": rating,
        "n_ratings": signals.get("n_ratings") or 0,
        "rating_is_thin": bool(signals.get("rating_is_thin")),
        # Shown ONLY when there is no rating -- for a book published this year
        # it is the one real signal that exists.
        "on_shelves": signals.get("on_shelves") or 0,
        "page_count": signals.get("page_count") or 0,
        "source": "Open Library",
        "freshness": freshness_phrase(signals),
    }
