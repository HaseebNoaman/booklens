"""A match recovered from raw cover text must never be the final word.

The defect these lock down was real and reproducible. City of Orange carries a
praise quote reading "...Cormac McCarthy's The Road" on its cover. The OCR
recovery path matches catalogue rows against ALL the cover text, so it scored
The Road at 79.9 -- and because that is not a rejection, the funnel returned it
and never asked Google. The reader saw one wrong book and no way to reach the
right one. On the 100-cover benchmark every wrong short-circuit came from this
path; the direct path produced none.
"""
import app as app_module
from matching import HIGH_CONFIDENCE, NEEDS_CONFIRMATION, REJECTED


def candidate(title, author="", score=70.0, **extra):
    row = {"title": title, "author": author, "score": score,
           "decision": NEEDS_CONFIRMATION, "reasons": []}
    row.update(extra)
    return row


def recovery_result(*candidates):
    return {"decision": NEEDS_CONFIRMATION, "candidates": list(candidates),
            "tier": app_module.RECOVERY_TIER, "rejected_count": 0}


# ----- the merge itself -----

def test_provider_results_and_recovered_ones_are_shown_together():
    recovery = recovery_result(candidate("The Road", "Cormac McCarthy", 79.9))
    external = {"decision": NEEDS_CONFIRMATION, "rejected_count": 0,
                "candidates": [candidate("City of Orange", "David Yoon", 93.8)]}

    merged = app_module.merge_recovery_with_external(recovery, external)

    titles = [c["title"] for c in merged["candidates"]]
    assert "City of Orange" in titles
    assert "The Road" in titles
    # Better evidence first, but the reader still gets to choose.
    assert titles[0] == "City of Orange"
    assert merged["decision"] == NEEDS_CONFIRMATION


def test_nothing_in_a_merged_list_can_auto_accept():
    recovery = recovery_result(candidate("The Road", "Cormac McCarthy", 79.9))
    external = {"decision": NEEDS_CONFIRMATION, "rejected_count": 0,
                "candidates": [candidate("City of Orange", "David Yoon", 95.0,
                                         decision=HIGH_CONFIDENCE)]}

    merged = app_module.merge_recovery_with_external(recovery, external)

    # Half of this list was assembled from jumbled text. Only the person
    # holding the book can settle it.
    assert all(c["decision"] == NEEDS_CONFIRMATION for c in merged["candidates"])


def test_an_exact_provider_match_outranks_a_recovered_guess():
    recovery = recovery_result(candidate("The Road", "Cormac McCarthy", 79.9))
    external = {"decision": HIGH_CONFIDENCE, "rejected_count": 0,
                "candidates": [candidate("City of Orange", "David Yoon", 97.0,
                                         decision=HIGH_CONFIDENCE)]}

    merged = app_module.merge_recovery_with_external(recovery, external)

    assert merged["decision"] == HIGH_CONFIDENCE
    assert [c["title"] for c in merged["candidates"]] == ["City of Orange"]


def test_a_provider_outage_leaves_the_old_behaviour_untouched():
    recovery = recovery_result(candidate("The Road", "Cormac McCarthy", 79.9))
    external = {"decision": REJECTED, "candidates": [], "rejected_count": 0}

    merged = app_module.merge_recovery_with_external(recovery, external)

    # Losing the network must never make identification worse than it was
    # before providers were consulted at all.
    assert merged is recovery
    assert [c["title"] for c in merged["candidates"]] == ["The Road"]


def test_the_same_book_from_two_sources_is_shown_once():
    recovery = recovery_result(candidate("City of Orange", "David Yoon", 70.0,
                                         isbn_13="9780593422168"))
    external = {"decision": NEEDS_CONFIRMATION, "rejected_count": 0,
                "candidates": [candidate("City of Orange", "David Yoon", 93.8,
                                         isbn_13="9780593422168")]}

    merged = app_module.merge_recovery_with_external(recovery, external)

    assert len(merged["candidates"]) == 1
    assert merged["candidates"][0]["score"] == 93.8


# ----- the funnel wiring -----

def test_a_recovery_only_match_never_answers_without_asking_the_providers(monkeypatch):
    asked = []

    monkeypatch.setattr(app_module, "retrieve_local_candidates",
                        lambda *a, **k: recovery_result(
                            candidate("The Road", "Cormac McCarthy", 79.9)))

    def spy(*args, **kwargs):
        asked.append(args)
        return {"decision": NEEDS_CONFIRMATION, "rejected_count": 0,
                "candidates": [candidate("City of Orange", "David Yoon", 93.8)]}

    monkeypatch.setattr(app_module, "retrieve_ranked_candidates", spy)

    result = app_module.retrieve_tiered_candidates(
        "OF CITY ORANGE", "DAVID YOON", "", "...Cormac McCarthy's The Road...")

    assert asked, "providers were never consulted"
    assert "City of Orange" in [c["title"] for c in result["candidates"]]
    assert result["tier"] == "local_recovery_plus_external"


def test_a_direct_catalogue_match_still_answers_on_its_own(monkeypatch):
    asked = []
    direct = {"decision": HIGH_CONFIDENCE, "rejected_count": 0,
              "tier": "local_catalogue",
              "candidates": [candidate("Dracula", "Bram Stoker", 96.0,
                                       decision=HIGH_CONFIDENCE)]}
    monkeypatch.setattr(app_module, "retrieve_local_candidates",
                        lambda *a, **k: direct)
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates",
                        lambda *a, **k: asked.append(1))

    result = app_module.retrieve_tiered_candidates("DRACULA", "BRAM STOKER")

    # The whole speed argument for a local catalogue is that a confident hit
    # costs no network round-trip.
    assert asked == []
    assert result is direct


def test_a_rejected_catalogue_match_goes_straight_to_the_providers(monkeypatch):
    monkeypatch.setattr(app_module, "retrieve_local_candidates",
                        lambda *a, **k: {"decision": REJECTED, "candidates": [],
                                         "tier": "local_catalogue"})
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates",
                        lambda *a, **k: {"decision": NEEDS_CONFIRMATION,
                                         "rejected_count": 0,
                                         "candidates": [candidate("Normal People")]})

    result = app_module.retrieve_tiered_candidates("SALY OAL PEOPLE RYOONEY")

    assert result["tier"] == "external"


def test_the_real_city_of_orange_cover_text_reaches_the_providers(monkeypatch):
    """End to end against the real 250-book catalogue, no stubbed local side."""
    asked = []

    def spy(*args, **kwargs):
        asked.append(args)
        return {"decision": NEEDS_CONFIRMATION, "rejected_count": 0,
                "candidates": [candidate("City of Orange", "David Yoon", 93.8)]}

    monkeypatch.setattr(app_module, "retrieve_ranked_candidates", spy)

    # Exactly what the OCR read off the cover, blurb and all.
    cover_text = ("co cncpmy, alonpnde kndy se, pohn Mandels Sution Elrron, "
                  "Kcdand Madhsann I Aur Lgonl, and Corese MaCanly TH ROA."
                  "-SAN FRANCISCO CHRONICLE CITY OF ORANGE DAVID YOON")
    result = app_module.retrieve_tiered_candidates(
        "OF CITY ORANGE", "DAVID YOON", "", cover_text,
        text_lines=cover_text.split())

    titles = [c["title"] for c in result["candidates"]]
    assert asked, "the praise quote still blocked the providers"
    assert "City of Orange" in titles
