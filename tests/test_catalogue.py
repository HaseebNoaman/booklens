import pytest

import database


@pytest.fixture()
def db(tmp_path):
    database.DB_NAME = str(tmp_path / "catalogue.db")
    database.init_db()
    database.init_db()
    return database.create_user("Admin", "admin@example.com", "hash", 1)


def test_identifier_first_verified_lookup(db):
    record_id = database.create_catalogue_book({
        "title": "Dune", "author": "Frank Herbert",
        "isbn_13": "9780441172719", "google_volume_id": "google-dune",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified full summary long enough for the catalogue.",
        "verification_status": "VERIFIED",
    }, db)
    assert database.lookup_verified_catalogue({"isbn_13": "9780441172719"})["id"] == record_id
    assert database.lookup_verified_catalogue({"google_books_id": "google-dune"})["id"] == record_id


def test_verified_title_and_author_pair_allowed_but_title_only_rejected(db):
    database.create_catalogue_book({
        "title": "The Hobbit", "author": "J. R. R. Tolkien",
        "google_volume_id": "google-hobbit",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "Bilbo Baggins joins an unexpected journey.",
        "verification_status": "VERIFIED"}, db)
    assert database.lookup_verified_catalogue(
        {"title": "The Hobbit", "author": "J R R Tolkien"}) is not None
    assert database.lookup_verified_catalogue({"title": "The Hobbit"}) is None


def test_pending_record_does_not_verify(db):
    database.create_catalogue_book({"title": "Pending Book", "author": "Careful Admin",
        "isbn_13": "9780306406157", "verification_status": "PENDING"}, db)
    assert database.lookup_verified_catalogue({"isbn_13": "9780306406157"}) is None


def test_duplicate_identifier_is_rejected(db):
    payload = {"title": "One", "author": "Author", "isbn_13": "9780306406157"}
    database.create_catalogue_book(payload, db)
    with pytest.raises(ValueError):
        database.create_catalogue_book({**payload, "title": "Duplicate"}, db)


def test_catalogue_counts(db):
    database.create_catalogue_book({
        "title": "V", "author": "A", "google_volume_id": "verified-v",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified source summary for this record.",
        "verification_status": "VERIFIED"}, db)
    database.create_catalogue_book({"title": "P", "author": "B"}, db)
    counts = database.catalogue_counts()
    assert counts["total"] == 2 and counts["verified"] == 1 and counts["pending"] == 1


def test_open_library_identifiers_are_exact_lookup_keys(db):
    record_id = database.create_catalogue_book({
        "title": "Open Book", "author": "Library Author",
        "open_library_edition_id": "OL123M", "open_library_work_id": "/works/OL456W",
        "source_dataset": "CMU Book Summary Corpus",
        "verified_summary": "A verified source summary for this record.",
        "verification_status": "VERIFIED"}, db)
    assert database.lookup_verified_catalogue(
        {"open_library_edition_id": "OL123M"})["id"] == record_id
    assert database.lookup_verified_catalogue(
        {"open_library_work_id": "/works/OL456W"})["id"] == record_id


def test_rejected_record_never_verifies(db):
    database.create_catalogue_book({"title": "Rejected", "author": "Reviewer",
        "isbn_13": "9780306406157", "verification_status": "REJECTED"}, db)
    assert database.lookup_verified_catalogue({"isbn_13": "9780306406157"}) is None


def test_migration_disables_legacy_unverified_ai_summary(db):
    book_id = database.save_book({
        "title": "Legacy Cache", "author": "Old Pipeline",
        "description": "Unverified API description.",
        "ai_summary": "A legacy generated summary.",
        "description_source": "google_volume",
    })
    database.init_db()
    migrated = database.get_book_by_id(book_id)
    assert migrated["ai_summary"] == ""
    assert migrated["verified_summary"] == ""
    assert migrated["catalogue_id"] is None
    assert migrated["summary_status"] == "unavailable"


def test_verified_record_requires_provenance_summary_and_exact_identifier(db):
    base = {"title": "Strict", "author": "Verifier",
            "verification_status": "VERIFIED"}
    with pytest.raises(ValueError, match="source_dataset"):
        database.create_catalogue_book(base, db)
    with pytest.raises(ValueError, match="verified_summary"):
        database.create_catalogue_book({**base, "source_dataset": "CMU"}, db)
    with pytest.raises(ValueError, match="exact identifier"):
        database.create_catalogue_book({**base, "source_dataset": "CMU",
            "verified_summary": "Verified text."}, db)


def test_catalogue_defaults_record_machine_not_human_verification(db):
    record_id = database.create_catalogue_book({
        "title": "Machine Checked", "author": "Source Author",
        "google_volume_id": "machine-check", "source_dataset": "CMU",
        "verified_summary": "Verified text from the source corpus.",
        "verification_status": "VERIFIED"}, db)
    row = database.get_catalogue_book(record_id)
    assert row["machine_verified"] == 1
    assert row["human_verified"] == 0
    assert row["short_summary_status"] == "pending"


def test_human_review_requires_name_and_timestamp(db):
    with pytest.raises(ValueError, match="reviewed_by and reviewed_at"):
        database.create_catalogue_book({
            "title": "Reviewed", "author": "Reviewer",
            "google_volume_id": "reviewed-id", "source_dataset": "CMU",
            "verified_summary": "Verified text.",
            "verification_status": "VERIFIED", "human_verified": True}, db)


def test_blank_unique_identifiers_are_stored_as_null(db):
    first = database.create_catalogue_book({"title": "One", "author": "A"}, db)
    second = database.create_catalogue_book({"title": "Two", "author": "B"}, db)
    assert database.get_catalogue_book(first)["isbn_13"] is None
    assert database.get_catalogue_book(second)["google_volume_id"] is None
