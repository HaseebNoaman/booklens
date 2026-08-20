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
# source has one, we say so - see resolve_description() below.

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from thefuzz import fuzz

import disk_cache

# The MATCHING ALGORITHM lives in matching.py; this file is the HTTP client
# layer (Google Books, Open Library). Every name defined there is
# re-exported here, so api.pick_best, api.cleanquery, api.T_TITLE and the rest
# keep resolving for app.py, test_app.py and the benchmark scripts in
# test_covers/ without any of them having to change.
from matching import (  # noqa: F401  (imported for re-export)
    cleanquery, verify_against_cover, pick_best,
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
    # pass pick_best's quality gate.
    clean_full = cleanquery(full_text)
    if clean_full and clean_full != clean_title:
        queries.append(clean_full)
    return queries


def searchbook(title, author="", full_text="", typed_title=False):
    # Main search function. Tries several queries and picks the best result.
    # typed_title=True means the user TYPED this title (manual fallback), so we
    # trust it more in pick_best (see that function) instead of verifying it
    # against a photographed cover.
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if not api_key:
        return {"error": "Google Books API key not set"}

    clean_title = cleanquery(title)
    clean_author = cleanquery(author)

    if not clean_title:
        return {"error": "No usable title text from OCR"}

    queries = build_search_queries(title, author, full_text)

    # Run ALL the queries and collect every candidate book. We used to stop
    # early once a query returned something, but that let one query's junk
    # block a later, better query. Precision is now handled by the quality
    # gate in pick_best, so it is safe to gather widely and let the gate
    # choose. Duplicates (same Google id) are removed.
    all_results = []
    seen_ids = set()
    api_error = False   # set if Google refuses a query (outage, timeout...)
    quota_hit = False   # set specifically on HTTP 429 "daily quota exceeded"

    # The queries are INDEPENDENT lookups, so run them at the same time instead
    # of one after another. Sequentially this phase measured ~10s of a ~40s
    # scan (four round-trips to Google, each with its own latency); in parallel
    # it costs about as long as the SLOWEST single query. Results are merged
    # below in the original query order, so de-duplication and the rank bonus
    # behave exactly as they did sequentially (order-independent output).
    with ThreadPoolExecutor(max_workers=max(1, len(queries))) as pool:
        fetched = list(pool.map(lambda q: _fetch_google_query(q, api_key),
                                queries))

    for books, had_error, had_quota in fetched:
        # A non-200 means Google itself rejected the request. 429 is the
        # daily-quota limit; anything else (500/503/...) is a temporary
        # outage. Neither means "this book does not exist", so we remember
        # what happened and report the RIGHT reason below.
        api_error = api_error or had_error
        quota_hit = quota_hit or had_quota
        for i, book in enumerate(books):
            bid = book.get("google_books_id", "")
            # Google's own result order encodes a relevance/popularity
            # model we cannot reconstruct from the sparse metadata, so
            # remember each candidate's best position as a small bonus
            # (8,6,4,2,0 down the list; max across queries). Only the
            # TYPED ranking uses it (see pick_best) — scan ranking has
            # real cover text to discriminate with.
            rank_bonus = max(0, 8 - 2 * i)
            if bid and bid in seen_ids:
                for existing in all_results:
                    if existing.get("google_books_id") == bid:
                        existing["_rank_bonus"] = max(
                            existing.get("_rank_bonus", 0), rank_bonus)
                        break
                continue
            seen_ids.add(bid)
            book["_rank_bonus"] = rank_bonus
            all_results.append(book)

    # Verify candidates against the FULL cover text, not just the (often
    # jumbled) OCR title. full_text falls back to title+author if empty.
    # (pick_best handles an empty candidate list itself.)
    result = pick_best(all_results, full_text or f"{title} {author}",
                       typed_title=typed_title, probable_title=title)
    if "error" not in result:
        return result

    # FALLBACK: Open Library (free, no key, no daily quota). We only get here
    # when Google produced nothing usable — zero candidates (including quota
    # exhaustion and outages, which used to kill scanning for the rest of the
    # day) or nothing that passed the quality gate. The SAME gate judges the
    # Open Library candidates, so this cannot loosen precision.
    # Measured on the 100 covers in test_covers/ (evaluate_ol_fallback.py,
    # 2026-07-10): +7 correct, +1 wrong, precision steady at 92%, and the
    # non-book poster stayed rejected.
    #
    # SCAN PATH ONLY. The typed-title gate is deliberately lenient (a partial
    # title like "Gatsby" must match), which is safe against Google's
    # intitle:-prefiltered results but NOT against Open Library's raw catalog
    # of millions of obscure entries: typing "jaws" accepted the medical text
    # "Fracture of the Lower Jaw" at high confidence (partial_ratio 86).
    # Seen live 2026-07-10 and reproduced in the tests below — do not re-enable
    # OL for typed titles without a stricter typed gate measured first.
    if not typed_title:
        ol_results = search_open_library(clean_title, clean_author)
        if ol_results:
            ol_best = pick_best(ol_results, full_text or f"{title} {author}",
                                probable_title=title)
            if "error" not in ol_best:
                return ol_best

    # Neither source found an acceptable match. If Google refused us, say so
    # clearly (and with the right cause) instead of the misleading
    # "no matching book".
    if not all_results and (quota_hit or api_error):
        if quota_hit:
            msg = ("Book search is busy right now (daily lookup limit reached). "
                   "Please try again later.")
        else:
            msg = ("Book search is temporarily unavailable. "
                   "Please try again in a moment.")
        return {"error": msg, "confidence": "low"}

    return result


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
    # NOTE: this deliberately skips pick_best and the quality gate —
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


# A Wikipedia article that describes a SERIES rather than one book. This is
# the exact failure that got Wikipedia removed in the first place: scanning
# "Harry Potter and the Philosopher's Stone" returned "Harry Potter is a series
# of seven children's fantasy novels...". Presence of the author was not enough
# to catch it, because the series article names the author too.
#
# It must fire only when the article's SUBJECT is the series, not whenever a
# series is mentioned. The first version matched any "book series" / "novel
# series" and wrongly rejected "The Miserable Mill is the fourth novel of the
# children's novel series A Series of Unfortunate Events" - an article about
# exactly the book we wanted. Anchoring on "is a ... series" fixes that,
# because an article about one book says "is the fourth novel", never
# "is a series".
WIKI_SERIES_RE = re.compile(
    r"\bis an?\s+(?:[\w'-]+\s+){0,3}series\b|\bis a series of\b",
    re.IGNORECASE)

# A Wikipedia article about the FILM of the book, not the book.
WIKI_FILM_RE = re.compile(
    r"\bis a \d{4} (?:American |British |[A-Za-z]+ )?film\b|"
    r"\bfilm directed by\b|\bis a film\b",
    re.IGNORECASE)


def looks_like_disambiguation(text):
    # Wikipedia disambiguation/word pages list links ("Martian, Martians or The
    # Martians may also refer to: ...") instead of describing a book. Feeding
    # one to the model produced a nonsense summary for The Martian.
    head = text[:500].lower()
    return "may refer to" in head or "may also refer to" in head


def is_author_page(resolved_title, author):
    # Wikipedia redirects a book with no article of its own to its AUTHOR's
    # biography - both "The 10X Rule" and "If You're Not First, You're Last"
    # resolve to "Grant Cardone". Summarising that describes the PERSON.
    if not author or not resolved_title:
        return False
    return fuzz.token_set_ratio(resolved_title.lower(), author.lower()) >= 85


def get_verified_wikipedia_description(title, author=""):
    # Wikipedia text for a book, but ONLY when the article can be shown to be
    # about THIS book. Returns "" when it cannot.
    #
    # WHY IT IS BACK. Wikipedia plot sections are the best summary input we
    # have for well-known books - far better than a publisher blurb, which is
    # marketing copy and is sometimes a film synopsis or a box-set listing.
    # It was removed wholesale because it kept returning the SERIES or the
    # AUTHOR. That was a verification failure, not a source failure: the old
    # code searched by title string and had nothing to check the result
    # against. We now hold the matched volume's real title and author, so an
    # article has to prove it describes that book before we will use it.
    #
    # It contributes NOTHING for new books - measured 0 of 100 on titles
    # published in 2026 - which is exactly why it is tried first and falls
    # through silently rather than being relied upon.
    if not title:
        return ""
    try:
        import wikipediaapi
        wiki = wikipediaapi.Wikipedia("BookFinder/1.0", "en", timeout=10)

        surname = author.split()[-1] if author.strip() else ""
        main_title = title.split(":")[0].strip()

        # Study and school editions prefix the author possessively -
        # "William Golding's Lord of the Flies". Wikipedia has no article under
        # that name, so the book was reported as having no description at all
        # even though its article exists. Strip the prefix and try the bare
        # title too. Seen live 2026-07-27.
        bare_title = re.sub(r"^\s*[\w.'-]+(?:\s+[\w.'-]+){0,3}'s\s+", "",
                            main_title).strip()
        # Catalogue titles also carry the author AFTER the title, and
        # Wikipedia never does. Both forms were seen live on 2026-07-27 and
        # each made the book report NO description although its article
        # exists: "Normal People by Sally Rooney", "Jaws, Peter Benchley".
        if author.strip():
            for name in filter(None, (author.strip(), surname)):
                bare_title = re.sub(
                    rf"\s*(?:,|\bby\b)\s*{re.escape(name)}\s*$", "",
                    bare_title, flags=re.IGNORECASE).strip()

        # Book titles routinely lose a leading "The" in cover text and in
        # catalogue metadata. Wikipedia keeps it: the article is "The
        # Miserable Mill", so a stored title of "Miserable Mill" found nothing
        # and the book fell back to a two-line blurb the model then padded
        # into an invented sentence. Seen live 2026-07-27.
        variants = [title, main_title, bare_title]
        for t in (main_title, bare_title):
            if t and not re.match(r"^(the|a|an)\s", t, re.IGNORECASE):
                variants.append(f"The {t}")

        terms = []
        for t in variants:
            if not t:
                continue
            if surname:
                terms.append(f"{t} ({surname} novel)")
            terms.append(f"{t} (novel)")
            terms.append(t)
        seen = set()
        terms = [t for t in terms if not (t in seen or seen.add(t))]

        for term in terms:
            page = wiki.page(term)
            if not page.exists() or len(page.summary) < 100:
                continue

            lead = page.summary
            # --- the verification gauntlet -------------------------------
            if looks_like_disambiguation(lead):
                continue
            if is_author_page(page.title, author):
                continue
            if WIKI_SERIES_RE.search(lead[:400]):
                continue        # the series, not this book
            if WIKI_FILM_RE.search(lead[:400]):
                continue        # the film, not the book
            if surname and surname.lower() not in (lead + page.text[:2000]).lower():
                continue        # never names the author - probably not the book
            # The resolved page title must still be recognisably this book.
            # Wikipedia follows redirects, so page.title is where we LANDED,
            # not what we asked for.
            #
            # Compare against the BARE title as well as the raw one. When the
            # catalogue title carries the author ("Jaws, Peter Benchley") the
            # raw comparison is diluted by words the article will never have:
            # "Jaws (novel)" scored 57 against it and was rejected by three
            # points, while scoring 100 against "Jaws". Seen live 2026-07-27.
            landed = page.title.lower()
            if max(fuzz.token_set_ratio(landed, main_title.lower()),
                   fuzz.token_set_ratio(landed, bare_title.lower())) < 60:
                continue

            # --- build the input the model reads -------------------------
            # HYBRID (measured 2026-07-19): the lead's first two sentences
            # anchor what the book IS, while the plot section supplies the
            # story. Lead-only leaked publication trivia into summaries;
            # plot-only garbled referents.
            plot = ""
            for section_name in ("Plot", "Plot summary", "Synopsis", "Summary"):
                section = page.section_by_title(section_name)
                if section is not None and len(section.text) > 100:
                    plot = section.text
                    break
            if plot:
                protected = re.sub(r"\b([A-Z])\.", r"\1<DOT>", lead)
                sentences = [s.strip() for s in
                             re.split(r"(?<=[.!?])\s+", protected) if s.strip()]
                text = f"{' '.join(sentences[:2]).replace('<DOT>', '.')} {plot}"
            else:
                text = lead

            # The model reads about 1200 characters. Cut on a sentence end.
            if len(text) > 1200:
                text = text[:1200]
                last_dot = text.rfind(". ")
                if last_dot > 100:
                    text = text[:last_dot + 1]
            return text
        return ""
    except Exception:
        return ""


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
    # the scan, so fetch_json returns None and resolve_description reports the
    # reason honestly. That is the opposite of the rule for the DATASET
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


def resolve_description(book):
    # Decide what text to summarise for ONE matched book.
    #
    # Order, RICHEST VERIFIED SOURCE FIRST:
    #   1. wikipedia           - lead + plot, but only when the article is
    #                            verified to be about THIS book (see
    #                            get_verified_wikipedia_description). Best text
    #                            available for well-known books; contributes
    #                            nothing for new ones, measured 0/100 on 2026
    #                            titles, so it simply falls through.
    #   2. google_volume       - the matched volume's own description. This is
    #                            the answer for new books.
    #   3. openlibrary_edition - the edition record for its ISBN
    #   4. openlibrary_work    - the work that edition belongs to
    #   5. nothing             - we say so, and never invent a substitute
    #
    # Every candidate must pass is_usable_description, which now also rejects
    # text that describes a FILM or a BOX SET rather than the book.
    #
    # Returns {"text", "source", "reason"}. `source` is None when there is no
    # text. `reason` distinguishes the two ways that can happen, which is a
    # distinction users and examiners both care about:
    #   "no_identifiers"            - we never had an id to look anything up by
    #   "sources_had_no_description"- we asked and this book genuinely has none
    # Refusing here is the same behaviour as the matching gate refusing a weak
    # candidate: an honest "I don't know" beats a confident wrong answer.
    volume_id = (book.get("google_books_id") or "").strip()
    isbn = (book.get("isbn_13") or book.get("isbn_10") or "").strip()
    work_key = (book.get("open_library_key") or "").strip()

    # Wikipedia is deliberately not part of the automatic summary funnel.
    # Even a plausible article is not a locally verified catalogue summary.
    # Publisher descriptions below may be displayed with that exact label,
    # but callers must never treat this function as catalogue verification.

    # 2. Google Books, by volume id.
    # The search response already carried this volume's own description, so
    # when it is usable we use it directly - it came from the matched volume,
    # which is exactly the guarantee we need, and it saves a round trip.
    # Only when it is missing, too short, or a film/box-set synopsis do we
    # re-ask by id, because search responses sometimes omit the field that the
    # full volume record has.
    existing = book.get("description", "") or ""
    if is_usable_description(existing):
        return {"text": existing, "source": "google_volume", "reason": ""}
    if volume_id:
        fetched = get_volume_by_id(volume_id)
        if is_usable_description(fetched):
            return {"text": fetched, "source": "google_volume", "reason": ""}

    # 2. Open Library edition, by ISBN.
    edition = get_open_library_edition(isbn) if isbn else None
    if edition is not None:
        edition_desc = _ol_text(edition.get("description", ""))
        if is_usable_description(edition_desc):
            return {"text": edition_desc[:1200],
                    "source": "openlibrary_edition", "reason": ""}
        # 3a. The edition points at its work - follow it. This keeps us on the
        # same book instead of running a fresh title search, which is the
        # mistake the old Wikipedia path made.
        works = edition.get("works") or []
        if works and not work_key:
            work_key = works[0].get("key", "")

    # 3b. Open Library work.
    if work_key:
        work_desc = get_open_library_work_description(work_key)
        if is_usable_description(work_desc):
            return {"text": work_desc, "source": "openlibrary_work", "reason": ""}

    # 4. Nothing. Say so.
    if not volume_id and not isbn and not work_key:
        return {"text": None, "source": None, "reason": "no_identifiers"}
    return {"text": None, "source": None,
            "reason": "sources_had_no_description"}


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
    # parse_book makes, so pick_best and the rest of the app can treat books
    # from either source identically. Search docs carry no description; the
    # existing enrichment step (Wikipedia / Open Library work page) fills
    # that in after the match, exactly like it does for Google results.
    authors = doc.get("author_name", []) or []
    publishers = doc.get("publisher", []) or []
    subjects = doc.get("subject", []) or []
    year = doc.get("first_publish_year")

    cover_id = doc.get("cover_i")
    thumb = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""

    # Open Library returns every ISBN it knows for the work, in no useful
    # order. Take the first 13-digit one so resolve_description can look up an
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
