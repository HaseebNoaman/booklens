"""Deterministic, grounded What-It's-About selection for Tier-2 books.

This module runs only after the existing matcher has selected an exact external
record. It retrieves descriptions through exact IDs, cleans provider noise,
builds single-sentence and adjacent-pair windows, and ranks those windows with
small rules that can be explained in a viva. It never generates new prose.
"""

from __future__ import annotations

import re
from typing import Iterable

from result_content import clean_source_text, detect_language


# v2: promotional verbs are rejected, and a predominantly promotional source
# yields no overview. Bumping the version makes cached v1 summaries -- which
# may contain the marketing this rule exists to remove -- regenerate.
METHOD = "candidate_window_heuristic_v2"
MIN_WORDS = 25
PREFERRED_MIN_WORDS = 30
PREFERRED_MAX_WORDS = 50
MAX_WORDS = 65
MIN_SCORE = 11

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['’][A-Za-zÀ-ÿ]+)?", re.UNICODE)
_CONTENT_RE = re.compile(r"[A-Za-zÀ-ÿ]{3,}", re.UNICODE)
_END_RE = re.compile(r"[.!?][\"'”’)]*$")

_ABBREVIATION_RE = re.compile(
    r"\b(Mr|Mrs|Ms|Dr|Prof|St|Sr|Jr|vs|etc|e\.g|i\.e)\.", re.IGNORECASE
)
_INITIAL_RE = re.compile(r"\b([A-Z])\.")

_CTA_RE = re.compile(
    r"\b(?:buy|order|pre[- ]?order|download|listen(?: now)?|click here|"
    r"visit (?:our|the) (?:site|website)|available now|read now|start reading|"
    r"add to (?:your )?cart|don't miss|subscribe|sign up)\b",
    re.IGNORECASE,
)
_AWARD_RE = re.compile(
    r"\b(?:award[- ]winning|winner of|finalist for|medal[- ]winning|"
    r"newbery|pulitzer|booker prize|book of the year|bestsell(?:er|ing)|"
    r"critically acclaimed|internationally acclaimed|modern classic|"
    r"masterpiece|millions of (?:copies|readers))\b",
    re.IGNORECASE,
)
_REVIEW_RE = re.compile(
    r"\b(?:praise for|critics? (?:say|call)|reviewers? (?:say|call)|"
    r"five[- ]star|a triumph|unputdownable|must[- ]read|readers? love)\b",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(
    r"^\s*(?:book jacket|jacket copy|from the (?:publisher|editor)|"
    r"editorial reviews?|product description|about the author|synopsis|"
    r"description|fiction|nonfiction|young adult|children'?s books?|"
    r"historical fiction|literary fiction)\s*[:\-]?\s*$",
    re.IGNORECASE,
)
_BIBLIOGRAPHIC_RE = re.compile(
    r"\b(?:first published|originally published|this edition|new edition|"
    r"revised edition|anniversary edition|translated by|translation by|"
    r"foreword by|introduction by|afterword by|isbn[- :]?|pages?\b|"
    r"publication date|publisher:)\b",
    re.IGNORECASE,
)
_PUBLICATION_ONLY_RE = re.compile(
    r"\b(?:published|publication|edition|volume|series|sequel|debut novel)\b",
    re.IGNORECASE,
)
_MARKETING_RE = re.compile(
    r"\b(?:stunning|breathtaking|gripping|riveting|compelling|unforgettable|"
    r"extraordinary|remarkable|beloved|celebrated|powerful|essential|definitive|"
    r"ground[- ]breaking|page[- ]turning)\b",
    re.IGNORECASE,
)
# _MARKETING_RE above catches promotional ADJECTIVES. These are promotional
# VERBS and second-person promises, which went straight onto the card: "The 10X
# Rule unveils the principle of Massive Action, allowing you to blast through
# business cliches ... and reach your dreams."
#
# Deliberately narrow. It does not reject every use of "you" -- non-fiction
# descriptions legitimately address the reader ("this book explains how you
# can ..."), and a blanket rule would wipe out non-fiction coverage entirely.
# Only aspirational promises are listed.
_PROMO_RE = re.compile(
    r"\b(?:unveils? the (?:principle|system|method|formula|blueprint)|"
    r"unlocks? (?:the (?:secrets?|potential|power)|your)|"
    r"blast through|transform your|reach your dreams|"
    r"change your life|proven (?:system|formula|method)|"
    r"guarantees? (?:companies|you|readers)|"
    r"step[- ]by[- ]step (?:guide|blueprint|system)|secrets? (?:to|of) success|"
    r"everything you need to know|will show you how|teaches you how to|"
    r"realize (?:their|your) (?:goals|dreams)|goals and dreams)\b",
    re.IGNORECASE,
)

# Above this many promotional sentences, the SOURCE is an advertisement rather
# than a description. Hunting for the least-bad sentence inside a sales pitch
# just yields a quieter sales pitch, so the honest answer is no overview at all
# -- the same choice the identification side makes when evidence is too weak.
_PROMO_SOURCE_LIMIT = 2

_CATEGORY_RE = re.compile(
    r"\b(?:perfect for fans of|ideal for readers|for fans of|book club pick|"
    r"reading group guide|ages? \d+|grades? \d+|the \w+ book in the series)\b",
    re.IGNORECASE,
)

_CHARACTER_RE = re.compile(
    r"\b(?:he|she|they|her|his|their|girl|boy|woman|man|child|children|"
    r"mother|father|daughter|son|family|friends?|detective|scientist|student|"
    r"writer|doctor|soldier|teacher|king|queen|prince|princess|hero|heroine)\b",
    re.IGNORECASE,
)
_START_RE = re.compile(
    r"\b(?:when|after|before|once|as |returns?|lives?|begins?|is born|"
    r"grows up|finds (?:himself|herself|themselves)|on the eve|set in|"
    r"in a world|at the start|newly|young|following)\b",
    re.IGNORECASE,
)
_PREMISE_RE = re.compile(
    r"\b(?:must|faces?|forced|discovers?|struggles?|tries?|seeks?|threatens?|"
    r"danger|surviv(?:e|al)|escapes?|protects?|saves?|fights?|battle|mystery|"
    r"secret|choice|mission|quest|investigates?|murder|war|vanishes?|"
    r"disappears?|risk|challenge|against|only hope|has to|cannot|can't)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(?:must|decides?|sets out|returns?|leaves?|travels?|joins?|takes?|"
    r"searches?|investigates?|fights?|tries?|discovers?|finds?|faces?|"
    r"struggles?|learns?|seeks?|builds?|creates?|crosses?|refuses?)\b",
    re.IGNORECASE,
)
_CONNECTOR_RE = re.compile(
    r"\b(?:but|however|until|forcing|leaving|yet|although|while|so that)\b",
    re.IGNORECASE,
)

_NONFICTION_CATEGORY_RE = re.compile(
    r"\b(?:biograph|memoir|history|science|psychology|business|self[- ]help|"
    r"philosophy|politic|economic|education|health|travel|religion|true crime|"
    r"social science|nature|environment|technology|mathematics|medical)\b",
    re.IGNORECASE,
)
_FICTION_CATEGORY_RE = re.compile(
    r"\b(?:fiction|novel|young adult|juvenile|children|fantasy|romance|mystery|"
    r"thriller|science fiction|literary)\b",
    re.IGNORECASE,
)
_THESIS_RE = re.compile(
    r"\b(?:argues?|examines?|explains?|explores?|shows?|reveals?|traces?|"
    r"investigates?|demonstrates?|considers?|challenges?|documents?|"
    r"illuminates?|asks?|offers? an account|makes the case)\b",
    re.IGNORECASE,
)
_IDEA_RE = re.compile(
    r"\b(?:how|why|theory|evidence|history|idea|argument|relationship|impact|"
    r"role|causes?|effects?|meaning|understanding|science|system|society|"
    r"culture|mind|brain|body|nature|economy|politics|trauma|sleep)\b",
    re.IGNORECASE,
)
_SUBJECT_RE = re.compile(
    r"\b(?:book|account|study|history|investigation|portrait|memoir|work|"
    r"research|story of|life of|science of|world of)\b",
    re.IGNORECASE,
)
_SCOPE_RE = re.compile(
    r"\b(?:from .{2,45} to|across|throughout|over (?:the|a) |centuries|"
    r"around the world|wide[- ]ranging)\b",
    re.IGNORECASE,
)

_SPOILER_RE = re.compile(
    r"\b(?:the (?:killer|culprit|murderer) (?:is|was|turns out to be)|"
    r"revealed (?:as|to be) the (?:killer|culprit|murderer)|"
    r"ending reveals?|final twist|concludes? with|ends? with|"
    r"in the end .{0,70}(?:wins?|defeats?|kills?|escapes?|succeeds?|dies?|"
    r"reunites?|is saved)|"
    r"by the end .{0,70}(?:wins?|defeats?|kills?|escapes?|succeeds?|dies?)|"
    r"ultimately .{0,50}(?:wins?|defeats?|kills?|is revealed|turns out))\b",
    re.IGNORECASE,
)

_COMMON_CAPITALIZED = {
    "A", "An", "And", "As", "At", "After", "Before", "But", "For",
    "From", "He", "Her", "His", "However", "In", "It", "Its", "On",
    "Once", "She", "The", "Their", "They", "This", "When", "While",
    "With", "Without", "Young",
}
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for",
    "from", "has", "have", "he", "her", "his", "in", "into", "is",
    "it", "its", "of", "on", "or", "she", "that", "the", "their",
    "they", "this", "to", "was", "when", "which", "who", "with",
}


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def word_count(text: str) -> int:
    return len(words(text))


def split_sentences(text: str) -> list[str]:
    """Split provider prose while protecting common abbreviations/initials."""
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        return []
    value = _ABBREVIATION_RE.sub(lambda match: match.group(0).replace(".", "<DOT>"), value)
    value = _INITIAL_RE.sub(lambda match: f"{match.group(1)}<DOT>", value)
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'“‘(]*[A-Z0-9])", value)
    return [part.replace("<DOT>", ".").strip() for part in parts if part.strip()]


def _has_story_or_thesis(sentence: str) -> bool:
    return bool(_CHARACTER_RE.search(sentence) or _PREMISE_RE.search(sentence)
                or _THESIS_RE.search(sentence) or _IDEA_RE.search(sentence))


def sentence_is_junk(sentence: str) -> bool:
    """Return True only for strong, generic provider-noise signals."""
    value = sentence.strip()
    if not value or _HEADING_RE.match(value):
        return True
    if word_count(value) < 5:
        return True
    if _CTA_RE.search(value) or _REVIEW_RE.search(value):
        return True
    if _PROMO_RE.search(value):
        return True
    if _AWARD_RE.search(value) and not _has_story_or_thesis(value):
        return True
    if _BIBLIOGRAPHIC_RE.search(value) and not _has_story_or_thesis(value):
        return True
    if _PUBLICATION_ONLY_RE.search(value) and not _has_story_or_thesis(value):
        return True
    if value[:1] in {'"', "'", "“", "‘"} and word_count(value) <= 30:
        return True
    return False


def clean_description(raw_text: str) -> dict:
    """Return cleaned, complete provider sentences and transparent removals."""
    plain = clean_source_text(raw_text or "")
    language = detect_language(plain)
    kept = []
    removed = []
    for sentence in split_sentences(plain):
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if sentence_is_junk(sentence):
            removed.append(sentence)
            continue
        if not _END_RE.search(sentence) or sentence.endswith(("...", "…", ":", ";")):
            removed.append(sentence)
            continue
        kept.append(sentence)
    # A description can survive sentence-level filtering and still be an
    # advertisement overall. When most of what is left is selling, publish
    # nothing rather than the quietest sales line in it.
    # Count across the WHOLE source, not just what survived. Promotional
    # sentences are already dropped above, so counting only the survivors would
    # always report zero and this rule would never fire.
    if sum(1 for s in kept + removed
           if _PROMO_RE.search(s)) >= _PROMO_SOURCE_LIMIT:
        removed.extend(kept)
        kept = []

    return {
        "text": " ".join(kept),
        "sentences": kept,
        "removed_sentences": removed,
        "language": language,
    }


def infer_kind(categories: str = "", explicit_kind: str = "") -> str:
    explicit = (explicit_kind or "").strip().lower()
    if explicit in {"fiction", "nonfiction"}:
        return explicit
    value = categories or ""
    if _NONFICTION_CATEGORY_RE.search(value):
        return "nonfiction"
    if _FICTION_CATEGORY_RE.search(value):
        return "fiction"
    return "fiction"


def _title_tokens(title: str) -> set[str]:
    return {
        token.lower() for token in _CONTENT_RE.findall(title or "")
        if token.lower() not in _STOPWORDS and len(token) >= 4
    }


def _content_tokens(text: str) -> set[str]:
    return {
        token.lower() for token in _CONTENT_RE.findall(text or "")
        if token.lower() not in _STOPWORDS
    }


def has_probable_name(text: str) -> bool:
    """Detect a likely named entity without any book-specific name list."""
    for match in re.finditer(
        r"\b[A-Z][a-z]+(?:['’][A-Za-z]+)?(?:\s+[A-Z][a-z]+(?:['’][A-Za-z]+)?){0,2}",
        text or "",
    ):
        phrase = match.group(0)
        if phrase not in _COMMON_CAPITALIZED and len(phrase) >= 4:
            return True
    return False


def _pair_disconnected(sentences: list[str]) -> bool:
    if len(sentences) != 2:
        return False
    first, second = sentences
    overlap = _content_tokens(first) & _content_tokens(second)
    linked_start = re.match(
        r"^(?:he|she|they|it|this|that|these|those|but|however|when|after|"
        r"as|yet|while|his|her|their)\b",
        second,
        re.IGNORECASE,
    )
    return not overlap and not linked_start


def _length_points(count: int) -> int:
    if PREFERRED_MIN_WORDS <= count <= PREFERRED_MAX_WORDS:
        return 6
    if MIN_WORDS <= count <= 29 or 51 <= count <= 60:
        return 4
    if 61 <= count <= MAX_WORDS:
        return 2
    return -100


def score_candidate(text: str, sentences: list[str], *, title: str,
                    kind: str, sentence_index: int) -> dict:
    """Score one extractive window using the frozen, additive rules."""
    count = word_count(text)
    signals = {
        "preferred_length": PREFERRED_MIN_WORDS <= count <= PREFERRED_MAX_WORDS,
        "title_overlap": bool(_title_tokens(title) & _content_tokens(text)),
        "connector": bool(_CONNECTOR_RE.search(text)),
        "marketing_residue": bool(_CTA_RE.search(text) or _MARKETING_RE.search(text)),
        "publication_residue": bool(_AWARD_RE.search(text) or _REVIEW_RE.search(text)
                                    or _BIBLIOGRAPHIC_RE.search(text)),
        "category_residue": bool(_CATEGORY_RE.search(text)),
        "disconnected_pair": _pair_disconnected(sentences),
        "spoiler_resolution": bool(_SPOILER_RE.search(text)),
    }
    score = float(_length_points(count))
    if signals["title_overlap"]:
        score += 2
    if signals["connector"]:
        score += 2

    if kind == "nonfiction":
        signals.update({
            "subject": bool(signals["title_overlap"] or _SUBJECT_RE.search(text)
                            or has_probable_name(text)),
            "thesis": bool(_THESIS_RE.search(text)),
            "idea": bool(_IDEA_RE.search(text)),
            "scope": bool(_SCOPE_RE.search(text)),
        })
        if signals["subject"]:
            score += 4
        if signals["thesis"]:
            score += 6
        if signals["idea"]:
            score += 3
        if signals["scope"]:
            score += 2
        required_signals = signals["subject"] and signals["thesis"]
    else:
        signals.update({
            "named_person": has_probable_name(text),
            "character": bool(has_probable_name(text) or _CHARACTER_RE.search(text)),
            "starting_situation": bool(_START_RE.search(text)),
            "premise": bool(_PREMISE_RE.search(text)),
            "action": bool(_ACTION_RE.search(text)),
        })
        if signals["named_person"]:
            score += 4
        if signals["character"]:
            score += 2
        if signals["starting_situation"]:
            score += 2
        if signals["premise"]:
            score += 5
        if signals["action"]:
            score += 3
        required_signals = signals["character"] and signals["premise"]

    if signals["marketing_residue"]:
        score -= 8
    if signals["publication_residue"]:
        score -= 8
    if signals["category_residue"]:
        score -= 6
    if signals["disconnected_pair"]:
        score -= 3
    score -= sentence_index * 0.15

    accepted = (
        MIN_WORDS <= count <= MAX_WORDS
        and len(sentences) in {1, 2}
        and all(_END_RE.search(sentence) for sentence in sentences)
        and not signals["spoiler_resolution"]
        and required_signals
        and score >= MIN_SCORE
    )
    return {
        "score": round(score, 3),
        "accepted": accepted,
        "word_count": count,
        "sentence_count": len(sentences),
        "signals": signals,
    }


def generate_candidate_windows(sentences: list[str]) -> Iterable[tuple[int, list[str]]]:
    for index, sentence in enumerate(sentences):
        yield index, [sentence]
        if index + 1 < len(sentences):
            yield index, [sentence, sentences[index + 1]]


def _why_won(candidate: dict, kind: str) -> str:
    signals = candidate["signals"]
    reasons = []
    if signals.get("preferred_length"):
        reasons.append("preferred 30–50 word length")
    if kind == "fiction":
        if signals.get("character"):
            reasons.append("character-bearing")
        if signals.get("starting_situation"):
            reasons.append("starting situation")
        if signals.get("premise"):
            reasons.append("central conflict/premise")
        if signals.get("action"):
            reasons.append("character action")
    else:
        if signals.get("subject"):
            reasons.append("main-subject signal")
        if signals.get("thesis"):
            reasons.append("thesis/examination signal")
        if signals.get("idea"):
            reasons.append("central-idea signal")
    return ", ".join(reasons) or "highest accepted frozen-rule score"


def select_provider_lead(sources: list[dict]) -> dict:
    """Return the first safe 1–2 sentence lead from the first usable source."""
    for source in sources:
        cleaned = clean_description(source.get("text", ""))
        if cleaned["language"].get("code") != "en":
            continue
        sentences = cleaned["sentences"]
        for size in (1, 2):
            if len(sentences) < size:
                continue
            chosen = sentences[:size]
            text = " ".join(chosen)
            count = word_count(text)
            if (MIN_WORDS <= count <= MAX_WORDS and not _SPOILER_RE.search(text)
                    and all(_END_RE.search(sentence) for sentence in chosen)):
                return {
                    "status": "ready",
                    "overview": text,
                    "source": source.get("source"),
                    "verification": source.get("verification"),
                    "source_text": cleaned["text"],
                    "word_count": count,
                    "sentence_count": size,
                }
    return {"status": "unavailable", "overview": "",
            "reason": "no_acceptable_provider_lead"}


def select_what_its_about(sources: list[dict], *, title: str = "",
                          categories: str = "", kind: str = "") -> dict:
    """Rank extractive windows from every verified exact-provider source."""
    resolved_kind = infer_kind(categories, kind)
    candidates = []
    source_audit = []
    for source_index, source in enumerate(sources):
        cleaned = clean_description(source.get("text", ""))
        audit = {
            "source": source.get("source"),
            "verification": source.get("verification"),
            "language": cleaned["language"],
            "kept_sentences": len(cleaned["sentences"]),
            "removed_sentences": len(cleaned["removed_sentences"]),
        }
        source_audit.append(audit)
        if cleaned["language"].get("code") != "en":
            continue
        for sentence_index, window in generate_candidate_windows(cleaned["sentences"]):
            text = " ".join(window)
            scored = score_candidate(
                text, window, title=title, kind=resolved_kind,
                sentence_index=sentence_index,
            )
            candidate = {
                **scored,
                "text": text,
                "source_index": source_index,
                "sentence_index": sentence_index,
                "source": source.get("source"),
                "verification": source.get("verification"),
                "source_text": cleaned["text"],
            }
            candidates.append(candidate)

    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    if not accepted:
        if not sources:
            reason = "no_exact_provider_description"
        elif all(audit["language"].get("code") != "en" for audit in source_audit):
            reason = "no_verified_english_description"
        else:
            reason = "no_candidate_passed_quality_gates"
        return {
            "status": "unavailable",
            "overview": "",
            "method": METHOD,
            "kind": resolved_kind,
            "reason": reason,
            "candidate_count": len(candidates),
            "source_audit": source_audit,
        }

    accepted.sort(key=lambda item: (
        -item["score"],
        item["source_index"],
        item["sentence_index"],
        item["sentence_count"],
        item["text"],
    ))
    winner = accepted[0]
    return {
        "status": "ready",
        "overview": winner["text"],
        "method": METHOD,
        "kind": resolved_kind,
        "source": winner["source"],
        "verification": winner["verification"],
        "source_text": winner["source_text"],
        "score": winner["score"],
        "word_count": winner["word_count"],
        "sentence_count": winner["sentence_count"],
        "signals": winner["signals"],
        "why_won": _why_won(winner, resolved_kind),
        "candidate_count": len(candidates),
        "accepted_candidate_count": len(accepted),
        "source_audit": source_audit,
    }


def collect_exact_provider_sources(book: dict) -> dict:
    """Collect Google and Open Library descriptions without a title search."""
    from api import (
        _ol_text,
        get_open_library_edition,
        get_open_library_work_description,
        get_volume_by_id,
        is_usable_description,
        search_by_isbn,
    )
    from matching import normalize_isbn

    sources = []
    attempts = []
    seen_texts = set()

    def add(text: str, source: str, verification: str):
        value = (text or "").strip()
        attempts.append({"source": source, "verification": verification,
                         "usable": bool(is_usable_description(value))})
        if not is_usable_description(value):
            return
        dedupe_key = re.sub(r"\s+", " ", clean_source_text(value)).strip().lower()
        if not dedupe_key or dedupe_key in seen_texts:
            return
        seen_texts.add(dedupe_key)
        sources.append({"text": value, "source": source,
                        "verification": verification})

    volume_id = (book.get("google_books_id") or "").strip()
    isbn = normalize_isbn(book.get("isbn_13") or book.get("isbn_10") or "")
    work_key = (book.get("open_library_work_id") or
                book.get("open_library_key") or "").strip()

    if volume_id:
        exact_google = get_volume_by_id(volume_id)
        if exact_google:
            add(exact_google, "google_volume", "selected_google_volume_id")
        else:
            add(book.get("description", ""), "google_volume",
                "selected_google_search_record")
    elif isbn:
        google_by_isbn = search_by_isbn(isbn)
        returned_isbns = {
            normalize_isbn(google_by_isbn.get("isbn_13", "")),
            normalize_isbn(google_by_isbn.get("isbn_10", "")),
        }
        if "error" not in google_by_isbn and isbn in returned_isbns:
            google_text = (google_by_isbn.get("description") or
                           get_volume_by_id(google_by_isbn.get("google_books_id", "")))
            add(google_text, "google_volume", "same_selected_isbn")

    edition = get_open_library_edition(isbn) if isbn else None
    if edition:
        add(_ol_text(edition.get("description", "")), "openlibrary_edition",
            "same_selected_isbn")
        works = edition.get("works") or []
        if works:
            edition_work_key = (works[0].get("key") or "").strip()
            if edition_work_key:
                work_key = work_key or edition_work_key

    if work_key:
        if not work_key.startswith("/"):
            work_key = f"/works/{work_key}" if not work_key.startswith("works/") else f"/{work_key}"
        add(get_open_library_work_description(work_key), "openlibrary_work",
            "selected_or_isbn_linked_work_id")

    if not volume_id and not isbn and not work_key:
        reason = "no_exact_external_identifier"
    elif not sources:
        reason = "exact_sources_had_no_usable_description"
    else:
        reason = ""
    return {"sources": sources, "attempts": attempts, "reason": reason}


def build_external_overview(book: dict, *, kind: str = "") -> dict:
    collected = collect_exact_provider_sources(book)
    result = select_what_its_about(
        collected["sources"],
        title=book.get("title", ""),
        categories=book.get("categories", ""),
        kind=kind,
    )
    result["provider_attempts"] = collected["attempts"]
    if result["status"] != "ready" and collected["sources"]:
        # A declined overview may still expose the cleaned exact-provider text
        # in the collapsed source panel. This is display provenance, not a
        # claim that the text passed the overview quality gate.
        first_source = collected["sources"][0]
        first_cleaned = clean_description(first_source.get("text", ""))
        result["source_text"] = first_cleaned["text"]
        result["source"] = first_source.get("source")
        result["verification"] = first_source.get("verification")
    if result["status"] != "ready" and collected["reason"]:
        result["reason"] = collected["reason"]
    return result
