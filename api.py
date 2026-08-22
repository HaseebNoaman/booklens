# api.py
# This file gets book information from the internet.
# Sources used:
#   1. Google Books API  -> book details AND the publisher description
#   2. Open Library API  -> description fallback (free, no key needed)
# We also use "fuzzy matching" so that small OCR mistakes still find the right book.
#
# WIKIPEDIA WAS REMOVED FROM THIS FILE (2026-07-26) and the reason matters.
# The app used to summarise the Wikipedia ARTICLE ABOUT THE SUBJECT rather than
# the description of the BOOK WE MATCHED. That single choice caused three
# separate failures:
#   1. A book published last month has no Wikipedia article at all, so the
#      product had no answer for new books - which is the question we were
#      asked in the examination and could not answer.
#   2. Wikipedia resolves to SERIES-level and AUTHOR-level entities. Scanning
#      "Harry Potter and the Philosopher's Stone" returned a summary of the
#      Harry Potter SERIES; "The 10X Rule" resolved to Grant Cardone's
#      biography. Guards were added for both and were still insufficient in
#      principle, because the article is simply not the book.
#   3. Wikipedia plot sections give away the ending, which is wrong for a
#      product whose job is helping someone decide whether to read the book.
# The description now always comes from the EXACT MATCHED VOLUME. When no
# source has one, we say so.
#
# It was removed from the funnel then, but the fetch itself and its two guards
# were left in the file behind no caller. They were deleted on 2026-08-23; the
# reasoning above is the part worth keeping.
#
# Choosing BETWEEN the sources below is no longer this file's job either.
# resolve_description() used to do it and was deleted on 2026-08-23 with the
# same finding: nothing had called it since whatitsabout_heuristic.py took over.
# This file now only FETCHES -- by volume id, by ISBN, by work key -- and
# build_external_overview() decides which answer a reader sees.

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import disk_cache

# The MATCHING ALGORITHM lives in matching.py; this file is the HTTP client
# layer (Google Books, Open Library). Every name defined there is
# re-exported here, so api.cleanquery, api.rank_candidates, api.T_TITLE and the
# rest keep resolving for app.py, the tests and the benchmark scripts without
# any of them having to change.
from matching import (  # noqa: F401  (imported for re-export)
    cleanquery,
    T_TITLE, T_AUTHOR, T_FIT, T_TITLE_HIGH, T_AUTHOR_HIGH,
    T_AUTHOR_HIGH_STRICT, T_AUTHOR_STRONG, T_TITLE_PRESENT,
    T_TITLE_DOMINANT, T_FIT_LOOSE, T_HIGH_ON_PROBABLE, T_TITLE_TYPED,
    DERIVED_EDITION_RE, DERIVED_EDITION_PENALTY, TITLE_ON_COVER_BONUS,
    rank_candidates, recover_ocr_candidates, valid_isbn, normalize_isbn,
)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


def _fetch_google_query(query, api_key):
    # ONE Google Books lookup, with the retry policy the scan path relies on.
    # Returns (books, api_error, quota_hit) so the caller can merge results
    # from several queries that ran at the same time.
    #
    # CACHE FIRST. The key is the exact query string, because that is what
    # determines the answer. A cached query costs no quota and works with the
    # network unplugged, which is what makes the demo unbreakable.
    cached = disk_cache.cache_get("google_query", query)
    if cached is not None:
        return [parse_book(item) for item in cached.get("items", [])], False, False

    if disk_cache.demo_offline():
        # DEMO_OFFLINE=1: no sockets. Fail loudly and name the missing key so
        # it can be pre-warmed, rather than quietly returning "no books" -
        # which would look like a matching failure instead of a setup problem.
        raise disk_cache.OfflineCacheMiss(
            f"DEMO_OFFLINE=1 and no cached Google result for query {query!r}. "
            f"Pre-warm it with test_covers/prewarm_demo_cache.py.")

    params = {
        "q": query,
        "maxResults": 5,
        "key": api_key,
        "langRestrict": "en",
        "printType": "books",
    }
    response = None
    for attempt in range(3):
        try:
            response = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=10)
            if response.status_code == 429 or response.status_code < 500:
                break
        except Exception:
            response = None
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))

    if response is None:
        return [], True, False
    if response.status_code == 429:
        return [], False, True
    if response.status_code != 200:
        return [], True, False
    try:
        data = response.json()
    except Exception:
        # Bad JSON from Google — treat like any other failed query.
        return [], True, False
    # Only a clean 200 with valid JSON reaches this line, so only a real
    # answer is ever written to disk. A 429, a 503 and a timeout all returned
    # above WITHOUT caching - see the note at the top of disk_cache.py for
    # why that distinction is the whole point.
    disk_cache.cache_put("google_query", query, data)
    return [parse_book(item) for item in data.get("items", [])], False, False


def build_search_queries(title, author="", full_text=""):
    # The EXACT list of Google queries a scan will run, most specific first.
    # Extracted so the pre-warm script can cache precisely the keys the scan
    # will ask for. Duplicating this list there would drift, and a drifted
    # pre-warm produces a cache that looks complete and fails offline.
    clean_title = cleanquery(title)
    clean_author = cleanquery(author)
    if not clean_title:
        return []

    queries = []
    if clean_author:
        queries.append(f"intitle:{clean_title} inauthor:{clean_author}")
    queries.append(f"intitle:{clean_title}")
    queries.append(clean_title)
    # LAST resort: throw ALL the text we read off the cover at Google. This
    # rescues messy covers where the title guess was wrong but the cover
    # still shows enough words to identify the book. The winner still has to
    # pass rank_candidates' quality gate.
    clean_full = cleanquery(full_text)
    if clean_full and clean_full != clean_title:
        queries.append(clean_full)
    return queries


def retrieve_ranked_candidates(title, author="", isbn="", full_text="", limit=5,
                               text_lines=None):
    """Retrieve possibilities without silently accepting the provider's first row."""
    title = (title or "").strip()
    author = (author or "").strip()
    isbn = normalize_isbn(isbn)
    if isbn and not valid_isbn(isbn):
        return {"decision": "REJECTED", "candidates": [],
                "error": "Please enter a valid ISBN-10 or ISBN-13."}
    if not title and not isbn:
        return {"decision": "REJECTED", "candidates": [],
                "error": "A book title is required."}

    # ISBN is exact evidence. Keep a candidate response shape so confirmation,
    # persistence and catalogue lookup still use the same funnel.
    if isbn:
        exact = search_by_isbn(isbn)
        if "error" not in exact:
            exact["provider"] = "google"
            ranked = rank_candidates([exact], title or exact.get("title", ""),
                                     author, isbn, limit=limit)
            return ranked

    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    queries = build_search_queries(title, author, full_text)
    all_results = []
    seen = set()
    api_failed = False
    if api_key and queries:
        with ThreadPoolExecutor(max_workers=max(1, len(queries))) as pool:
            fetched = list(pool.map(lambda q: _fetch_google_query(q, api_key), queries))
        for books, had_error, had_quota in fetched:
            api_failed = api_failed or had_error or had_quota
            for book in books:
                key = book.get("google_books_id") or (
                    book.get("isbn_13"), book.get("title"), book.get("author"))
                if key in seen:
                    continue
                seen.add(key)
                book["provider"] = "google"
                all_results.append(book)

    # Open Library adds independent candidates and also keeps the manual flow
    # functional when Google is unconfigured or temporarily unavailable.
    if title:
        for book in search_open_library(cleanquery(title), cleanquery(author)):
            key = book.get("open_library_key") or (
                book.get("isbn_13"), book.get("title"), book.get("author"))
            if key in seen:
                continue
            seen.add(key)
            book["provider"] = "openlibrary"
            all_results.append(book)

    ranked = rank_candidates(all_results, title, author, isbn, limit=limit)
    # A cover's visual hierarchy can scramble title/author fields even when
    # the raw OCR contains the right words. Re-check provider results against
    # all visible cover text before rejecting them. This is generic metadata
    # matching, not a list of special-case books, and it always requires the
    # user to confirm the recovered result.
    if ranked["decision"] == "REJECTED" and (full_text or text_lines):
        recovered = recover_ocr_candidates(
            all_results, title, author, full_text, text_lines, limit=limit)
        if recovered["decision"] != "REJECTED":
            return recovered
    if ranked["decision"] == "REJECTED" and not all_results and api_failed:
        ranked["error"] = "Book search is temporarily unavailable. Please try again."
    elif ranked["decision"] == "REJECTED":
        ranked["error"] = "No candidate was strong enough to verify. Edit the title or author and try again."
    return ranked


def hydrate_exact_candidate(candidate):
    """Refresh a selected provider record by its exact identifier."""
    candidate = dict(candidate or {})
    volume_id = (candidate.get("google_books_id") or "").strip()
    if volume_id:
        params = {}
        key = os.environ.get("GOOGLE_BOOKS_API_KEY")
        if key:
            params["key"] = key
        data = disk_cache.fetch_json("google_volume_record", volume_id,
                                     f"{GOOGLE_BOOKS_URL}/{volume_id}",
                                     params=params, timeout=10)
        if data and data.get("volumeInfo"):
            exact = parse_book(data)
            exact["provider"] = "google"
            return exact
    return candidate


def search_by_isbn(isbn):
    # Look up a book by its ISBN number (read from the barcode).
    # NOTE: this deliberately skips the ranking quality gate —
    # an ISBN identifies exactly one book edition, so the first result
    # IS the right book by definition. Fuzzy matching only exists to fix
    # OCR mistakes, and a barcode has none.
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if not api_key:
        return {"error": "Google Books API key not set"}

    params = {
        "q": f"isbn:{isbn}",
        "maxResults": 1,
        "key": api_key
    }
    data = disk_cache.fetch_json("google_isbn", isbn, GOOGLE_BOOKS_URL,
                                 params=params, timeout=10)
    if data is None:
        return {"error": "ISBN lookup failed"}
    items = data.get("items", [])
    if not items:
        return {"error": "No book found for this ISBN"}
    # A barcode/ISBN is exact, so this is always a high-confidence match.
    book = parse_book(items[0])
    book["confidence"] = "high"
    return book


# ---------------------------------------------------------------------------
# DESCRIPTION RESOLUTION — where the text we summarise comes from.
# ---------------------------------------------------------------------------
# The rule is one sentence: the description must belong to the EXACT VOLUME we
# matched. Never to a search for the title, never to an article about the
# subject. Everything below exists to keep that promise.

# How long a description has to be before we will summarise it. Below this
# there is nothing for the model to work with and simple_truncate in
# summarizer.py would just echo it back. Also used by the new-books coverage
# experiment so that "usable" means the same thing in the app and in the
# evaluation - the number is defined ONCE, here.
MIN_USABLE_DESCRIPTION = 60

# Strings some publishers ship INSTEAD of a description. They pass a length
# check but say nothing about the book, so they must not count as coverage.
PLACEHOLDER_DESCRIPTION_RE = re.compile(
    r"^\s*(no description available|description (is )?(not available|coming soon)|"
    r"n/?a|tbc|tbd|coming soon|see (the )?back cover)\s*\.?\s*$",
    re.IGNORECASE)


# Text that is attached to a book's record but is NOT ABOUT THE BOOK.
#
# Found live 2026-07-27: the description Google returned for "The Fault in Our
# Stars" was "Josh Boone directs this drama starring Shailene Woodley and Ansel
# Elgort... Based on the novel by John Green, the film tracks..." - Google had
# matched a FILM TIE-IN edition and handed us the movie's synopsis. Summarising
# that produces a summary of a film, which is not what the user photographed.
#
# Only STRONG signals are listed. "now a major motion picture" is deliberately
# absent: it appears on plenty of genuine book blurbs whose remaining text does
# describe the book, and rejecting those would lose real descriptions.
FILM_DESCRIPTION_RE = re.compile(
    r"\bdirects this\b|\bdirected by\b|\bstarring\b|\bthe film\b|"
    r"\bthis film\b|\bscreenplay\b|\bfilm adaptation\b|"
    r"\bbased on the (?:best[- ]?selling )?novel by\b",
    re.IGNORECASE)


def describes_something_else(text):
    # True when this text describes a FILM or a BOX SET rather than the book.
    # Box sets reuse DERIVED_EDITION_RE from matching.py rather than a second
    # pattern, so the two places that care about box sets cannot drift apart.
    # Only the opening is checked: a blurb that ends with "soon to be a major
    # film" is still a book blurb, whereas one that OPENS by naming a director
    # is a film synopsis.
    if not text:
        return False
    head = text[:400]
    return bool(FILM_DESCRIPTION_RE.search(head)
                or DERIVED_EDITION_RE.search(head))


def is_usable_description(text):
    # ONE definition of "usable", shared by the app and the evaluation script.
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < MIN_USABLE_DESCRIPTION:
        return False
    if PLACEHOLDER_DESCRIPTION_RE.match(stripped):
        return False
    return not describes_something_else(stripped)


def get_volume_by_id(volume_id):
    # Fetch ONE Google Books volume by its id: GET /volumes/{id}.
    # Every other Google call in this file is a SEARCH (/volumes?q=...), which
    # answers "what books look like this text?". This one answers "what does
    # THIS volume say?", which is the question we actually need. Asking by id
    # is what makes it impossible to drift to a different edition or book.
    # Returns the description string, or "" if there is none / the call fails.
    if not volume_id:
        return ""
    params = {}
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if api_key:
        params["key"] = api_key
    # NOTE ON FAILURE HANDLING: in the LIVE APP a failed lookup must not break
    # the scan, so fetch_json returns None and the caller reports the reason
    # honestly. That is the opposite of the rule for the DATASET
    # scripts, which pass strict=True so a bad response stops the run rather
    # than being recorded as an absence (EVALUATION.md 8.1). The difference is
    # deliberate: a user wants a working app, a measurement wants the truth or
    # nothing. Either way the failure is never cached.
    data = disk_cache.fetch_json("google_volume", volume_id,
                                 f"{GOOGLE_BOOKS_URL}/{volume_id}",
                                 params=params, timeout=10)
    if not data:
        return ""
    return data.get("volumeInfo", {}).get("description", "") or ""


def get_open_library_edition(isbn):
    # Fetch the Open Library EDITION record for an ISBN: /isbn/{isbn}.json.
    # An edition is one specific printing, so it is the closest thing Open
    # Library has to "the exact book on the desk". Returns the JSON dict, or
    # None. Editions often have no description of their own, in which case the
    # caller falls back to the WORK record they point at.
    if not isbn:
        return None
    return disk_cache.fetch_json("ol_edition", isbn,
                                 f"https://openlibrary.org/isbn/{isbn}.json",
                                 headers={"User-Agent": "BookFinder/1.0"},
                                 timeout=10)


def _ol_text(value):
    # Open Library returns a description as either a plain string or a
    # {"type": ..., "value": ...} dict. Handle both.
    if isinstance(value, dict):
        return value.get("value", "") or ""
    return value or ""


def get_open_library_work_description(work_key):
    # Fetch a WORK record (/works/OL...W.json) and return its description.
    # A work is the abstract book across all its editions - less precise than
    # an edition, which is why it is tried second, but still the right BOOK.
    if not work_key:
        return ""
    data = disk_cache.fetch_json("ol_work", work_key,
                                 f"https://openlibrary.org{work_key}.json",
                                 headers={"User-Agent": "BookFinder/1.0"},
                                 timeout=10)
    if not data:
        return ""
    return _ol_text(data.get("description", ""))[:1200]


def parse_book(item):
    # Turn one raw Google Books "item" into our own simple dictionary.
    vi = item.get("volumeInfo", {})

    authors = vi.get("authors", [])        # this is a LIST
    cats = vi.get("categories", [])        # this is a LIST
    imgs = vi.get("imageLinks", {})        # note the capital L in imageLinks

    thumb = imgs.get("thumbnail", "") or imgs.get("smallThumbnail", "")
    # Google sometimes returns http:// links; browsers prefer https://.
    if thumb and thumb.startswith("http://"):
        thumb = thumb.replace("http://", "https://")

    # ISBNs. We never read these before, which meant that once a book was
    # matched we had no way to ask Open Library about THAT EXACT EDITION -
    # only to search it by title again, which can land on a different book.
    # industryIdentifiers looks like [{"type": "ISBN_13", "identifier": "..."}].
    isbn_13 = ""
    isbn_10 = ""
    for ident in vi.get("industryIdentifiers", []) or []:
        if ident.get("type") == "ISBN_13" and not isbn_13:
            isbn_13 = (ident.get("identifier") or "").strip()
        elif ident.get("type") == "ISBN_10" and not isbn_10:
            isbn_10 = (ident.get("identifier") or "").strip()

    return {
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "title": vi.get("title", ""),
        "author": ", ".join(authors),
        "publisher": vi.get("publisher", ""),
        "published_date": vi.get("publishedDate", ""),   # capital D
        "description": vi.get("description", ""),
        "page_count": int(vi.get("pageCount") or 0),
        "categories": ", ".join(cats),
        "thumbnail": thumb,
        "google_books_id": item.get("id", ""),
        # Popularity signals (sparse — most editions have no ratings, so
        # these are only ever a small tie-breaker, never a decider).
        "ratings_count": int(vi.get("ratingsCount") or 0),
        "average_rating": float(vi.get("averageRating") or 0.0)
    }


OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"


def parse_ol_doc(doc):
    # Turn one Open Library search "doc" into the SAME dictionary shape that
    # parse_book makes, so the matcher and the rest of the app can treat books
    # from either source identically. Search docs carry no description; the
    # overview builder fills that in after the match, exactly like it does for
    # Google results.
    authors = doc.get("author_name", []) or []
    publishers = doc.get("publisher", []) or []
    subjects = doc.get("subject", []) or []
    year = doc.get("first_publish_year")

    cover_id = doc.get("cover_i")
    thumb = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""

    # Open Library returns every ISBN it knows for the work, in no useful
    # order. Take the first 13-digit one so the overview builder can look up an
    # edition record; better an approximate edition than none at all, and the
    # work record is the fallback underneath it anyway.
    isbns = [str(i) for i in (doc.get("isbn") or [])]
    isbn_13 = next((i for i in isbns if len(i) == 13), "")

    return {
        "isbn_13": isbn_13,
        "isbn_10": next((i for i in isbns if len(i) == 10), ""),
        "title": doc.get("title", ""),
        "author": ", ".join(authors[:2]),
        "publisher": publishers[0] if publishers else "",
        "published_date": str(year) if year else "",
        "description": "",
        "page_count": int(doc.get("number_of_pages_median") or 0),
        "categories": ", ".join(subjects[:3]),
        "thumbnail": thumb,
        "google_books_id": "",
        "open_library_key": doc.get("key", ""),
        "open_library_work_id": doc.get("key", ""),
        "open_library_edition_id": ((doc.get("edition_key") or [""])[0]),
    }


def search_open_library(title, author=""):
    # Search Open Library for candidate books (free, no API key, no daily
    # quota). Used as a FALLBACK when Google Books finds nothing — including
    # when Google's daily quota is exhausted, which used to kill scanning for
    # the rest of the day. Returns a list of book dicts (possibly empty).
    query = f"{title} {author}".strip()
    params = {
        "q": query,
        "limit": 5,
        # Ask only for the fields we use; the full docs are huge.
        # isbn was added so a matched Open Library candidate can still be
        # resolved to an EDITION record for its description.
        "fields": ("key,edition_key,title,author_name,first_publish_year,cover_i,"
                   "number_of_pages_median,publisher,subject,isbn")
    }
    data = disk_cache.fetch_json("ol_search", query, OPEN_LIBRARY_SEARCH_URL,
                                 params=params, timeout=10,
                                 headers={"User-Agent": "BookFinder/1.0"})
    if not data:
        return []
    docs = data.get("docs", [])
    return [parse_ol_doc(d) for d in docs if d.get("title")]
