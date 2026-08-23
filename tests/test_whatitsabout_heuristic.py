"""The card shows the publisher's description, minus what is not about the book.

These tests changed shape on 2026-08-23 with the module. They used to assert
that a scorer ranked one- and two-sentence windows and that a keyword whitelist
vetoed the rest. Nothing is ranked any more, so what is worth locking in is the
opposite: which kinds of sentence must never reach a card, and that ordinary
descriptions are no longer refused for lacking a magic word.

Every rejection test below names the real book whose card carried that text
before the rule was written. They were found by reading all 147 cards the
provider path produces, one at a time -- the only check that catches a bad
card -- which is why they are recorded here rather than in a comment.
"""
from pathlib import Path

import api

from whatitsabout_heuristic import (
    MAX_WORDS,
    METHOD,
    MIN_WORDS,
    clean_description,
    collect_exact_provider_sources,
    read_one_source,
    select_what_its_about,
    sentence_is_junk,
    split_sentences,
    word_count,
)


def source(text, name="google_volume"):
    return {"text": text, "source": name,
            "verification": "synthetic_exact_id_test"}


def overview(text, **kwargs):
    return select_what_its_about([source(text)], **kwargs).get("overview", "")


def test_cleaner_removes_html_cta_awards_reviews_and_headings():
    raw = (
        "<h2>BOOK JACKET</h2><p>Winner of a major award.</p>"
        "<p>Mara returns to her coastal home after ten years away. When her "
        "brother disappears, she must uncover the town's secret before another "
        "family member is taken.</p><p>Download now and start reading.</p>"
        "<p>\"A triumph\" - Example Review</p>"
    )
    cleaned = clean_description(raw)
    assert cleaned["language"]["code"] == "en"
    assert len(cleaned["sentences"]) == 2
    assert "BOOK JACKET" not in cleaned["text"]
    assert "Download" not in cleaned["text"]
    assert "Winner" not in cleaned["text"]


# ----- what must NOT be refused any more -----

def test_a_description_without_a_premise_keyword_is_no_longer_refused():
    # THE REGRESSION THIS MODULE WAS REWRITTEN FOR. The old gate demanded a
    # word from a fixed list, and The Tale of Despereaux's real Google
    # description contains none of them, so the card was blank while this text
    # sat on the same screen. Measured across 190 books outside the catalogue,
    # that whitelist blanked 51% of cards, and 91 of the 97 blanks had readable
    # text like this one.
    text = ("The adventures of Desperaux Tilling, a small mouse of unusual "
            "talents, the princess that he loves, the servant girl who longs "
            "to be a princess, and a devious rat determined to bring them all "
            "to ruin.")
    assert "Desperaux Tilling" in overview(text, title="The Tale of Despereaux")


def test_a_short_complete_description_is_kept():
    # 18 words, and the whole of what Open Library holds for this book. The old
    # 25-word floor existed because a fragment that short meant the picker had
    # found nothing worth showing; a complete short description is a complete
    # answer.
    text = ("The story of a Cro-Magnon woman orphaned as a child and raised by "
            "a group of Neanderthals.")
    result = select_what_its_about([source(text)], title="The Clan of the Cave Bear")
    assert result["status"] == "ready"
    assert MIN_WORDS <= word_count(result["overview"])


def test_several_sentences_are_kept_in_the_publishers_order():
    text = ("Two girls who grow up to become women. Two friends who become "
            "something worse than enemies. Toni Morrison tells the story of Nel "
            "Wright and Sula Peace, who meet as children in the small town of "
            "Medallion, Ohio.")
    result = overview(text, title="Sula")
    assert result.index("Two girls") < result.index("Two friends")
    assert word_count(result) <= MAX_WORDS


# ----- what must never reach a card -----

def test_the_encyclopaedia_opener_is_rejected():
    # True sentences, and not an answer to what the book is about. The card
    # carried these for American Gods, The Gruffalo and The Name of the Wind.
    assert sentence_is_junk(
        "American Gods (2001) is a fantasy novel by British author Neil Gaiman.")
    assert sentence_is_junk(
        "The Gruffalo is a British children's picture book by writer and "
        "playwright Julia Donaldson.")
    assert sentence_is_junk(
        "The Name of the Wind is a heroic fantasy novel written by American "
        "author Patrick Rothfuss.")


def test_critical_reception_and_awards_are_rejected():
    # The Green Mile's whole card was its award record; The Two Towers' was a
    # critic's verdict. An award rule already existed but stepped aside for any
    # sentence containing a story word, which both of these have.
    assert sentence_is_junk(
        "The Green Mile won the Bram Stoker Award for Best Novel in 1996.")
    assert sentence_is_junk(
        "Critic Michael Straight has hailed it as one of the very few works of "
        "genius in recent literature.")
    assert sentence_is_junk(
        "Widely acclaimed for his work completing Robert Jordan's Wheel of "
        "Time saga, Brandon Sanderson now begins a grand cycle of his own.")


def test_publisher_and_edition_boilerplate_is_rejected():
    # Public-domain reprints describe the packaging. Walden's card was the
    # Classic Library's mission statement; The Call of the Wild's was a notice
    # that the edition is a Ukrainian parallel translation.
    assert sentence_is_junk(
        "We are delighted to publish this classic book as part of our "
        "extensive Classic Library collection.")
    assert sentence_is_junk(
        "This book contains a parallel translation from English into Ukrainian.")
    assert sentence_is_junk(
        "Presents the original text of Shakespeare's play side by side with a "
        "modern version, and provides quizzes and other study activities.")


def test_chapter_structure_is_rejected():
    assert sentence_is_junk(
        "The section begins with Mrs Ramsay assuring her son James that they "
        "should be able to visit the lighthouse.")
    assert sentence_is_junk(
        "Franklin's account of his life is divided into four parts, reflecting "
        "the different periods at which he wrote them.")


def test_a_spoiler_is_still_rejected():
    # This used to be enforced by the accept gate that has been removed. It is
    # the one failure a reader could not forgive, so it moved into the junk
    # rules rather than disappearing with the scorer.
    assert sentence_is_junk(
        "In the end she reveals that the killer is her trusted partner and "
        "saves the final witness.")


def test_a_lead_that_cannot_stand_alone_is_dropped():
    # Removing a junk sentence orphans the one after it. With its opener gone,
    # To the Lighthouse began "This prediction is denied by Mr Ramsay" and a
    # reader had no way to know what prediction.
    text = ("The section begins with Mrs Ramsay assuring her son James that "
            "they can visit the lighthouse tomorrow. This prediction is denied "
            "by Mr Ramsay, who voices his certainty that the weather will not "
            "be clear. Their summer home stands in the Hebrides, on the Isle "
            "of Skye, and the family returns to it over a decade of summers.")
    result = overview(text, title="To the Lighthouse")
    assert not result.startswith("This prediction")


# ----- refusing a whole source -----

def test_a_source_that_is_mostly_not_a_description_yields_nothing():
    # The Left Hand of Darkness arrives as a Guardian column, Walden as a
    # reprint publisher's page. Writing a pattern per case is the unbounded
    # vocabulary trap again; when the cleaner throws away as much as it keeps,
    # the record was not a description.
    pitch = ("Winner of the Hugo Award for Best Novel. Praise for this "
             "extraordinary book. Critics say it is a triumph. One of the "
             "greatest novels of the century. Mara walks home along the "
             "flooded coast road at dusk.")
    result = select_what_its_about([source(pitch)], title="Anything")
    assert result["status"] == "unavailable"
    assert result["reason"] == "mostly_not_a_description"


def test_the_next_source_is_tried_when_the_first_is_not_a_description():
    # Measured: 35 of 190 books were saved this way. The remaining selection is
    # between SOURCES, never between sentences.
    review = ("Praise for this extraordinary book. Critics say it is a "
              "triumph. Winner of the Booker Prize. One of the finest novels "
              "of the decade. He walks home alone.")
    real = ("Mara returns to her island after ten years away. When her brother "
            "disappears, she searches the flooded coast before the tide "
            "destroys the only road home.")
    result = select_what_its_about(
        [source(review, "google_volume"), source(real, "openlibrary_work")],
        title="Island Road")
    assert result["status"] == "ready"
    assert result["source"] == "openlibrary_work"
    assert "Mara returns" in result["overview"]


def test_a_language_we_cannot_identify_is_not_treated_as_english():
    # detect_language scores nine languages by stop-word lists. Text in a tenth
    # scores zero everywhere and comes back "unknown", and accepting unknown put
    # a Polish description of Paper Towns on an English card.
    polish = ("Quentin Jacobsen od zawsze jest zakochany we wspanialej "
              "kolezance, zbuntowanej Margo Roth Spiegelman. W dziecinstwie "
              "przezyli razem cos niesamowitego, teraz chodza do tego samego "
              "liceum i prawie ze soba nie rozmawiaja ze soba wcale.")
    assert read_one_source(polish)["refused"] == "not_english"


def test_non_english_source_is_declined_without_translation():
    text = (
        "Ketika Mara kembali ke rumah, dia menemukan bahwa adiknya telah hilang. "
        "Dia harus mencari rahasia lama sebelum keluarganya berada dalam bahaya."
    )
    result = select_what_its_about([source(text)], title="Rumah Lama")
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_verified_english_description"


def test_exact_collector_considers_google_and_open_library(monkeypatch):
    google = (
        "Mara returns to her island after ten years away. When her brother "
        "disappears, she must confront an old secret before another storm arrives."
    )
    open_library = (
        "Mara comes home hoping to repair her family. Her brother then vanishes, "
        "forcing her to search the island while a dangerous storm closes in."
    )
    monkeypatch.setattr(api, "get_volume_by_id", lambda value: google)
    monkeypatch.setattr(api, "get_open_library_edition", lambda value: {
        "description": "", "works": [{"key": "/works/OL_TEST_W"}]
    })
    monkeypatch.setattr(api, "get_open_library_work_description",
                        lambda value: open_library)
    monkeypatch.setattr(api, "search_by_isbn",
                        lambda value: {"error": "should not be called"})
    collected = collect_exact_provider_sources({
        "google_books_id": "google-test", "isbn_13": "9780306406157"
    })
    assert [item["source"] for item in collected["sources"]] == [
        "google_volume", "openlibrary_work"
    ]
    assert all("title" not in attempt["verification"]
               for attempt in collected["attempts"])


def test_no_model_dependency_or_known_title_special_cases():
    module_text = (Path(__file__).resolve().parents[1] /
                   "whatitsabout_heuristic.py").read_text(encoding="utf-8").lower()
    for forbidden in ("transformers", "sentence_transformers", "torch", "flan",
                      "bge", "crossencoder", "embeddinggemma"):
        assert forbidden not in module_text
    for diagnostic_title in ("hunger games", "secret garden", "dreamland",
                             "honorary consul", "captain underpants"):
        assert diagnostic_title not in module_text
    # v3 stopped extracting windows altogether. Bumped deliberately so cached
    # v1 and v2 overviews -- single sentences chosen by the retired scorer --
    # regenerate as cleaned descriptions.
    assert METHOD == "cleaned_provider_description_v3"
