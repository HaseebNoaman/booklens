from pathlib import Path

import api

from whatitsabout_heuristic import (
    MAX_WORDS,
    METHOD,
    MIN_WORDS,
    clean_description,
    collect_exact_provider_sources,
    generate_candidate_windows,
    score_candidate,
    select_provider_lead,
    select_what_its_about,
    split_sentences,
    word_count,
)


def source(text, name="google_volume"):
    return {"text": text, "source": name,
            "verification": "synthetic_exact_id_test"}


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


def test_windows_are_only_single_sentences_and_adjacent_pairs():
    sentences = ["One starts here.", "Two follows it.", "Three ends here."]
    windows = list(generate_candidate_windows(sentences))
    assert windows == [
        (0, [sentences[0]]),
        (0, [sentences[0], sentences[1]]),
        (1, [sentences[1]]),
        (1, [sentences[1], sentences[2]]),
        (2, [sentences[2]]),
    ]
    assert all([sentences[0], sentences[2]] != window for _, window in windows)


def test_fiction_ranking_prefers_character_and_central_premise_over_lead():
    description = (
        "This lyrical novel presents a quiet portrait of a changing coastal town. "
        "Mara returns home after ten years away and tries to rebuild her life. "
        "When her brother disappears, she must uncover the town's secret before "
        "another family member is taken."
    )
    sources = [source(description)]
    lead = select_provider_lead(sources)
    result = select_what_its_about(sources, title="The Quiet Coast", kind="fiction")
    assert lead["status"] == "ready"
    assert result["status"] == "ready"
    assert "brother disappears" in result["overview"]
    assert result["signals"]["character"] is True
    assert result["signals"]["premise"] is True
    assert MIN_WORDS <= word_count(result["overview"]) <= MAX_WORDS


def test_nonfiction_requires_subject_and_thesis_signal():
    description = (
        "Drawing on decades of research, this book examines how chronic stress "
        "changes the human body. It explains why social conditions shape health "
        "and what the evidence means for prevention."
    )
    result = select_what_its_about(
        [source(description)], title="Stress and Society", kind="nonfiction"
    )
    assert result["status"] == "ready"
    assert result["signals"]["subject"] is True
    assert result["signals"]["thesis"] is True


def test_good_55_word_candidate_is_not_rejected_for_exceeding_45_words():
    text = (
        "Mara returns to her isolated island after a decade away and hopes to "
        "repair her relationship with her family. When a violent storm cuts off "
        "the community and her younger brother disappears, she must cross the "
        "flooded coast, confront an old secret, and find him before the tide "
        "destroys the only road home."
    )
    sentences = split_sentences(text)
    scored = score_candidate(text, sentences, title="Island Road",
                             kind="fiction", sentence_index=0)
    assert 51 <= scored["word_count"] <= 60
    assert scored["accepted"] is True


def test_explicit_resolution_or_culprit_is_rejected():
    text = (
        "Detective Mara investigates a locked-room murder and must question every "
        "guest before the evidence vanishes. In the end she reveals that the "
        "killer is her trusted partner and saves the final witness."
    )
    sentences = split_sentences(text)
    scored = score_candidate(text, sentences, title="Locked Room",
                             kind="fiction", sentence_index=0)
    assert scored["signals"]["spoiler_resolution"] is True
    assert scored["accepted"] is False


def test_non_english_source_is_declined_without_translation():
    text = (
        "Ketika Mara kembali ke rumah, dia menemukan bahwa adiknya telah hilang. "
        "Dia harus mencari rahasia lama sebelum keluarganya berada dalam bahaya."
    )
    result = select_what_its_about([source(text)], title="Rumah Lama",
                                  kind="fiction")
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
    # v2 rejects promotional verbs and refuses a predominantly
    # promotional source. Bumped deliberately so cached v1 overviews,
    # which may contain that marketing, regenerate.
    assert METHOD == "candidate_window_heuristic_v2"
