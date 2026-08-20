from result_content import (clean_source_text, detect_language,
                            prepare_external_source)


def test_provider_html_and_marketing_are_removed():
    value = (
        "<p><b>AN INSTANT #1 BESTSELLER</b></p>"
        "<p>Violet must leave the safety of home and seek new allies.</p>"
        "<p>'Unmissable' - A Reviewer</p>"
        "<p>#BookTok #Bestseller #ReadNow</p>"
    )
    cleaned = clean_source_text(value)
    assert "<p>" not in cleaned
    assert "BESTSELLER" not in cleaned
    assert "#BookTok" not in cleaned
    assert "Violet must leave" in cleaned


def test_inline_bestseller_accolade_is_removed_without_losing_description():
    value = (
        "A sharp novel from R.F. Kuang, the #1 New York Times bestselling "
        "author of Babel. June takes a manuscript and must protect the lie."
    )
    cleaned = clean_source_text(value)
    assert "bestselling" not in cleaned.lower()
    assert "R.F. Kuang. June takes" in cleaned


def test_language_detection_catches_live_provider_examples():
    assert detect_language(
        "June Hayward dan Athena Liu sama-sama penulis. Ketika Athena "
        "meninggal, June mencuri manuskrip dan menyerahkannya sebagai karyanya."
    )["code"] == "id"
    assert detect_language(
        "Een roman over liefde en familie. In de lente keren de drie dochters "
        "van Lara terug naar hun ouderlijk huis en horen ze haar verhaal."
    )["code"] == "nl"
    assert detect_language(
        "När solen går upp sprider sig oron i distrikten och fyra ungdomar "
        "ska skickas till arenan för att strida på liv och död."
    )["code"] == "sv"
    assert detect_language(
        "Demon Copperhead is born to a teenage mother and has little beyond "
        "his wit, his courage, and a fierce talent for survival."
    )["code"] == "en"


def test_foreign_source_is_not_sent_to_summarizer(monkeypatch):
    monkeypatch.setattr("result_content._exact_open_library_sources",
                        lambda book: iter(()))
    prepared = prepare_external_source(
        {"isbn_13": "9786020672809"},
        {"text": "June dan Athena sama-sama penulis. Buku ini adalah karya "
                 "yang terkenal tetapi tidak semua orang tahu rahasianya.",
         "source": "google_volume", "reason": ""},
    )
    assert prepared["model_text"] is None
    assert prepared["reason"] == "non_english_source"
    assert prepared["language"]["code"] == "id"


def test_exact_english_fallback_can_replace_foreign_source(monkeypatch):
    monkeypatch.setattr(
        "result_content._exact_open_library_sources",
        lambda book: iter([(
            "June is a struggling writer who takes her late friend's manuscript "
            "and publishes it as her own. The success forces her to protect a "
            "lie that becomes harder to contain.",
            "openlibrary_work",
        )]),
    )
    prepared = prepare_external_source(
        {"isbn_13": "9786020672809"},
        {"text": "June dan Athena sama-sama penulis. Buku ini adalah karya "
                 "yang terkenal tetapi tidak semua orang tahu rahasianya.",
         "source": "google_volume", "reason": ""},
    )
    assert prepared["model_text"].startswith("June is a struggling writer")
    assert prepared["source"] == "openlibrary_work"
    assert prepared["language"]["code"] == "en"
