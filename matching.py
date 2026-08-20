# matching.py
# THE MATCHING ALGORITHM — this is the core of the project.
#
# Everything here answers one question: given the text we read off a cover and
# a handful of candidate books from Google Books / Open Library, WHICH book is
# it, and how sure are we? The two jobs are deliberately separate:
#
#   verify_against_cover()  scores one candidate against the cover text
#   pick_best()             ranks all candidates, then applies a QUALITY GATE
#                           that rejects a weak best-candidate outright and
#                           grades an accepted one high / medium
#
# Every threshold below was measured on the 100-cover benchmark in
# test_covers/, not guessed, and each one carries the measurement that set it.
# The honest-rejection behaviour is the point: the system says "no matching
# book found" rather than showing a confident wrong answer.
#
# This code used to live in api.py. It was moved out because api.py is the
# HTTP client layer (Google Books, Open Library) and the matching
# logic is a different concern that happens to consume its output. api.py
# re-exports every name defined here, so `api.pick_best`, `api.T_TITLE` and
# the rest keep working for app.py, the tests and every benchmark script in
# test_covers/.

import os
import re

from thefuzz import fuzz

# Words that carry no identifying information in a title. "The Girl on the
# Train" and "The Boy in the Boat" share three of six words, all from this
# list, so counting them would make almost any title look "covered".
TITLE_STOPWORDS = {
    "a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "from",
    "with", "by", "or", "is", "it", "as", "into",
}


def title_token_coverage(book_title, cover_text):
    # What FRACTION of the candidate title's OWN words actually appear in the
    # text we read off the cover? Returns 0.0-1.0.
    #
    # WHY THIS EXISTS. title_score in verify_against_cover is
    # max(token_set_ratio, partial_ratio), and both of those can be high while
    # most of the title is missing from the cover:
    #   - token_set_ratio returns 100 whenever the candidate's title words are
    #     a SUBSET of the cover text, no matter how much else is on the cover.
    #   - partial_ratio scores a SHORT title highly against almost any longer
    #     text, because it slides the short string along looking for its best
    #     window.
    # So a candidate could look "present" on author evidence alone. That is
    # exactly how a DIFFERENT BOOK BY THE CORRECT AUTHOR gets accepted: the
    # author matches strongly, and the title only has to clear a bar that a
    # couple of common words already clears.
    #
    # This measure is deliberately different: it is RECALL over the candidate
    # title's words, so a title whose words are mostly absent from the cover
    # scores low however well the author matches.
    title = (book_title or "").split(":")[0].lower()
    title_tokens = re.findall(r"[a-z0-9']+", title)
    content = [t for t in title_tokens
               if t not in TITLE_STOPWORDS and len(t) > 1]
    # A title made only of stopwords or single letters ("It", "Us", "A") has
    # nothing to measure. Fall back to the raw words rather than returning a
    # free pass, so those titles are still judged on something.
    if not content:
        content = [t for t in title_tokens if t]
    if not content:
        return 0.0

    cover_tokens = re.findall(r"[a-z0-9']+", (cover_text or "").lower())
    if not cover_tokens:
        return 0.0

    found = 0
    for word in content:
        for token in cover_tokens:
            # Exact or near-exact match. fuzz.ratio >= 85 absorbs the OCR
            # noise we actually see ("Mockingbird" read as "Mockinqbird").
            # The containment test catches the other common OCR artifact:
            # neighbouring words merged into one ("GRANTCARDONE").
            if word == token or word in token or fuzz.ratio(word, token) >= 85:
                found += 1
                break
    return found / len(content)


def cleanquery(text):
    # Clean the OCR text so it makes a good search query.
    if not text:
        return ""
    # Replace anything that is not a letter/number/space with a space.
    text = re.sub(r"[^\w\s]", " ", text)
    # Turn multiple spaces into one space.
    text = re.sub(r"\s+", " ", text).strip()
    # Keep only words that are 2 letters or longer.
    words = [w for w in text.split() if len(w) >= 2]
    # Use only the first 8 words (queries get worse if too long).
    return " ".join(words[:8])


def verify_against_cover(book, cover_text):
    # Score how well a candidate book matches ALL the text read off the
    # cover (not the jumbled OCR title). Returns three 0-100 numbers:
    #   title_score : is the book's TITLE present on the cover? (order- and
    #                 subset-tolerant, so extra cover words don't hurt)
    #   author_score: is the book's AUTHOR present on the cover?
    #   title_fit   : is the cover essentially JUST this title? High for a
    #                 title-only cover, LOW for a poster whose title is a
    #                 small part of its text -> this is our precision guard.
    title = book.get("title", "").split(":")[0].lower()
    author = book.get("author", "").lower()
    cover = cover_text.lower()

    title_score = max(fuzz.token_set_ratio(title, cover),
                      fuzz.partial_ratio(title, cover))
    author_score = 0
    if author:
        author_score = max(fuzz.token_set_ratio(author, cover),
                           fuzz.partial_ratio(author, cover))
    title_fit = fuzz.token_sort_ratio(title, cover)
    return title_score, author_score, title_fit


# Acceptance thresholds, chosen by measuring on 100 real covers
# (test_covers/). This point maximized correct matches while a non-book
# poster stayed rejected.
T_TITLE = 65
T_AUTHOR = 55
T_FIT = 85

# Higher bar for a "high confidence" match: the title AND author are both
# clearly on the cover. A match that only just passes the acceptance gate
# above is graded "medium" instead, so the UI can ask the user to confirm.
T_TITLE_HIGH = 80
T_AUTHOR_HIGH = 70

# HIGH additionally requires the author to pass a STRICT check using
# token_set_ratio alone. partial_ratio (used in the acceptance scores) lets a
# short author string ride a substring: the print-on-demand notebook
# "Admit One" by "Admit Journals" hit author 73 against a cinema TICKET
# because "admit" is literally on it — our one silent high-band false accept
# (EVALUATION.md negative set). Strict scores: ticket 53 vs 56+ for every
# legitimate high on the 100 covers, so 55 separates them cleanly. Measured
# cost: 4 correct books drop high->medium (the confirm bar), the wrong
# high pick "The Shining" (strict 49) drops with them. Confidence band only —
# acceptance is unchanged.
T_AUTHOR_HIGH_STRICT = 55

# Extra "safe loosening" rules that recover real books the strict gate missed
# WITHOUT letting a non-book poster through (measured offline on the 100 covers
# in test_covers/: these lifted correct matches 52 -> 54 while the poster stayed
# rejected). A poster's author barely matches and its title is a small part of
# the page, so both rules below still exclude it.
#   Rule 1: a STRONG author backs up a title that is present (Project Hail Mary,
#           Start with Why — clear author, title just under the main threshold).
T_AUTHOR_STRONG = 70
T_TITLE_PRESENT = 60
#   Rule 2: a very strong, cover-dominant title (title clearly present AND the
#           cover is mostly just that title).
T_TITLE_DOMINANT = 90
T_FIT_LOOSE = 65

# A HIGH-confidence match must also agree with the cover's DOMINANT text
# (the height-ordered probable title). Covers routinely quote OTHER books in
# small print — taglines, "author of ..." promos — and those quotes are real
# titles by the same author, so they pass every text-presence check. Seen
# live 2026-07-20: a stylized "10X" logo defeated OCR and the tagline
# "IF YOU'RE NOT FIRST, YOU'RE LAST" (Cardone's other book) matched at HIGH.
# A match verified only against small print is genuinely less certain, so it
# is capped at MEDIUM and the UI asks the user to confirm. Acceptance is
# unchanged — this is a confidence-band rule only.
# Threshold measured on the 100-cover benchmark (test_covers/
# confidence_bands.py): the live tagline case scores 50 while every correct
# high scores 61+ except one cover whose dominant text is the AUTHOR name
# (Klara and the Sun, 30 — demoted to the confirm bar, which is honest).
# 55 splits the two groups with margin on both sides.
T_HIGH_ON_PROBABLE = 55

# When the user TYPES the title (manual fallback), we trust it like a barcode:
# accept the closest book whose title matches what they typed, using the lenient
# (subset/substring tolerant) title score, and do NOT require an author.
T_TITLE_TYPED = 60

# Derived editions (box sets, third-party "Summary of ..." knockoffs, collected
# biographies) share the real book's title and author, so they can outrank the
# actual book. Measured on the 100 covers (test_covers/error_analysis.py,
# tune_thresholds.py, 2026-07-13): a "Colleen Hoover Ebook Box Set" beat Verity
# and "Walter Isaacson: The Genius Biographies" beat Steve Jobs. Penalizing
# them (with the title bonus below) lifted correct 64 -> 67 and precision
# 90% -> 94% with the poster still rejected.
DERIVED_EDITION_RE = re.compile(
    r"box(ed)? set|summary of|summary and analysis|workbook|study guide|"
    r"biographies|ebook collection|books collection|novels collection",
    re.IGNORECASE)
DERIVED_EDITION_PENALTY = 40

# Broader catalogue-funnel exclusions. These are intentionally metadata words,
# not genres: they describe a derived product rather than the individual book.
UNSAFE_EDITION_RE = re.compile(
    r"\b(?:box(?:ed)?\s*set|complete\s+(?:set|collection|series|works)|"
    r"collection\s+of|omnibus|bundle|study\s*guide|summary(?:\s+and\s+analysis)?|"
    r"workbook|teacher(?:'s)?\s+edition|educator\s+edition|companion|"
    r"reader(?:'s)?\s+guide|film\s+adaptation|movie\s+tie[- ]?in|"
    r"screenplay|series\s+guide|books?\s+collection|novels?\s+collection)\b",
    re.IGNORECASE)

# The height-ordered probable title (the biggest text on the cover) is almost
# always the REAL title, while promo text ("author of Seabiscuit...") and
# library-slip noise are smaller. A candidate whose title sits inside the
# probable title earns a bonus, which breaks exactly the ties the cover text
# alone cannot (Unbroken vs Seabiscuit, Gone Girl vs Dark Places — both wrong
# books were physically on the cover too, in smaller print).
TITLE_ON_COVER_BONUS = 15

# MINIMUM TITLE-TOKEN COVERAGE (scan path only).
# A candidate must not be accepted on AUTHOR evidence alone: at least this
# fraction of its own title words has to be physically present on the cover.
# See title_token_coverage() above for why the existing title_score cannot
# express this.
#
# MEASURED AND CURRENTLY INERT (0.0 = gate off). Swept on the 100-cover
# benchmark, experiments/title_coverage_sweep.csv:
#
#   threshold | correct wrong none precision
#        0.00 |      67     4   29       94%   <- shipped
#        0.20 |      66     4   30       94%
#        0.50 |      66     4   30       94%
#        0.60 |      60     4   36       94%
#        1.00 |      55     4   41       93%
#
# At EVERY threshold `wrong` stays at 4. The gate removes correct matches and
# not one wrong one, so on this benchmark it is a pure loss. The reason is
# structural: all 4 wrong matches score coverage 1.00, because the wrong
# candidate's title really IS printed on the cover. The clearest case is
# cover_034, whose jacket reads "THE STAND ... a novel by the author of THE
# SHINING" - the OCR missed the title and read the tagline, so the matcher
# was handed another Stephen King title that is genuinely on the cover.
# Presence cannot separate those; only PROMINENCE can, and that is what
# T_HIGH_ON_PROBABLE already measures (as a confidence band, not acceptance).
#
# Kept in the tree because the measurement is the finding, and because the
# metric is reusable if acceptance is ever tied to prominence. Set
# OCR_TITLE_COVERAGE to re-run the sweep without editing this file.
T_TITLE_COVERAGE = float(os.environ.get("OCR_TITLE_COVERAGE", "0.0"))


def probable_title_agreement(book_title, probable_title):
    # How well does a candidate's title agree with the cover's BIGGEST text
    # (the height-ordered probable title)? Returns 0-100, or None when there
    # is no probable title to judge against.
    #
    # max() of both fuzz flavours: partial_ratio handles a probable title that
    # is a subset of the real title ("RULE"), token_set_ratio handles
    # word-order scrambling from OCR ("Purple The Colpr").
    if not probable_title:
        return None
    main = (book_title or "").split(":")[0].lower()
    probable = probable_title.lower()
    return max(fuzz.partial_ratio(main, probable),
               fuzz.token_set_ratio(main, probable))


# ACCEPTANCE on prominence (scan path only). MEASURED AND REJECTED - 0 = off.
#
# The idea: the same-author trap puts the WRONG title genuinely on the cover
# ("a novel by the author of THE SHINING"), so presence cannot separate them,
# but SIZE should - reject a candidate that does not agree with the cover's
# biggest text. Swept in experiments/probable_acceptance_sweep.csv:
#
#   threshold | correct wrong none precision
#           0 |      69     2   29       97%   <- shipped
#          40 |      68     2   30       97%
#          50 |      67     2   31       97%
#          55 |      65     1   34       98%
#          70 |      62     1   37       98%
#
# At 55 it catches 1 wrong and costs 4 correct. Why it fails: on real jackets
# the biggest text is very often the AUTHOR or the SUBTITLE, not the title.
# The five covers it rejects at 55 are
#   cover_034 The Stand   -> The Shining (the wrong one)  agreement 50
#   cover_003 Atomic Habits   correct, probable = the subtitle   46
#   cover_047 Verity          correct, probable = "HOOVER COLLEEN"  50
#   cover_011 Da Vinci Code   correct, probable = "CODE DANBROWN"   53
#   cover_045 Klara and the Sun correct, probable = "ISHIGURO KAZUO" 30
# The wrong one scores 50, sitting INSIDE the range of the correct ones
# (46-53). There is no separating threshold - the signal does not
# discriminate on this benchmark.
#
# Decisive point: all five were already in the MEDIUM band, so the app was
# already asking the user to confirm every one of them. Promoting this to an
# acceptance rule does not prevent a wrong book being shown confidently - the
# confidence band already prevented that - it only converts "ask the user"
# into "show nothing". That is a worse product for no precision that matters.
# It stays a CONFIDENCE rule (T_HIGH_ON_PROBABLE), which is where it works.
T_ACCEPT_ON_PROBABLE = float(os.environ.get("OCR_ACCEPT_ON_PROBABLE", "0"))


def pick_best(results, cover_text, typed_title=False, probable_title=""):
    # Rank every candidate by how well it matches the cover text, then apply
    # the quality gate so a weak best-candidate is honestly rejected.
    # typed_title=True -> the user typed the title, so we trust it (see below).
    # probable_title  -> the height-ordered OCR title (biggest cover text),
    #                    used only as a ranking bonus, never for the gate.
    if not results:
        return {"error": "No matching book found", "confidence": "low"}

    best_book = None
    best_key = -1
    best_scores = (0, 0, 0)

    for book in results:
        title_score, author_score, title_fit = verify_against_cover(book, cover_text)
        # TYPED MODE: the "cover text" is just the few words the user typed,
        # and fuzzing an author name against a short typed title yields a
        # meaningless 25-45 — pure noise that decides ties randomly. Seen
        # live 2026-07-20: for typed "The Hobbit", "Jeff Barton" (a game
        # guide) scored 42 vs 35 for "J.R.R. Tolkien" and the noise picked
        # the guide. Count the author only when it is a REAL signal, i.e.
        # the user actually typed author words ("The Hobbit Tolkien").
        ranked_author = author_score
        if typed_title and author_score < T_AUTHOR:
            ranked_author = 0
        key = title_score + ranked_author + title_fit
        # Prefer books that actually have a description (needed for a summary).
        if len(book.get("description", "")) >= 50:
            key += 20
        # A real book usually has more than 20 pages -> tiny tie-breaker.
        if book.get("page_count", 0) > 20:
            key += 5
        # TYPED MODE tie-breakers, measured on the 100-title typed benchmark
        # (test_covers/evaluate_typed_search.py): bare-title top-1 went
        # 72/100 (old ranking) -> 82/100 with the author-noise gate above
        # plus these two signals. Both are small and capped — they separate
        # otherwise-tied editions but can never override a title or author
        # difference.
        if typed_title:
            # Any user ratings at all mark the edition people actually read
            # (knockoff "editions" of popular novels have none).
            ratings = book.get("ratings_count", 0) or 0
            if ratings >= 100:
                key += 10
            elif ratings >= 1:
                key += 5
            # Google's own relevance order (recorded by searchbook).
            key += book.get("_rank_bonus", 0)
        title_main = book.get("title", "").split(":")[0]
        if DERIVED_EDITION_RE.search(book.get("title", "")):
            key -= DERIVED_EDITION_PENALTY
        elif probable_title and len(title_main) >= 4 and \
                fuzz.partial_ratio(title_main.lower(), probable_title.lower()) >= 90:
            key += TITLE_ON_COVER_BONUS
        if key > best_key:
            best_key = key
            best_book = book
            best_scores = (title_score, author_score, title_fit)

    title_score, author_score, title_fit = best_scores
    best_book.pop("_rank_bonus", None)   # internal ranking detail, not API data

    # TYPED-TITLE PATH: the user chose this title, so trust it. Accept the best
    # candidate whose title matches what they typed (lenient, so partial titles
    # like "Gatsby" and small typos still work). No author needed — they only
    # typed a title.
    if typed_title:
        if title_score < T_TITLE_TYPED:
            return {"error": "No matching book found", "confidence": "low"}
        best_book["confidence"] = "high" if title_score >= T_TITLE_HIGH else "medium"
        return best_book

    # SCAN PATH — QUALITY GATE. Accept the book if ANY of these hold:
    #   both_present  : the title AND the author both appear on the cover, OR
    #   is_title_cover: the cover is essentially just this title, OR
    #   strong_author : a strong author match backs up a present title, OR
    #   title_dominant: a very strong, cover-dominant title.
    # A random poster has a weak author and its title is a small fraction of its
    # text, so it fails all four and is rejected.
    both_present = title_score >= T_TITLE and author_score >= T_AUTHOR
    is_title_cover = title_fit >= T_FIT
    strong_author = author_score >= T_AUTHOR_STRONG and title_score >= T_TITLE_PRESENT
    title_dominant = title_score >= T_TITLE_DOMINANT and title_fit >= T_FIT_LOOSE
    if not (both_present or is_title_cover or strong_author or title_dominant):
        # LOW confidence: nothing convincing. The caller shows the "type the
        # title or scan the barcode" fallback instead of guessing a wrong book.
        return {"error": "No matching book found", "confidence": "low"}

    # AND, on top of whichever rule fired: enough of the candidate's OWN title
    # has to be on the cover. Without this a strong author match could carry a
    # candidate whose title is barely present, which is how a DIFFERENT BOOK
    # BY THE CORRECT AUTHOR gets through - the rule above named
    # `strong_author` is the one that does it, since its title bar
    # (T_TITLE_PRESENT) is met by a couple of common words.
    # Typed-title searches return earlier and never reach here: there the
    # "cover text" is what the user typed, so measuring coverage against it
    # would be circular.
    coverage = title_token_coverage(best_book.get("title", ""), cover_text)
    if coverage < T_TITLE_COVERAGE:
        return {"error": "No matching book found", "confidence": "low"}

    # PROMINENCE. How well does the matched title agree with the cover's
    # BIGGEST text? Computed once and used twice: as an acceptance rule here,
    # and to set the confidence band below.
    #
    # This is the signal that separates a book from ANOTHER BOOK BY THE SAME
    # AUTHOR named in small print. Presence cannot: a tagline like "a novel by
    # the author of THE SHINING" puts the wrong title genuinely ON the cover.
    # Only size tells them apart, and the height-ordered probable_title is
    # exactly that measurement.
    agreement = probable_title_agreement(best_book.get("title", ""),
                                         probable_title)
    if agreement is not None and agreement < T_ACCEPT_ON_PROBABLE:
        return {"error": "No matching book found", "confidence": "low"}

    # We accepted the book. Now grade HOW sure we are so the frontend can
    # either show it outright or ask the user to confirm:
    #   HIGH   - the title AND author are both strongly on the cover.
    #   MEDIUM - it passed the gate but not strongly (a title-only cover, a
    #            weaker author, or one of the loosened rules) -> confirm it.
    author_strict = fuzz.token_set_ratio(best_book.get("author", "").lower(),
                                         cover_text.lower())
    # Dominant-text agreement (see T_HIGH_ON_PROBABLE above): high confidence
    # requires the matched title to appear in the cover's BIGGEST text, not
    # only somewhere in the small print. `agreement` was computed above.
    on_probable = agreement is None or agreement >= T_HIGH_ON_PROBABLE
    if (title_score >= T_TITLE_HIGH and author_score >= T_AUTHOR_HIGH
            and author_strict >= T_AUTHOR_HIGH_STRICT and on_probable):
        best_book["confidence"] = "high"
    else:
        best_book["confidence"] = "medium"

    return best_book


# ---------------------------------------------------------------------------
# Verified-catalogue candidate funnel (used by the improved API)
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
REJECTED = "REJECTED"


def normalize_match_text(value):
    value = (value or "").casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_isbn(value):
    return re.sub(r"[^0-9Xx]", "", value or "").upper()


def valid_isbn(value):
    value = normalize_isbn(value)
    if len(value) == 13 and value.isdigit():
        total = sum(int(ch) * (1 if i % 2 == 0 else 3)
                    for i, ch in enumerate(value[:12]))
        return (10 - total % 10) % 10 == int(value[-1])
    if len(value) == 10 and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == "X"):
        total = sum((10 - i) * (10 if ch == "X" else int(ch))
                    for i, ch in enumerate(value))
        return total % 11 == 0
    return False


def _year(value):
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", str(value or ""))
    return match.group(1) if match else ""


def score_candidate(book, query_title, query_author="", query_isbn=""):
    """Return an explainable score without mutating the provider record."""
    title = normalize_match_text(book.get("title"))
    author = normalize_match_text(book.get("author"))
    q_title = normalize_match_text(query_title)
    q_author = normalize_match_text(query_author)
    q_isbn = normalize_isbn(query_isbn)
    candidate_isbns = {normalize_isbn(book.get("isbn_10")),
                       normalize_isbn(book.get("isbn_13"))}
    candidate_isbns.discard("")

    title_score = fuzz.token_set_ratio(q_title, title) if q_title and title else 0
    # ratio prevents a short generic word from earning 100 merely because it
    # is a subset of a much longer collection title.
    title_order_score = fuzz.ratio(q_title, title) if q_title and title else 0
    title_similarity = round(title_score * 0.65 + title_order_score * 0.35, 1)
    author_similarity = fuzz.token_set_ratio(q_author, author) if q_author and author else 0

    reasons = []
    penalties = []
    exact_isbn = bool(q_isbn and q_isbn in candidate_isbns)
    score = 0.0

    if exact_isbn:
        score = 100.0
        reasons.append("Exact ISBN match")
    else:
        score += title_similarity * 0.58
        if title_similarity >= 92:
            reasons.append("Title is an exact or near-exact match")
        elif title_similarity >= 75:
            reasons.append("Title is a close match")
        else:
            penalties.append("Title agreement is weak")

        if q_author:
            score += author_similarity * 0.32
            if author_similarity >= 85:
                reasons.append("Author matches")
            elif author_similarity < 55:
                score -= 35
                penalties.append("Author does not match")
            else:
                penalties.append("Author match is uncertain")
        else:
            # Missing author is not evidence against the book, but it caps
            # automatic acceptance later.
            reasons.append("Author was not provided; confirmation is required")

        if book.get("google_books_id") or book.get("open_library_key"):
            score += 3
        if candidate_isbns:
            score += 3
        if int(book.get("page_count") or 0) > 20:
            score += 2

    combined = " ".join(filter(None, (
        book.get("title", ""), book.get("categories", ""),
        book.get("publisher", ""))))
    unsafe = bool(UNSAFE_EDITION_RE.search(combined))
    if unsafe and not exact_isbn:
        score -= 60
        penalties.append("Derived edition, collection, guide, or adaptation")

    # A title that adds a large unrelated suffix is commonly a series/guide
    # record. Do not reject genuine subtitles outright, but make them confirm.
    if q_title and title and len(title.split()) >= len(q_title.split()) + 5 \
            and title_similarity < 90 and not exact_isbn:
        score -= 12
        penalties.append("Candidate title contains substantial extra wording")

    score = round(max(0.0, min(100.0, score)), 1)
    if exact_isbn:
        decision = HIGH_CONFIDENCE
    elif unsafe or title_similarity < 55 or (q_author and author_similarity < 35):
        decision = REJECTED
    elif score >= 82 and q_author and author_similarity >= 75 and title_similarity >= 85:
        decision = HIGH_CONFIDENCE
    elif score >= 55:
        decision = NEEDS_CONFIRMATION
    else:
        decision = REJECTED

    return {
        "score": score,
        "decision": decision,
        "reasons": reasons + penalties,
        "score_breakdown": {
            "title_similarity": title_similarity,
            "author_similarity": author_similarity if q_author else None,
            "exact_isbn": exact_isbn,
            "unsafe_edition": unsafe,
            "publication_year": _year(book.get("published_date")),
        },
    }


def rank_candidates(results, query_title, query_author="", query_isbn="", limit=5):
    """Rank candidates and assign the final funnel decision.

    A high candidate must also be clearly ahead of the runner-up. Ambiguity is
    presented to the user rather than hidden behind the highest numeric score.
    """
    ranked = []
    seen = set()
    for raw in results or []:
        identity = (raw.get("google_books_id") or raw.get("open_library_key") or
                    normalize_isbn(raw.get("isbn_13")) or
                    f"{normalize_match_text(raw.get('title'))}|{normalize_match_text(raw.get('author'))}")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        candidate = dict(raw)
        candidate.update(score_candidate(candidate, query_title, query_author, query_isbn))
        ranked.append(candidate)
    ranked.sort(key=lambda item: item["score"], reverse=True)

    plausible = [c for c in ranked if c["decision"] != REJECTED]
    if not plausible:
        final = REJECTED
    else:
        top = plausible[0]
        runner_score = plausible[1]["score"] if len(plausible) > 1 else 0
        if top["decision"] == HIGH_CONFIDENCE and top["score"] - runner_score >= 8:
            final = HIGH_CONFIDENCE
        else:
            final = NEEDS_CONFIRMATION
            top["decision"] = NEEDS_CONFIRMATION
            if runner_score and top["score"] - runner_score < 8:
                top["reasons"].append("Several candidates have similar scores")

    shown = plausible[:max(1, int(limit))]
    return {"decision": final, "candidates": shown,
            "rejected_count": len(ranked) - len(plausible)}


def _ocr_tokens(value, *, title=False):
    """Return meaningful tokens while keeping OCR comparisons deterministic."""
    tokens = re.findall(r"[a-z0-9']+", normalize_match_text(value))
    if title:
        meaningful = [token for token in tokens
                      if token not in TITLE_STOPWORDS and len(token) > 1]
        return meaningful or [token for token in tokens if token]
    # Initials are weak evidence by themselves. A surname/full given name is
    # useful; isolated C/S/J letters are not.
    meaningful = [token for token in tokens if len(token) > 1]
    return meaningful or [token for token in tokens if token]


def _ocr_token_coverage(expected, observed, *, title=False):
    """Fraction of canonical words visible anywhere in noisy cover OCR.

    This is order-independent and accepts the two common OCR joins we see on
    covers: adjacent words merged together (``andhis``) and a one-character
    recognition error. It never invents a word that is absent from the image.
    """
    wanted = _ocr_tokens(expected, title=title)
    seen = _ocr_tokens(observed)
    if not wanted or not seen:
        return 0.0
    found = 0
    for word in wanted:
        for token in seen:
            contained = len(word) >= 3 and (word in token or token in word)
            if word == token or contained or fuzz.ratio(word, token) >= 84:
                found += 1
                break
    return found / len(wanted)


def recover_ocr_candidates(results, probable_title="",
                           probable_author="", full_text="",
                           text_lines=None, limit=5):
    """Recover candidates from scrambled cover OCR.

    Visual hierarchy is useful, but a cover can make the author larger than
    the title, split a title over several lines, or include an illustrator
    credit. In those cases ``probable_title`` and ``probable_author`` can be
    wrong even though the *raw OCR lines* contain the correct evidence.

    This recovery compares candidate title/author metadata with all readable
    cover text. Candidates may come from the verified local catalogue or an
    external provider; the evidence and safety boundary are identical. It is
    deliberately conservative:

    * most canonical title words must actually be present;
    * short/generic titles also require author evidence;
    * every recovery is returned as NEEDS_CONFIRMATION, never silently saved.

    The function contains no book-specific aliases or corrections.
    """
    lines = [str(line).strip() for line in (text_lines or []) if str(line).strip()]
    observed_parts = [full_text, probable_title, probable_author, *lines]
    observed = " ".join(part for part in observed_parts if part).strip()
    if len(normalize_match_text(observed)) < 3:
        return {"decision": REJECTED, "candidates": [],
                "rejected_count": len(results or [])}

    # Height-sorted OCR lines put visually dominant text first. Use it only as
    # a small ranking signal: many legitimate covers make the author largest.
    prominent_parts = [probable_title, *lines[:4]]
    prominent_parts = [part for part in prominent_parts if part]
    ranked = []
    seen = set()
    for raw in results or []:
        identity = (raw.get("catalogue_id") or
                    f"{normalize_match_text(raw.get('title'))}|"
                    f"{normalize_match_text(raw.get('author'))}")
        if not identity or identity in seen:
            continue
        seen.add(identity)

        title = raw.get("title", "")
        author = raw.get("author", "")
        title_words = _ocr_tokens(title, title=True)
        title_coverage = _ocr_token_coverage(title, observed, title=True)
        author_coverage = _ocr_token_coverage(author, observed)
        title_fuzzy = max(fuzz.token_set_ratio(normalize_match_text(title),
                                               normalize_match_text(observed)),
                          fuzz.partial_ratio(normalize_match_text(title),
                                             normalize_match_text(observed)))
        author_fuzzy = 0
        if author:
            author_fuzzy = max(
                fuzz.token_set_ratio(normalize_match_text(author),
                                     normalize_match_text(observed)),
                fuzz.partial_ratio(normalize_match_text(author),
                                   normalize_match_text(observed)))
        prominence = max(
            [max(fuzz.token_set_ratio(normalize_match_text(title),
                                      normalize_match_text(part)),
                 fuzz.partial_ratio(normalize_match_text(title),
                                    normalize_match_text(part)))
             for part in prominent_parts] or [0])

        title_evidence = title_fuzzy * 0.55 + title_coverage * 100 * 0.45
        author_evidence = author_fuzzy * 0.60 + author_coverage * 100 * 0.40
        score = round(title_evidence * 0.68 + author_evidence * 0.24 +
                      prominence * 0.08, 1)

        # A recovery must be grounded in visible canonical title words. Short
        # titles (It, Us, Dune) additionally need a visible author because one
        # generic word alone is not enough identification evidence.
        short_title = len(title_words) <= 2
        plausible = (title_coverage >= 0.67 and title_evidence >= 72 and
                     (not short_title or author_evidence >= 68) and
                     (author_evidence >= 48 or
                      (title_coverage == 1.0 and prominence >= 82)))
        if not plausible:
            continue

        candidate = dict(raw)
        candidate.update({
            "score": max(0.0, min(100.0, score)),
            "decision": NEEDS_CONFIRMATION,
            "reasons": [
                "Title words recovered from the raw cover text",
                "Author evidence found in the cover text" if author_evidence >= 68
                else "Author is unclear; confirm the edition",
                "Recovered from scrambled OCR; confirmation required",
            ],
            "score_breakdown": {
                "title_similarity": round(title_evidence, 1),
                "author_similarity": round(author_evidence, 1),
                "title_token_coverage": round(title_coverage, 3),
                "author_token_coverage": round(author_coverage, 3),
                "prominence": prominence,
                "ocr_recovery": True,
            },
        })
        ranked.append(candidate)

    ranked.sort(key=lambda item: item["score"], reverse=True)
    shown = ranked[:max(1, int(limit))]
    if not shown:
        return {"decision": REJECTED, "candidates": [],
                "rejected_count": len(results or [])}

    # Keep close alternatives visible. A large margin improves ordering but
    # does not bypass confirmation—the OCR hierarchy has already proved noisy.
    if len(shown) > 1 and shown[0]["score"] - shown[1]["score"] < 8:
        shown[0]["reasons"].append("Several catalogue books have similar OCR evidence")
    return {"decision": NEEDS_CONFIRMATION, "candidates": shown,
            "rejected_count": len(results or []) - len(ranked),
            "recovered_from_ocr": True}


# Backwards-compatible, descriptive name used by older audit scripts.
recover_catalogue_candidates = recover_ocr_candidates
