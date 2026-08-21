import io

import pytest
from PIL import Image

import app as app_module
import database
from matching import REJECTED


def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (100, 140), "white").save(stream, format="PNG")
    stream.seek(0)
    return stream


@pytest.fixture()
def client(tmp_path):
    database.DB_NAME = str(tmp_path / "app.db")
    database.init_db()
    app_module.app.config.update(TESTING=True, MAX_CONTENT_LENGTH=1024 * 1024)
    return app_module.app.test_client()


def register_and_login(client, email="reader@example.com"):
    # Registration now answers 202 and leaves the account unverified; these
    # tests are about everything that happens AFTER sign-in, so the
    # confirmation step is granted directly here. The link itself is exercised
    # end to end in tests/test_email_verification.py.
    assert client.post("/api/register", json={"name": "Reader", "email": email,
                                               "password": "strongpass"}).status_code == 202
    database.mark_email_verified(database.get_user_by_email(email)["id"])
    response = client.post("/api/login", json={"email": email,
                                                "password": "strongpass"})
    return response.get_json()["token"]


def auth(token):
    return {"Authorization": "Bearer " + token}


def candidate():
    return {"title": "Dune", "author": "Frank Herbert", "publisher": "Ace",
            "published_date": "1965", "page_count": 412, "categories": "Fiction",
            "thumbnail": "", "description": "A publisher description.",
            "google_books_id": "dune-google", "isbn_13": "9780441172719",
            "provider": "google", "score": 78.0, "decision": "NEEDS_CONFIRMATION",
            "reasons": ["Title is an exact or near-exact match", "Author matches"],
            "score_breakdown": {"title_similarity": 100, "author_similarity": 100}}


def test_ocr_failure_returns_manual_recovery(client, monkeypatch):
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "process_book_cover", lambda *a, **k: {
        "probable_title": "", "probable_author": "", "full_text": "",
        "confidence_score": 0.0})
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (png_bytes(), "cover.png")},
                           content_type="multipart/form-data")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ocr_review"
    assert data["ocr"]["status"] == "OCR_FAILED"
    assert "type the book title" in data["message"].lower()


def test_low_confidence_ocr_returns_editable_title_and_author(client, monkeypatch):
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "process_book_cover", lambda *a, **k: {
        "probable_title": "Dune", "probable_author": "Frank Herbert",
        "full_text": "Dune Frank Herbert", "confidence_score": 0.4})
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (png_bytes(), "cover.png")},
                           content_type="multipart/form-data")
    data = response.get_json()
    assert data["status"] == "ocr_review"
    assert data["ocr"]["status"] == "OCR_LOW_CONFIDENCE"
    assert data["ocr"]["extracted_title"] == "Dune"
    assert data["ocr"]["extracted_author"] == "Frank Herbert"


def test_scan_recovers_scrambled_raw_ocr_from_catalogue(client, monkeypatch):
    token = register_and_login(client)
    database.create_catalogue_book({
        "title": "The Horse and His Boy", "author": "C. S. Lewis",
        "isbn_13": "9780064471060",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified summary long enough for the test.",
        "short_summary": "A stored overview.", "short_summary_status": "ok",
        "verification_status": "VERIFIED"})
    monkeypatch.setattr(app_module, "process_book_cover", lambda *a, **k: {
        "probable_title": "C.S.LEWIS ANDHIS BOY THE HORSE",
        "probable_author": "C-0ONOT",
        "full_text": "C.S. LEWIS THE HORSE ANDHIS BOY C-0ONOT",
        "text_lines": ["C.S.LEWIS", "ANDHIS BOY", "THE HORSE", "C-0ONOT"],
        "confidence_score": 0.91})
    # This test used to assert the network was NEVER called here. That
    # assertion described a design that has since been measured and changed:
    # a recovery match is made against ALL the cover text, blurb included, so
    # a praise quote naming another book could win and end the search (City of
    # Orange recovered "The Road" that way). Providers are now always asked as
    # well -- see tests/test_recovery_merge.py. What must NOT change is this:
    # a scrambled title still finds the catalogue book, and a provider that
    # offers nothing cannot take it away.
    asked = []

    def providers_find_nothing(*args, **kwargs):
        asked.append(args)
        return {"decision": REJECTED, "candidates": [], "rejected_count": 0}

    monkeypatch.setattr(app_module, "retrieve_ranked_candidates",
                        providers_find_nothing)

    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (png_bytes(), "cover.png")},
                           content_type="multipart/form-data")
    data = response.get_json()
    assert response.status_code == 200
    assert data["status"] == "needs_confirmation"
    assert data["candidates"][0]["title"] == "The Horse and His Boy"
    assert data["candidates"][0]["provider"] == "local_catalogue"
    assert asked, "providers were never consulted for a recovered match"


def test_scan_escalates_when_confident_ocr_does_not_match_a_book(client, monkeypatch):
    token = register_and_login(client)
    database.create_catalogue_book({
        "title": "Dune", "author": "Frank Herbert",
        "isbn_13": "9780441172719",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified summary long enough for the test.",
        "short_summary": "A stored overview.", "short_summary_status": "ok",
        "verification_status": "VERIFIED"})
    calls = []

    def ocr_result(*args, **kwargs):
        tier = kwargs.get("rec_tier")
        calls.append(tier)
        if len(calls) == 1:
            return {"probable_title": "UNRELATED LARGE COVER WORDS",
                    "probable_author": "Unknown", "full_text": "UNRELATED WORDS",
                    "text_lines": ["UNRELATED", "WORDS"],
                    "confidence_score": 0.97}
        return {"probable_title": "Dune", "probable_author": "Frank Herbert",
                "full_text": "Dune Frank Herbert",
                "text_lines": ["Dune", "Frank Herbert"],
                "confidence_score": 0.88}

    monkeypatch.setattr(app_module, "process_book_cover", ocr_result)
    monkeypatch.setattr(
        app_module, "retrieve_ranked_candidates",
        lambda *a, **k: pytest.fail("Escalated local hit called network"))
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (png_bytes(), "cover.png")},
                           content_type="multipart/form-data")
    data = response.get_json()
    assert len(calls) == 2
    assert response.status_code == 200
    assert data["status"] == "success"
    assert data["book"]["title"] == "Dune"


def test_manual_candidates_are_not_persisted_before_confirmation(client, monkeypatch):
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates", lambda *a, **k: {
        "decision": "NEEDS_CONFIRMATION", "candidates": [candidate()]})
    response = client.post("/api/search-by-title", headers=auth(token),
                           json={"title": "Dune", "author": "Frank Herbert"})
    data = response.get_json()
    assert data["status"] == "needs_confirmation"
    conn = database.get_db()
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0
    conn.close()

    monkeypatch.setattr(app_module, "hydrate_exact_candidate", lambda c: c)
    monkeypatch.setattr(app_module, "build_external_overview", lambda c: {
        "status": "unavailable", "overview": "", "source_text": "",
        "source": None, "reason": "exact_sources_had_no_usable_description"})
    confirmed = client.post("/api/identify/confirm", headers=auth(token), json={
        "attempt_id": data["attempt_id"],
        "candidate_id": data["candidates"][0]["candidate_id"]})
    assert confirmed.status_code == 200
    assert confirmed.get_json()["summary_status"] == "unavailable"
    conn = database.get_db()
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1
    conn.close()


def test_external_exact_description_uses_deterministic_overview_and_cache(client, monkeypatch):
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates", lambda *a, **k: {
        "decision": "NEEDS_CONFIRMATION", "candidates": [candidate()]})
    monkeypatch.setattr(app_module, "hydrate_exact_candidate", lambda c: c)
    overview_calls = []
    source_text = (
        "Paul arrives on Arrakis with his family and enters a dangerous political "
        "struggle. When betrayal destroys their position, he must survive the "
        "desert and decide how to respond to the conflict around him."
    )
    overview_text = (
        "When betrayal destroys their position, Paul must survive the desert and "
        "decide how to respond to the dangerous political conflict around him."
    )
    monkeypatch.setattr(app_module, "build_external_overview", lambda c: (
        overview_calls.append(c["google_books_id"]) or {
            "status": "ready", "overview": overview_text,
            "source_text": source_text, "source": "google_volume",
            "reason": "", "method": app_module.EXTERNAL_OVERVIEW_METHOD}))

    found = client.post("/api/search-by-title", headers=auth(token),
                        json={"title": "Dune", "author": "Frank Herbert"}).get_json()
    result = client.post("/api/identify/confirm", headers=auth(token), json={
        "attempt_id": found["attempt_id"],
        "candidate_id": found["candidates"][0]["candidate_id"]}).get_json()
    assert result["summary_trust"] == "EXTERNAL_NOT_VERIFIED"
    assert result["book"]["ai_summary"] == overview_text
    assert overview_calls == ["dune-google"]
    conn = database.get_db()
    cached = conn.execute("SELECT * FROM external_summary_cache").fetchall()
    conn.close()
    assert len(cached) == 1
    assert cached[0]["provider_id"] == "dune-google"
    assert cached[0]["summary_method"] == app_module.EXTERNAL_OVERVIEW_METHOD
    assert cached[0]["trust_status"] == "EXTERNAL_NOT_VERIFIED"


def test_external_network_or_description_failure_is_not_cached(client, monkeypatch):
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates", lambda *a, **k: {
        "decision": "NEEDS_CONFIRMATION", "candidates": [candidate()]})
    monkeypatch.setattr(app_module, "hydrate_exact_candidate", lambda c: c)
    monkeypatch.setattr(app_module, "build_external_overview", lambda c: {
        "status": "unavailable", "overview": "", "source_text": "",
        "source": None, "reason": "exact_sources_had_no_usable_description"})
    found = client.post("/api/search-by-title", headers=auth(token),
                        json={"title": "Dune", "author": "Frank Herbert"}).get_json()
    result = client.post("/api/identify/confirm", headers=auth(token), json={
        "attempt_id": found["attempt_id"],
        "candidate_id": found["candidates"][0]["candidate_id"]}).get_json()
    assert result["summary_status"] == "unavailable"
    conn = database.get_db()
    assert conn.execute("SELECT COUNT(*) FROM external_summary_cache").fetchone()[0] == 0
    conn.close()


def test_local_catalogue_returns_stored_summary_without_model_or_network(client, monkeypatch):
    token = register_and_login(client)
    admin_id = database.create_user("Admin", "admin2@example.com", "hash", 1)
    database.create_catalogue_book({
        "title": "Dune", "author": "Frank Herbert", "google_volume_id": "dune-google",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "This is the administrator-verified full summary for Dune.",
        "short_summary": "Stored short summary for Dune.",
        "short_summary_status": "ok",
        "short_summary_method": "ai_model",
        "verification_status": "VERIFIED"}, admin_id)
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates",
                        lambda *a, **k: pytest.fail("Tier 1 hit called the network"))
    monkeypatch.setattr(app_module, "hydrate_exact_candidate",
                        lambda c: pytest.fail("Tier 1 hit hydrated externally"))
    monkeypatch.setattr(app_module, "build_external_overview",
                        lambda *a, **k: pytest.fail("Tier 1 ran external heuristic"))
    response = client.post("/api/search-by-title", headers=auth(token),
                           json={"title": "Dune", "author": "Frank Herbert"})
    assert response.status_code == 200
    found = response.get_json()
    assert found["source"] == "local_catalogue"
    assert found["summary_status"] == "ready"
    assert found["book"]["ai_summary"] == "Stored short summary for Dune."
    assert found["book"]["verified_summary"].startswith("This is the administrator")


def test_local_ambiguity_does_not_fall_through_to_external(client, monkeypatch):
    token = register_and_login(client)
    for title, isbn in (("The Stand", "9780306406157"),
                        ("The Stand: Complete Edition", "9780441172719")):
        database.create_catalogue_book({
            "title": title, "author": "Stephen King", "isbn_13": isbn,
            "source_dataset": "CMU Book Summary Corpus",
            "verified_summary": "A sufficiently detailed verified source summary.",
            "short_summary": "Stored overview.", "short_summary_status": "ok",
            "verification_status": "VERIFIED"})
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates",
                        lambda *a, **k: pytest.fail("Ambiguous Tier 1 called network"))
    result = client.post("/api/search-by-title", headers=auth(token),
                         json={"title": "The Stand", "author": "Stephen King"}).get_json()
    assert result["status"] in {"success", "needs_confirmation"}
    if result["status"] == "needs_confirmation":
        assert all(c["provider"] == "local_catalogue" for c in result["candidates"])


def test_true_catalogue_miss_uses_external_tier(client, monkeypatch):
    token = register_and_login(client)
    calls = []
    monkeypatch.setattr(app_module, "retrieve_ranked_candidates",
                        lambda *a, **k: (calls.append(a) or {
                            "decision": "REJECTED", "candidates": [],
                            "error": "External miss"}))
    result = client.post("/api/search-by-title", headers=auth(token),
                         json={"title": "A Book Not In This Catalogue",
                               "author": "No Such Author"}).get_json()
    assert calls
    assert result["decision"] == "REJECTED"


def test_normal_user_cannot_access_admin(client):
    token = register_and_login(client)
    assert client.get("/api/admin/catalogue", headers=auth(token)).status_code == 403


def test_admin_can_access_catalogue(client):
    from werkzeug.security import generate_password_hash
    database.create_user("Admin", "admin@example.com",
                         generate_password_hash("strongpass"), 1,
                         email_verified=1)
    token = client.post("/api/login", json={"email": "admin@example.com",
                                             "password": "strongpass"}).get_json()["token"]
    assert client.get("/api/admin/catalogue", headers=auth(token)).status_code == 200
