"""The publisher's own description, minus the parts that are not about the book.

This module runs only after the matcher has selected an exact external record.
It retrieves descriptions through exact IDs and removes provider noise. It never
generates prose, and -- since 2026-08-23 -- it no longer CHOOSES prose either.

It used to. Every one- and two-sentence window was scored, and a window also
had to contain a word from a fixed list ("must", "faces", "discovers",
"danger"...) or it was refused. Measured on 190 books outside the catalogue,
that whitelist left 51% of cards blank, and 91 of those 97 blanks had a
perfectly readable publisher description on the same screen. The Tale of
Despereaux was refused because "a devious rat determined to bring them all to
ruin" is not on the list.

A whitelist over an open vocabulary cannot be completed -- the same lesson the
provider/shelf synonym map already bought. So the rules were turned around.
They now say what is NOT a description: markup, marketing, critical reception,
an encyclopaedia opener, a publisher's reprint notice, chapter structure,
spoilers. Everything else is the answer, in the publisher's own order.

The only choice left is between SOURCES -- the first record that reads like a
description wins -- which is a much smaller claim to defend, and it makes a bad
card structurally impossible: the worst this can print is a real sentence the
publisher wrote, never a machine's pick of the least-bad one.
"""

from __future__ import annotations

import re

from result_content import clean_source_text, detect_language


# v3: nothing is extracted any more -- see the module docstring. Bumping the
# version makes cached v1 and v2 overviews, which were single windows chosen by
# the retired scorer, regenerate as cleaned descriptions.
METHOD = "cleaned_provider_description_v3"

# The card is a short PARAGRAPH now, not a one-or-two-sentence window, because
# nothing is being extracted -- the publisher's sentences are shown in the
# order they were written, minus the ones that are not about the book.
#
# MIN_WORDS was 25, chosen when a fragment shorter than that meant the picker
# had found nothing worth showing. It is 15 here because a whole short
# description is a complete answer: "The story of a Cro-Magnon woman orphaned
# as a child and raised by a group of Neanderthals" is 18 words and is the
# entire Open Library record for The Clan of the Cave Bear. The 25-word floor
# was throwing that away, along with Carrie (17) and Watership Down (24).
#
# MAX_WORDS was 65 for the same reason and is 90 here: measured over 190 books,
# the median cleaned description runs 71 words, so 65 cut most of them
# mid-paragraph. 90 keeps three or four sentences and stops before an essay.
MIN_WORDS = 15
MAX_WORDS = 90

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['’][A-Za-zÀ-ÿ]+)?", re.UNICODE)
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
    r"masterpiece|millions of (?:copies|readers)|"
    # "This story of heroic endeavour WON HEMINGWAY THE NOBEL PRIZE" scored
    # 18.85 and would have been The Old Man and the Sea's description. A prize
    # is a fact about the book's reception, not about what happens in it.
    r"nobel prize|national book award|won (?:\w+ ){0,3}the \w+ prize)\b",
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
    r"publication date|publisher:|"
    # The encyclopaedia opener. "The Da Vinci Code is a 2003 mystery thriller
    # novel by Dan Brown" scored 19.0 and told a reader nothing about the book;
    # its own stored summary opens on the murder in the Louvre and scored 22.
    # "And Then There Were None is a mystery novel by the English writer Agatha
    # Christie" is the same shape.
    r"is a \d{4}\b|novel by the \w+ writer|"
    r"(?:second|third|fourth|fifth|debut) novel)\b",
    re.IGNORECASE,
)
_PUBLICATION_ONLY_RE = re.compile(
    r"\b(?:published|publication|edition|volume|series|sequel|debut novel)\b",
    re.IGNORECASE,
)
# A window that opens on a pronoun or a bare connective is quoting from the
# middle of something, and the reader has no way to know who "they" are. The
# card was showing "They want to change her and never let her go" for Coraline,
# "He has a little brother, Manny" for Diary of a Wimpy Kid, "His family
# accompanies him on this job" for The Shining, and "However, his plane crashes"
# for Hatchet. Each is a true sentence about the book and a useless first one.
_DANGLING_OPENING_RE = re.compile(
    r"\s*(?:he|she|they|it|his|her|their|its|him|them|there|then|however|"
    r"but|and|so|yet|meanwhile|later|afterwards?|eventually|"
    r"when they|after the)\b",
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
_PREMISE_RE = re.compile(
    r"\b(?:must|faces?|forced|discovers?|struggles?|tries?|seeks?|threatens?|"
    r"danger|surviv(?:e|al)|escapes?|protects?|saves?|fights?|battle|mystery|"
    r"secret|choice|mission|quest|investigates?|murder|war|vanishes?|"
    r"disappears?|risk|challenge|against|only hope|has to|cannot|can't)\b",
    re.IGNORECASE,
)

_NONFICTION_CATEGORY_RE = re.compile(
    # "biograph" carried a trailing \b, so it could never match "Biography" or
    # "Autobiography" -- the word continues and the boundary fails. The term had
    # been dead since it was written; lists like "Biography; History" were only
    # ever caught by "history".
    r"\b(?:\w*biograph\w*|memoir|history|science|psychology|business|self[- ]help|"
    r"philosophy|politic|economic|education|health|travel|religion|true crime|"
    r"social science|nature|environment|technology|mathematics|medical)\b",
    re.IGNORECASE,
)
_FICTION_CATEGORY_RE = re.compile(
    r"\b(?:fiction|novel|young adult|juvenile|children|fantasy|romance|mystery|"
    r"thriller|science fiction|literary)\b",
    re.IGNORECASE,
)
# The only genre label that settles the question on its own. Everything else is
# a hint, and hints collide: "Science Fiction" carries "science", "Alternate
# history" carries "history", "Time travel" carries "travel".
_EXPLICIT_NONFICTION_RE = re.compile(r"\bnon[- ]?fiction\b", re.IGNORECASE)
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

# ---------------------------------------------------------------------------
# FOUR MORE KINDS OF SENTENCE THAT ARE NOT A DESCRIPTION.
#
# These were written by reading all 147 cards the provider path produces, one
# at a time. Each pattern names the card that put it here, so a later reader
# can judge it against the evidence rather than against taste. Nothing was
# added on the strength of a guess about what providers might send.
# ---------------------------------------------------------------------------

# The encyclopaedia opener, in the shapes _BIBLIOGRAPHIC_RE does not reach:
# apostrophes ("is a British children's picture book" -- The Gruffalo),
# slashes ("is a 1994 horror/fantasy novel" -- Insomnia), plurals ("the third
# of the four crime novels" -- The Hound of the Baskervilles), "written by"
# (The Name of the Wind), and a parenthesised year (Heart of Darkness).
_OPENER_RE = re.compile(
    r"(?:\(\d{4}\)\s+is\b"
    r"|\bis (?:a|an|the)\s+(?:\d{4}\s+)?(?:(?:[\w’'/-]+\s+){0,5})?"
    r"(?:novel|novella|book|play|poem|memoir|biography|autobiography|story|"
    r"collection|tragedy|comedy|epic|anthology|picture book|fairy tale)\b"
    r"|\b(?:novel|novella|book|play|story|series)s? (?:written )?by (?:the )?"
    r"(?:[\w’'-]+\s+){0,2}(?:writer|author|novelist|poet|playwright|dramatist)\b"
    r"|\bis the (?:traditional name|debut|first|second|third|fourth|fifth|"
    r"\d+(?:st|nd|rd|th))\b)",
    re.IGNORECASE,
)

# How the book was RECEIVED. True, and never an answer to "what is it about?".
#   "Critic Michael Straight has hailed it as ..."          The Two Towers
#   "won the Bram Stoker Award for Best Novel in 1996"      The Green Mile
#   "Widely acclaimed for his work completing ..."          The Way of Kings
#   "transports us to a world unlike any we have ever"      A Clash of Kings
#   "For over a century both children and adults have been enchanted"  Peter Pan
_RECEPTION_RE = re.compile(
    r"\b(?:hailed (?:it |this |him |her )?as|has been (?:called|hailed|praised)|"
    r"described (?:it |this |them )?as|regarded as|"
    r"(?:widely |traditionally )?(?:seen|considered) as|best[- ]?selling author|"
    r"i(?:'ve| have) been recommending|one of (?:my|our) favou?rite|"
    r"won (?:the )?[\w' ]{0,30}(?:award|prize|medal|acclaim)|was awarded the|"
    r"nominated (?:as|for)|shortlisted for|winner of|"
    r"one of the (?:\w+ ){0,3}(?:most|greatest|finest|best|great|important)\b|"
    r"is one of the very few|critical acclaim|instant (?:success|bestseller)|"
    r"beloved by|enthralled readers|continues to thrill|have been enchanted|"
    r"widely acclaimed|acclaimed for|sit spellbound|"
    r"transports? (?:us|readers)|invites readers|delivers the long-awaited|"
    r"unlike any (?:we|you) have ever|cemented [\w’']+ stature|"
    r"named .{0,30}in its list of|selected by [A-Z]|\bcritics?\b)",
    re.IGNORECASE,
)

# Structure and narrative technique: true of the artefact, useless to someone
# deciding whether to read it.
#   "The section begins with Mrs Ramsay assuring her son James"  To the Lighthouse
#   "Franklin's account of his life is divided into four parts"  Franklin
#   "is told from an animal point of view"                       White Fang
_STRUCTURE_RE = re.compile(
    r"\b(?:the (?:section|chapter|part|work|book|novel|story|play|narrative) "
    r"(?:begins|opens|starts|is (?:split|divided))\b|"
    r"(?:is|are) (?:split|divided) into|there are actual breaks|"
    r"(?:is|was) (?:partially |partly )?(?:told|narrated|written) (?:in|from)\b|"
    r"point of view\b|as with (?:his|her|their) previous|"
    r"in a new introduction|it includes connections to|"
    r"according to \w+, it was|in the opening paragraph|"
    r"prior to (?:starting|writing) the (?:novel|book)|"
    r"the (?:style|prose|typography|illustrations?) (?:is|are|was)\b)",
    re.IGNORECASE,
)

# The record describes an EDITION, or the publisher, or the author -- not the
# book. Public-domain reprints and study editions arrive filed under the
# original title with the packaging described instead of the story.
#   "We are delighted to publish this classic book ..."      Walden
#   "a parallel translation ... Recommended for students"    The Call of the Wild
#   "the original text side by side with a modern version"   Julius Caesar
#   "Arthur Miller (1915-2005), American dramatist, was born" The Crucible
_EDITION_RE = re.compile(
    r"\b(?:we are (?:delighted|pleased|proud) to (?:publish|present|offer)|"
    r"as part of our (?:extensive|classic|special)|"
    r"(?:many of )?the books in our collection|has been the leading publisher|"
    r"penguin (?:modern )?classics|out of print for decades|"
    r"reproduced from the original|scanned from the original|classic library|"
    r"our publishing program|our philosophy has been guided|"
    r"hand curated by our staff|deserves to be brought back|"
    r"parallel translation|recommended for students|"
    r"side by side with a modern|study activities|in comic book format|"
    r"using this (?:text|edition)|book jacket|"
    r"(?:originally |first )?seriali[sz]ed in|"
    r"is one of (?:several|the) [\w ]{0,20}(?:plays|novels|books) "
    r"(?:that )?(?:he|she|they) wrote|written (?:several|a few) years after|"
    r"\(\d{4}\s*[-–]\s*\d{4}\)|"
    r"(?:was |been )adapted (?:as|into) an? [\w -]*"
    r"(?:film|movie|series|play|musical)|a major \w+ movie event)\b",
    re.IGNORECASE,
)

# Markup a provider left behind, and the debris of a quoted review.
#   "[Comment by Kim Stanley Robinson, on The Guardian's website][1]: > ..."
#   "Cast: 2 to 3m, 6 to 10w."                              The Bluest Eye
_DEBRIS_RE = re.compile(
    r"\[[^\]]{3,}\]\[\d+\]|\]\(https?://|^\s*>|^\s*Cast:|"
    r"^\s*(?:and|but|or)\s[\w ]{0,20}[\"”]\s*$")

# A LEAD that cannot stand alone. Not a junk rule -- a consequence of them.
# Dropping a sentence orphans the one after it: with its opener removed, To the
# Lighthouse began "This prediction is denied by Mr Ramsay" and the reader had
# no way to know what prediction. Only the FIRST kept sentence is tested; by
# the second, the referent exists. _DANGLING_OPENING_RE covers the pronoun
# cases already and is used alongside this.
_ORPHAN_LEAD_RE = re.compile(
    r"^\s*(?:this (?:prediction|discovery|event|decision)|"
    r"these are the hallmarks|as our story opens|"
    r"it (?:portrays|follows|tells|is at once))\b",
    re.IGNORECASE,
)



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
    if (_OPENER_RE.search(value) or _RECEPTION_RE.search(value)
            or _STRUCTURE_RE.search(value) or _EDITION_RE.search(value)
            or _DEBRIS_RE.search(value)):
        return True
    # "Perfect for fans of ...", "reading group guide", "ages 8-12". Shelving
    # advice, not description. This was a scoring signal that merely subtracted
    # points; with the scorer gone it has to reject or it does nothing.
    if _CATEGORY_RE.search(value):
        return True
    # No story-word escape for awards. It used to have one, because under the
    # old design dropping too many sentences left nothing to choose between and
    # the card went blank -- so the cleaner was made timid on purpose. Nothing
    # is chosen any more, the surviving sentences are simply shown, so dropping
    # one is cheap and an award is never a fact about what happens in the book.
    # Measured: it was letting through "The Green Mile won the Bram Stoker
    # Award" and "Her New York Times bestselling Outlander novels have earned
    # the praise of critics" as whole cards.
    if _AWARD_RE.search(value):
        return True
    # Spoilers used to be blocked by the accept gate that has gone. Without
    # this line "the killer is ..." would print on the card of a detective
    # novel, which is the one failure a reader could never forgive.
    if _SPOILER_RE.search(value):
        return True
    if _BIBLIOGRAPHIC_RE.search(value) and not _has_story_or_thesis(value):
        return True
    if _PUBLICATION_ONLY_RE.search(value) and not _has_story_or_thesis(value):
        return True
    # PILED-UP PROMOTIONAL ADJECTIVES. _MARKETING_RE has been in this file
    # since v1 and was never consulted -- it fed a score that no longer exists,
    # so "an unforgettable novel that mixes fiction and photography in a
    # thrilling reading experience" walked onto Miss Peregrine's card.
    #
    # TWO of them, not one. Every word in that list has an honest use in a real
    # description -- "a powerful storm", "her beloved grandmother" -- and
    # rejecting on one hit would take out ordinary sentences. Two in the same
    # sentence is a blurb writer, not a plot.
    if len(_MARKETING_RE.findall(value)) >= 2:
        return True
    # A QUOTED REVIEW, at any length. The 30-word ceiling let A Wizard of
    # Earthsea open with 40 words of Neil Gaiman before the story started.
    if value[:1] in {'"', "'", "“", "‘"}:
        return True
    # A FRAGMENT CUT OUT OF A QUOTATION. Kindred's card began 'My left arm."'
    # -- the tail of a sentence whose opening was in the part the cleaner
    # removed. A closing quote inside the first few words with no opening one
    # before it is that shape.
    head = value[:60]
    if any(mark in head for mark in ('"', "”")) and not any(
            mark in head for mark in ("“", "‘")) and value[:1] not in {'"', "“"}:
        return True
    return False


def clean_description(raw_text: str) -> dict:
    """Return cleaned, complete provider sentences and transparent removals."""
    plain = clean_source_text(raw_text or "")
    # Open Library stores descriptions as Markdown, and it was reaching the
    # card: Murder on the Orient Express opened "***While en route from Syria to
    # Paris***" and Catch-22 read "*Catch-22* is the story of a bombardier".
    # The tail after a "---" rule is a table of contents, not a description --
    # Goblet of Fire ended "--- Contains: - [Harry Potter and the Goblet of..."
    plain = re.split(r"\s-{3,}\s", plain)[0]
    plain = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", plain)
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", plain)
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
    # ORDER IS THE WHOLE FUNCTION, and it used to be wrong. Asking the
    # nonfiction words first let the "science" inside "Science Fiction" file a
    # quarter of the catalogue -- 62 of 250 books, Kindred and Stardust and
    # Cat's Cradle and The Time Traveler's Wife among them -- as nonfiction, so
    # their descriptions were scored by rules written for biographies. The same
    # collision hides in "Alternate history", "Time travel" and "Science
    # fantasy", which is why blanking out compound genres was not enough either.
    #
    # A genre list that says "Non-fiction" means it. Otherwise a fiction label
    # anywhere in the list settles it, because a novel is routinely tagged with
    # its subject matter while a biography is not tagged "Novel". Only a list
    # with no fiction label at all is read for nonfiction words.
    #
    # Measured on the 250-book catalogue: 7 nonfiction, and all 7 are right --
    # Cosmos, Guns Germs and Steel, The Wealth of Nations, Into the Wild, The
    # 33 Strategies of War, The 4-Hour Workweek, The Art of Seduction. The one
    # book it now gets wrong is Walden, which the source dataset itself tags
    # "Speculative fiction; Fiction". That is a bad row, not a bad rule.
    value = categories or ""
    if _EXPLICIT_NONFICTION_RE.search(value):
        return "nonfiction"
    if _FICTION_CATEGORY_RE.search(value):
        return "fiction"
    if _NONFICTION_CATEGORY_RE.search(value):
        return "nonfiction"
    return "fiction"


def _is_wrong_document(sentence: str) -> bool:
    """Does this sentence say the record is not a description at all?

    A description can contain an encyclopaedia opener or a chapter note and
    still be a description -- that is ordinary noise. But review quotes,
    critical reception, sales copy and a publisher's reprint notice are what a
    DIFFERENT KIND OF PAGE is made of, and enough of them means the provider
    returned a review or a catalogue entry rather than a blurb.
    """
    return bool(_REVIEW_RE.search(sentence) or _RECEPTION_RE.search(sentence)
                or _PROMO_RE.search(sentence) or _CTA_RE.search(sentence)
                or _EDITION_RE.search(sentence) or _AWARD_RE.search(sentence)
                or _DEBRIS_RE.search(sentence))


def read_one_source(raw_text: str) -> dict:
    """Clean one provider record down to the part that describes the book.

    Returns {"text", "refused", "language", "kept", "dropped"}. `refused` names
    why there is no text, and is empty when there is.
    """
    cleaned = clean_description(raw_text or "")
    language = cleaned["language"]
    # A POSITIVE identification of English, not merely the absence of another
    # language. detect_language scores nine languages by stop-word lists; text
    # in a tenth scores zero everywhere and comes back "unknown". Accepting
    # unknown put a Polish description of Paper Towns on an English card.
    if language.get("code") != "en":
        return {"text": "", "refused": "not_english", "language": language,
                "kept": [], "dropped": cleaned["removed_sentences"]}

    # clean_description has already applied sentence_is_junk; cleaned
    # ["sentences"] are the survivors and cleaned["removed_sentences"] is what
    # it threw away. Filtering again here would be a no-op -- an earlier draft
    # did exactly that and left the wrong-document count permanently at zero,
    # which silently disabled the rule below.
    kept = list(cleaned["sentences"])
    dropped = list(cleaned["removed_sentences"])
    wrong_document = sum(1 for s in dropped if _is_wrong_document(s))

    # Advance past a lead that cannot stand on its own -- see _ORPHAN_LEAD_RE.
    while kept and (_ORPHAN_LEAD_RE.match(kept[0])
                    or _DANGLING_OPENING_RE.match(kept[0])):
        dropped.append(kept.pop(0))

    if not kept:
        return {"text": "", "refused": "nothing_describes_the_book",
                "language": language, "kept": [], "dropped": dropped}

    # THE RECORD IS THE WRONG KIND OF DOCUMENT.
    #
    # Removing junk sentence by sentence stops working when the whole record is
    # a newspaper review (The Left Hand of Darkness arrives as a Guardian
    # column), a reprint publisher's page (Walden), a critics' round-up (The
    # Two Towers) or author marketing (The 4-Hour Workweek). Writing a pattern
    # for each is the unbounded-vocabulary trap the old whitelist fell into,
    # approached from the other side. clean_description already carries the
    # idea for advertisements (_PROMO_SOURCE_LIMIT).
    #
    # ONLY "wrong document" DROPS COUNT. The first version of this counted
    # every dropped sentence and was measurably wrong: Open Library records
    # derived from Wikipedia carry several bibliographic sentences AND a real
    # one, so it threw away "A young girl named Alice falls through a rabbit
    # hole into a fantasy world of anthropomorphic creatures" because three
    # encyclopaedia openers sat beside it. An opener is ordinary noise inside a
    # real description; a page of review quotes is a different document.
    if wrong_document and wrong_document >= max(2, len(kept)):
        return {"text": "", "refused": "mostly_not_a_description",
                "language": language, "kept": kept, "dropped": dropped}

    chosen, running = [], 0
    for sentence in kept:
        length = word_count(sentence)
        if chosen and running + length > MAX_WORDS:
            break
        chosen.append(sentence)
        running += length

    text = " ".join(chosen)
    if word_count(text) < MIN_WORDS:
        return {"text": "", "refused": "too_short", "language": language,
                "kept": kept, "dropped": dropped}
    return {"text": text, "refused": "", "language": language,
            "kept": kept, "dropped": dropped}


def select_what_its_about(sources: list[dict], *, title: str = "",
                          categories: str = "", kind: str = "") -> dict:
    """The publisher's own words, minus the parts that are not about the book.

    NOTHING IS CHOSEN SENTENCE BY SENTENCE. An earlier version scored every
    one- and two-sentence window and published the winner, gated behind a
    keyword whitelist -- a candidate had to contain a word from a fixed list
    ("must", "faces", "discovers", "danger"...) or it was refused outright.
    Measured on 190 books outside the catalogue, that whitelist blanked 51% of
    cards, and 91 of the 97 blanks had a perfectly readable publisher
    description sitting on the same screen. The Tale of Despereaux was refused
    because "determined to bring them all to ruin" is not on the list.

    A whitelist over an open vocabulary cannot be completed -- the same lesson
    the provider/shelf synonym map already bought. So the question was turned
    around. The rules now say what is NOT a description -- markup, marketing,
    critical reception, an encyclopaedia opener, a publisher's reprint notice,
    chapter structure -- and everything else is the answer, in the order the
    publisher wrote it.

    What selection remains is between SOURCES, not sentences: the first record
    that reads like a description wins. That is a far smaller claim to defend,
    and it is what makes a bad card impossible to manufacture -- the worst this
    can do is show a publisher's real sentence, never a machine's pick of the
    least-bad one. Measured: 49% of cards filled -> 77%, on 190 books.
    """
    source_audit = []
    for source in sources:
        result = read_one_source(source.get("text", ""))
        source_audit.append({
            "source": source.get("source"),
            "verification": source.get("verification"),
            "language": result["language"],
            "kept_sentences": len(result["kept"]),
            "removed_sentences": len(result["dropped"]),
            "refused": result["refused"],
        })
        if not result["text"]:
            continue
        return {
            "status": "ready",
            "overview": result["text"],
            "method": METHOD,
            "source": source.get("source"),
            "verification": source.get("verification"),
            "source_text": " ".join(result["kept"]),
            "word_count": word_count(result["text"]),
            "sentence_count": len(split_sentences(result["text"])),
            "source_audit": source_audit,
        }

    if not sources:
        reason = "no_exact_provider_description"
    elif all(audit["refused"] == "not_english" for audit in source_audit):
        reason = "no_verified_english_description"
    else:
        reason = next((a["refused"] for a in source_audit
                       if a["refused"] and a["refused"] != "not_english"),
                      "no_usable_provider_description")
    return {
        "status": "unavailable",
        "overview": "",
        "method": METHOD,
        "reason": reason,
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
