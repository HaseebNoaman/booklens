"""Prepare provider text for safe, useful BookLens result cards.

This module deliberately does not identify books and does not score matches.
It runs only after an exact catalogue/provider candidate has been selected.
The responsibilities stay small and explainable:

1. turn HTML-heavy publisher descriptions into plain text;
2. remove obvious review/marketing boilerplate;
3. reject non-English overview input instead of showing mixed-language text;
4. try an English Open Library description only through the selected book's
   exact ISBN/work identifiers.

No text is generated here.  The summarizer remains behind summary_service.py.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


LANGUAGE_NAMES = {
    "en": "English",
    "id": "Indonesian",
    "nl": "Dutch",
    "sv": "Swedish",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "unknown": "Unknown",
}

# Function words are a more reliable signal than accents: English book blurbs
# frequently contain foreign names, while Google occasionally labels a
# translated description as English.  These compact sets are intentionally
# transparent and deterministic, not a hidden ML classifier.
_LANGUAGE_WORDS = {
    "en": {
        "a", "an", "and", "are", "as", "at", "be", "because", "book",
        "but", "by", "for", "from", "has", "have", "he", "her", "his",
        "in", "into", "is", "it", "its", "more", "not", "of", "on",
        "or", "she", "story", "than", "that", "the", "their", "them",
        "there", "they", "this", "to", "was", "when", "where", "which",
        "who", "will", "with", "without", "would", "you", "young",
    },
    "id": {
        "ada", "akan", "atau", "bagi", "buku", "dan", "dari", "dengan",
        "dia", "di", "ini", "itu", "jadi", "juga", "karena", "ketika",
        "karya", "lebih", "mereka", "namun", "pada", "sebagai", "sementara",
        "seseorang", "tak", "telah", "tentang", "tidak", "untuk", "yang",
    },
    "nl": {
        "aan", "als", "bij", "boek", "dan", "dat", "de", "door", "een",
        "en", "haar", "het", "hun", "in", "is", "maar", "met", "naar",
        "om", "op", "over", "te", "van", "voor", "wat", "ze", "zijn",
    },
    "sv": {
        "är", "att", "av", "boken", "den", "det", "du", "en", "ett",
        "för", "från", "har", "här", "i", "inte", "med", "men", "när",
        "och", "om", "på", "sig", "ska", "som", "till", "var", "över",
    },
    "de": {
        "aber", "als", "auch", "auf", "aus", "bei", "buch", "das", "dem",
        "den", "der", "die", "ein", "eine", "für", "hat", "im", "in",
        "ist", "mit", "nicht", "sich", "sie", "und", "von", "zu",
    },
    "fr": {
        "au", "aux", "avec", "ce", "cette", "dans", "de", "des", "du",
        "elle", "en", "est", "et", "il", "la", "le", "les", "livre",
        "mais", "ne", "pas", "pour", "qui", "se", "son", "une",
    },
    "es": {
        "al", "con", "de", "del", "el", "ella", "en", "es", "esta",
        "la", "las", "libro", "los", "más", "no", "para", "pero", "por",
        "que", "se", "su", "una", "y",
    },
    "pt": {
        "a", "ao", "com", "da", "de", "do", "e", "ela", "em", "é",
        "livro", "mais", "mas", "não", "o", "os", "para", "por", "que",
        "se", "sua", "um", "uma",
    },
    "it": {
        "a", "che", "con", "da", "del", "della", "di", "e", "è", "gli",
        "il", "in", "la", "libro", "ma", "non", "per", "si", "sua", "un",
        "una",
    },
}

_MARKETING_PHRASES = re.compile(
    r"\b(?:accolades?|an? (?:instant )?(?:#?1 )?(?:new york times )?bestseller|"
    r"bestselling author|don't miss|limited edition|while supplies last|"
    r"readers? (?:love|say)|praise for|pre-order|order now|reviewers? say|"
    r"other books in|reading order|five[- ]star|stars? (?:say|for)|"
    r"soon to be a major|film version|movie adaptation)\b",
    re.IGNORECASE,
)
_HASHTAG_TAIL = re.compile(r"(?:\s*#[\wÀ-ÿ-]+){2,}\s*$", re.UNICODE)
_INLINE_MARKETING_CLAUSE = re.compile(
    r",?\s*(?:the\s+)?#?\d*\s*"
    r"(?:(?:new york times|sunday times|usa today|international)\s+)?"
    r"bestselling author(?:\s+of\s+[^.!?]+)?",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+", re.UNICODE)


class _PlainTextParser(HTMLParser):
    """Extract text while preserving paragraph/list boundaries."""

    BLOCK_TAGS = {"br", "p", "div", "li", "ul", "ol", "h1", "h2", "h3"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ARG002 - HTMLParser contract
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def source_blocks(value: str) -> list[str]:
    """Return clean provider-text blocks without rendering provider HTML."""
    parser = _PlainTextParser()
    parser.feed(value or "")
    parser.close()
    parsed = html.unescape(parser.text()).replace("\u00a0", " ")
    blocks = []
    for raw in re.split(r"[\r\n]+", parsed):
        block = re.sub(r"\s+", " ", raw).strip(" _\t")
        block = _HASHTAG_TAIL.sub("", block).strip()
        if block:
            blocks.append(block)
    return blocks


def _looks_like_review(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return True
    if _MARKETING_PHRASES.search(stripped):
        return True
    if stripped.count("#") >= 2:
        return True
    # Short quoted blurbs are endorsements, not descriptions of the book.
    if len(stripped) < 280 and stripped[:1] in {'"', "'", "“", "‘"}:
        return True
    return False


def clean_source_text(value: str, *, remove_marketing: bool = True) -> str:
    """Convert HTML/publisher copy to readable plain text.

    The complete sentences are preserved.  We remove only obvious promotional
    blocks and repeated hashtag tails; no facts are added or rewritten.
    """
    blocks = source_blocks(value)
    if remove_marketing:
        useful = [block for block in blocks if not _looks_like_review(block)]
        # If a provider supplied only one unusual block, preserving it is more
        # honest than silently turning an existing description into "missing".
        if useful:
            blocks = useful
    text = " ".join(blocks)
    # Providers sometimes place an accolade inside an otherwise useful block.
    # Remove only that clause instead of throwing away the exact description.
    text = _INLINE_MARKETING_CLAUSE.sub("", text)
    text = _HASHTAG_TAIL.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_language(value: str) -> dict:
    """Detect common provider-description languages with explainable scores."""
    cleaned = clean_source_text(value, remove_marketing=False)
    tokens = [token.lower().strip("'") for token in _WORD_RE.findall(cleaned)]
    if len(tokens) < 6:
        return {"code": "unknown", "name": LANGUAGE_NAMES["unknown"],
                "confidence": "low", "scores": {}}

    scores = {
        code: sum(1 for token in tokens if token in words)
        for code, words in _LANGUAGE_WORDS.items()
    }
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_code, best_score = ordered[0]
    second_score = ordered[1][1]
    minimum = 2 if len(tokens) < 25 else 3
    if best_score < minimum or best_score <= second_score:
        best_code = "unknown"
        confidence = "low"
    else:
        margin = best_score - second_score
        confidence = "high" if best_score >= 5 and margin >= 2 else "medium"
    return {
        "code": best_code,
        "name": LANGUAGE_NAMES.get(best_code, best_code.upper()),
        "confidence": confidence,
        "scores": scores,
    }


def language_for_client(value: str) -> dict | None:
    """Return a compact language label only when text provides evidence."""
    result = detect_language(value)
    if result["code"] == "unknown":
        return None
    return {key: result[key] for key in ("code", "name", "confidence")}


def _exact_open_library_sources(book: dict):
    """Yield exact ISBN/work descriptions without performing a title search."""
    # Imported lazily to keep this presentation service separate from the
    # frozen identification module and to avoid adding an app import cycle.
    from api import (_ol_text, get_open_library_edition,
                     get_open_library_work_description)

    isbn = (book.get("isbn_13") or book.get("isbn_10") or "").strip()
    work_key = (book.get("open_library_key") or
                book.get("open_library_work_id") or "").strip()
    edition = get_open_library_edition(isbn) if isbn else None
    if edition:
        edition_text = _ol_text(edition.get("description", ""))
        if edition_text:
            yield edition_text, "openlibrary_edition"
        if not work_key:
            works = edition.get("works") or []
            if works:
                work_key = (works[0].get("key") or "").strip()
    if work_key:
        work_text = get_open_library_work_description(work_key)
        if work_text:
            yield work_text, "openlibrary_work"


def prepare_external_source(book: dict, resolved: dict) -> dict:
    """Prepare exact external text for the frozen summarizer adapter.

    Non-English text is never translated or summarized from model memory.  We
    may fall through to Open Library only through the selected ISBN/work.
    """
    raw = (resolved or {}).get("text") or ""
    source = (resolved or {}).get("source")
    reason = (resolved or {}).get("reason") or ""
    cleaned = clean_source_text(raw)
    language = detect_language(cleaned)

    if cleaned and language["code"] == "en":
        return {"model_text": cleaned, "display_text": cleaned,
                "source": source, "reason": "", "language": language}

    # Preserve the existing provider-failure contract. An English fallback is
    # a response to a known foreign source, not a hidden retry when every exact
    # source was already reported unavailable.
    if not cleaned:
        return {"model_text": None, "display_text": "", "source": None,
                "reason": reason or "sources_had_no_description",
                "language": language}

    # A foreign Google edition can still map to an English Open Library work
    # through its exact ISBN.  This broadens description availability without
    # weakening candidate matching or choosing a fresh title-search result.
    for fallback_text, fallback_source in _exact_open_library_sources(book):
        fallback_clean = clean_source_text(fallback_text)
        fallback_language = detect_language(fallback_clean)
        if fallback_clean and fallback_language["code"] == "en":
            return {"model_text": fallback_clean,
                    "display_text": fallback_clean,
                    "source": fallback_source, "reason": "",
                    "language": fallback_language}

    code = language["code"]
    return {"model_text": None, "display_text": cleaned,
            "source": f"{source or 'provider'}_non_english",
            "reason": ("non_english_source" if code != "unknown"
                       else "source_language_uncertain"),
            "language": language}
