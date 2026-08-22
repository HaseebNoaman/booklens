# matching.py
# THE MATCHING ALGORITHM — this is the core of the project.
#
# Everything here answers one question: given the text we read off a cover and
# a handful of candidate books from Google Books / Open Library, WHICH book is
# it, and how sure are we? The three jobs are deliberately separate:
#
#   score_candidate()        scores ONE candidate against the query
#   rank_candidates()        orders them and decides HIGH_CONFIDENCE,
#                            NEEDS_CONFIRMATION or REJECTED for the whole set
#   recover_ocr_candidates() rescues a match from raw cover text when the
#                            title guess itself was wrong
#
# It answers with a SET and a decision, never with a single book. That is the
# difference from the matcher this file used to hold: pick_best() returned one
# winner, so every scan produced an answer. Ranking with a refusal state is
# what lets the product say "I am not sure" instead. pick_best and its two
# scorers were deleted on 2026-08-23 once their last caller went; the
# thresholds they used are kept below, labelled, because each one records a
# measurement.
#
# Every threshold below was measured on the 100-cover benchmark, not guessed,
# and each one carries the measurement that set it. The honest-rejection
# behaviour is the point: the system says "no matching book found" rather than
# showing a confident wrong answer.
#
# This code used to live in api.py. It was moved out because api.py is the
# HTTP client layer (Google Books, Open Library) and the matching logic is a
# different concern that happens to consume its output. api.py re-exports every
# name defined here, so `api.rank_candidates`, `api.T_TITLE` and the rest keep
# working for app.py, the tests and every benchmark script.

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


# ---------------------------------------------------------------------------
# RETIRED THRESHOLDS -- kept as the measurement record, read by nothing.
#
# Every T_* constant from here down to T_ACCEPT_ON_PROBABLE belonged to
# pick_best(), the single-winner matcher this file used to hold. That function
# was deleted on 2026-08-23 after its last caller went, and the live matcher
# below (score_candidate / rank_candidates / recover_ocr_candidates) does not
# read one of them.
#
# They stay because each number is a measurement, and several record something
# that was TRIED AND REJECTED -- which is worth more than the number itself.
# Deleting them would delete the evidence, not just the code.
# ---------------------------------------------------------------------------

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
#
# The second group was added 2026-08-21 after 100 real cover photographs were
# run through the pipeline. Three of the failures were a derived product shown
# as the novel -- "Pride and Prejudice [adaptation]" by Fern Siegel, and a
# 7 Habits "Expert Guide ... in 30 Minutes" -- which is the worst error this
# product can make, because the reader is told it IS the book. Every added
# pattern was checked against all 250 catalogue titles and all 100 benchmark
# titles first: zero false matches.
#
# Deliberately NOT added: a rule for the "AUTHOR's TITLE written by somebody
# else" shape ("Cormac McCarthy's The Road" by Harold Bloom). Any regex broad
# enough to catch it also catches "Bridget Jones's Diary", and losing a real
# novel costs more than showing a criticism volume. Those stay a known limit.
UNSAFE_EDITION_RE = re.compile(
    r"\b(?:box(?:ed)?\s*set|complete\s+(?:set|collection|series|works)|"
    r"collection\s+of|omnibus|bundle|study\s*guide|summary(?:\s+and\s+analysis)?|"
    r"workbook|teacher(?:'s)?\s+edition|educator\s+edition|companion|"
    r"reader(?:'s)?\s+guide|film\s+adaptation|movie\s+tie[- ]?in|"
    r"screenplay|series\s+guide|e-?books?\s+collection|books?\s+collection|"
    r"novels?\s+collection|"
    r"adaptation|expert\s+guide|in\s+\d+\s+minutes|spark\s*notes|"
    r"cliffs?\s*notes|critical\s+(?:insights|essays|interpretations)|"
    r"bloom'?s\s+(?:modern\s+)?critical|analysis\s+of)\b",
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
# It needed its own measure because title_score is a fuzzy similarity: it
# cannot tell "most of this title is on the cover" from "these strings look
# alike". The function that computed it went with pick_best.
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
