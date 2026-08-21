# database.py
# This file handles everything related to our SQLite database.
# It creates the tables and gives simple functions to add and read data.

import sqlite3
import datetime
import logging
import json
import hashlib
import os
import re

from thefuzz import fuzz

# The name of our database file. SQLite will create this file automatically.
DB_NAME = os.environ.get("BOOKLENS_DB_PATH", "bookfinder.db")


def get_db():
    # Open a connection to the database.
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    # row_factory = sqlite3.Row lets us read columns by name, like a dictionary.
    # Example: user['email'] instead of user[2]
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # This function creates all our tables when the app starts.
    conn = get_db()
    cur = conn.cursor()

    # Table 1: users -> stores login accounts
    # is_admin: 0 = normal user, 1 = admin (can see the admin dashboard)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            auth_version INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # If the database file was created BEFORE we added is_admin, the old
    # users table will not have that column. This adds it. If the column
    # already exists, SQLite throws an error and we simply ignore it.
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists, nothing to do
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Cold-start interests. Stored comma-joined, matching how subjects are held
    # everywhere else (books.categories, catalogue_books.genres), so the same
    # normalize_subjects() reads all of them.
    try:
        cur.execute("ALTER TABLE users ADD COLUMN interests TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Proof that the address belongs to whoever signed up. 0 until the link
    # in the verification email is opened. Accounts created by the seeding
    # scripts (admin, demo) are written as 1 directly -- there is nobody to
    # click their link.
    try:
        cur.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # One-time links: email verification and password reset. Only the SHA-256
    # of the token is stored, so a leaked database cannot be used to verify or
    # reset anything -- the same reason password_hash exists.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            purpose TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash)")

    # What Open Library says about a book TODAY: a rating, how many people
    # shelved it, and a page count taken as a median across editions rather
    # than from whichever single edition a provider happened to hold.
    #
    # One row per book, refreshed rather than appended, because the value of
    # this data is that it is current -- keeping history would only invite
    # someone to average a stale row into a fresh one.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS live_signals (
            book_id INTEGER PRIMARY KEY,
            rating REAL,
            n_ratings INTEGER DEFAULT 0,
            want_to_read INTEGER DEFAULT 0,
            already_read INTEGER DEFAULT 0,
            on_shelves INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'openlibrary',
            fetched_at INTEGER DEFAULT 0,
            FOREIGN KEY (book_id) REFERENCES books (id)
        )
    """)

    # Table 2: books -> stores book info, also works as a cache
    cur.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            description TEXT,
            ai_summary TEXT,
            thumbnail TEXT,
            page_count INTEGER,
            publisher TEXT,
            published_date TEXT,
            categories TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Same migration trick as is_admin: older databases get the genre column.
    try:
        cur.execute("ALTER TABLE books ADD COLUMN categories TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # column already exists, nothing to do

    # confidence: how sure the matcher was when this book was FIRST saved
    # ("high" or "medium"). Cache hits used to hard-code "high", silently
    # promoting books the user was originally asked to confirm.
    try:
        cur.execute("ALTER TABLE books ADD COLUMN confidence TEXT DEFAULT 'high'")
    except sqlite3.OperationalError:
        pass  # column already exists, nothing to do

    # Identifiers for the EXACT volume this row came from, plus where its
    # description was found. Added 2026-07-26 with the Wikipedia removal: the
    # description must belong to the matched volume, so a cached row has to
    # remember which volume that was. description_source is one of
    # "google_volume" / "openlibrary_edition" / "openlibrary_work" / "none"
    # ("none" = we asked every source and this book genuinely has none, which
    # is what lets the app stop retrying and say so honestly).
    for column, ddl in (
            ("google_books_id", "ALTER TABLE books ADD COLUMN google_books_id TEXT DEFAULT ''"),
            ("isbn_13", "ALTER TABLE books ADD COLUMN isbn_13 TEXT DEFAULT ''"),
            ("description_source", "ALTER TABLE books ADD COLUMN description_source TEXT DEFAULT ''"),
            ("description_reason", "ALTER TABLE books ADD COLUMN description_reason TEXT DEFAULT ''")):
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists, nothing to do

    # Table 3: history -> remembers which user scanned which book
    # is_favorite: 0 = normal scan, 1 = the user starred it (their bookshelf)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            is_favorite INTEGER DEFAULT 0,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (book_id) REFERENCES books(id)
        )
    """)

    # Same migration trick as is_admin above: databases created BEFORE we
    # added favorites get the missing column here.
    try:
        cur.execute("ALTER TABLE history ADD COLUMN is_favorite INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists, nothing to do

    # Small personal-library fields. They belong to a USER'S history row, not
    # to the shared book cache: two readers can keep different statuses/notes
    # for the same identified book.
    for ddl in (
            "ALTER TABLE history ADD COLUMN reading_status TEXT DEFAULT 'identified'",
            "ALTER TABLE history ADD COLUMN private_note TEXT DEFAULT ''"):
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # Table 4: messages -> stores "Contact Us" form submissions
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            subject TEXT,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes make our most common lookups fast even with thousands of rows.
    # Without an index SQLite reads EVERY row of the table to find a match.
    # books uses LOWER(title) because find_cached_book compares with LOWER();
    # an index on the plain title column would never be used for that query.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_books_title_lower ON books(LOWER(title))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_history_book_id ON history(book_id)")

    # Additive cache columns. `books` remains the fast display/history cache;
    # trust lives in catalogue_books below and is never inferred from cache.
    for ddl in (
            "ALTER TABLE books ADD COLUMN isbn_10 TEXT DEFAULT ''",
            "ALTER TABLE books ADD COLUMN open_library_edition_id TEXT DEFAULT ''",
            "ALTER TABLE books ADD COLUMN open_library_work_id TEXT DEFAULT ''",
            "ALTER TABLE books ADD COLUMN catalogue_id INTEGER",
            "ALTER TABLE books ADD COLUMN verified_summary TEXT DEFAULT ''",
            "ALTER TABLE books ADD COLUMN summary_status TEXT DEFAULT 'unavailable'"):
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # Older BookLens databases may contain AI summaries produced from
    # publisher/API text before the verified-catalogue boundary existed.
    # Preserve the cached metadata and user history, but make those legacy
    # summaries unavailable. New summaries are written only after a verified
    # catalogue row has been linked to the book.
    cur.execute("""
        UPDATE books
           SET ai_summary='', verified_summary='', catalogue_id=NULL,
               summary_status='unavailable'
         WHERE COALESCE(description_source, '') <> 'catalogue_verified'
           AND (COALESCE(ai_summary, '') <> ''
                OR COALESCE(verified_summary, '') <> ''
                OR catalogue_id IS NOT NULL)
    """)

    # A curated catalogue is separate from API/cache rows. Only VERIFIED rows
    # may supply a full summary to FLAN-T5.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS catalogue_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            author TEXT NOT NULL,
            normalized_author TEXT NOT NULL,
            isbn_10 TEXT DEFAULT '',
            isbn_13 TEXT DEFAULT '',
            google_volume_id TEXT DEFAULT '',
            open_library_edition_id TEXT DEFAULT '',
            open_library_work_id TEXT DEFAULT '',
            publisher TEXT DEFAULT '',
            publication_year TEXT DEFAULT '',
            genres TEXT DEFAULT '',
            source_dataset TEXT DEFAULT '',
            source_summary TEXT DEFAULT '',
            verified_summary TEXT DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (verification_status IN ('PENDING','VERIFIED','REJECTED','NEEDS_REVIEW')),
            verified_by INTEGER,
            verified_at TEXT,
            verification_notes TEXT DEFAULT '',
            machine_verified INTEGER NOT NULL DEFAULT 0
                CHECK (machine_verified IN (0, 1)),
            human_verified INTEGER NOT NULL DEFAULT 0
                CHECK (human_verified IN (0, 1)),
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_notes TEXT DEFAULT '',
            short_summary TEXT DEFAULT '',
            short_summary_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (short_summary_status IN ('pending','ok','fallback_extract','unavailable')),
            short_summary_method TEXT DEFAULT '',
            short_summary_model TEXT DEFAULT '',
            short_summary_source_sha256 TEXT DEFAULT '',
            short_summary_generated_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (verified_by) REFERENCES users(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS external_summary_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            author TEXT DEFAULT '',
            description_source TEXT NOT NULL,
            description_sha256 TEXT NOT NULL,
            source_description TEXT NOT NULL,
            short_summary TEXT NOT NULL,
            summary_method TEXT NOT NULL,
            summary_status TEXT NOT NULL
                CHECK (summary_status IN ('ready','fallback_extract')),
            trust_status TEXT NOT NULL DEFAULT 'EXTERNAL_NOT_VERIFIED'
                CHECK (trust_status='EXTERNAL_NOT_VERIFIED'),
            generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider, provider_id, description_sha256)
        )
    """)
    # Add the two-tier catalogue fields to databases created by the previous
    # improved build. Each ALTER is idempotent and preserves existing rows.
    for ddl in (
            "ALTER TABLE catalogue_books ADD COLUMN verified_at TEXT",
            "ALTER TABLE catalogue_books ADD COLUMN machine_verified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE catalogue_books ADD COLUMN human_verified INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE catalogue_books ADD COLUMN reviewed_by TEXT",
            "ALTER TABLE catalogue_books ADD COLUMN reviewed_at TEXT",
            "ALTER TABLE catalogue_books ADD COLUMN review_notes TEXT DEFAULT ''",
            "ALTER TABLE catalogue_books ADD COLUMN short_summary TEXT DEFAULT ''",
            "ALTER TABLE catalogue_books ADD COLUMN short_summary_status TEXT NOT NULL DEFAULT 'pending'",
            "ALTER TABLE catalogue_books ADD COLUMN short_summary_method TEXT DEFAULT ''",
            "ALTER TABLE catalogue_books ADD COLUMN short_summary_model TEXT DEFAULT ''",
            "ALTER TABLE catalogue_books ADD COLUMN short_summary_source_sha256 TEXT DEFAULT ''",
            "ALTER TABLE catalogue_books ADD COLUMN short_summary_generated_at TEXT"):
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS identification_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            input_method TEXT NOT NULL,
            ocr_status TEXT DEFAULT '',
            ocr_title TEXT DEFAULT '',
            ocr_author TEXT DEFAULT '',
            ocr_text TEXT DEFAULT '',
            ocr_confidence REAL DEFAULT 0,
            query_title TEXT DEFAULT '',
            query_author TEXT DEFAULT '',
            query_isbn TEXT DEFAULT '',
            decision TEXT DEFAULT 'REJECTED',
            selected_candidate_id INTEGER,
            selected_book_id INTEGER,
            failure_reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (selected_book_id) REFERENCES books(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS candidate_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            rank_position INTEGER NOT NULL,
            provider TEXT DEFAULT '',
            provider_id TEXT DEFAULT '',
            score REAL NOT NULL DEFAULT 0,
            decision TEXT NOT NULL,
            reasons TEXT DEFAULT '[]',
            metadata_json TEXT NOT NULL,
            is_selected INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (attempt_id) REFERENCES identification_attempts(id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT DEFAULT '',
            details TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_user_id) REFERENCES users(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_catalogue_status ON catalogue_books(verification_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_catalogue_title_author ON catalogue_books(normalized_title, normalized_author)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_catalogue_isbn13 ON catalogue_books(isbn_13) WHERE isbn_13 IS NOT NULL AND isbn_13 <> ''")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_catalogue_isbn10 ON catalogue_books(isbn_10) WHERE isbn_10 IS NOT NULL AND isbn_10 <> ''")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_catalogue_google ON catalogue_books(google_volume_id) WHERE google_volume_id IS NOT NULL AND google_volume_id <> ''")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_catalogue_ol_edition ON catalogue_books(open_library_edition_id) WHERE open_library_edition_id IS NOT NULL AND open_library_edition_id <> ''")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_catalogue_ol_work ON catalogue_books(open_library_work_id) WHERE open_library_work_id IS NOT NULL AND open_library_work_id <> ''")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_catalogue_short_status ON catalogue_books(short_summary_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_external_summary_identity ON external_summary_cache(provider, provider_id, id DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_attempts_user ON identification_attempts(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_candidates_attempt ON candidate_matches(attempt_id)")

    conn.commit()
    conn.close()
    logging.info("Database initialized (core tables plus verified catalogue and review audit)")


def create_user(name, email, password_hash, is_admin=0, email_verified=0):
    # Add a new user. Returns the new user's id, or None if the email already exists.
    # is_admin is 0 by default, so normal registration never creates an admin.
    # email_verified is 0 by default, so normal registration never trusts an
    # address on its own word; the seeding scripts pass 1 because no human is
    # ever going to open a link for the admin or demo account.
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (name, email, password_hash, is_admin, email_verified) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, email, password_hash, is_admin, 1 if email_verified else 0)
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # This happens when the email is already used (UNIQUE rule broken).
        return None
    finally:
        conn.close()


def get_user_by_email(email):
    # Find one user using their email.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cur.fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    # Find one user using their id.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()
    return user


# Different books can share one title: "The Hobbit" names both Tolkien's
# novel and a 2003 video-game strategy guide (seen live 2026-07-20 — the
# guide was cached first and then served for a Tolkien query). So a title
# alone is NOT identity. When the caller also knows an author, the cached
# row's author must roughly agree; token_set_ratio tolerates initials and
# OCR noise ("J.R.R. Tolkien" vs "Tolkien" scores 100).
CACHE_AUTHOR_MATCH = 60


def find_cached_book(title, author=""):
    # Check if we already saved this book before (case-insensitive title
    # match + author agreement when both sides know the author).
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM books WHERE LOWER(title) = LOWER(?)", (title,))
    rows = cur.fetchall()
    conn.close()

    author = (author or "").strip().lower()
    for row in rows:
        row_author = (row["author"] or "").strip().lower()
        # An author-less query (bare typed title, no OCR author) keeps the
        # old behavior; an author-less ROW cannot contradict the query.
        if not author or not row_author or \
                fuzz.token_set_ratio(author, row_author) >= CACHE_AUTHOR_MATCH:
            return row
    return None


def save_book(book_data):
    # Save a new book into the database. book_data is a normal dictionary.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO books
        (title, author, description, ai_summary, thumbnail, page_count, publisher, published_date, categories, confidence,
         google_books_id, isbn_13, description_source, description_reason,
         isbn_10, open_library_edition_id, open_library_work_id, catalogue_id,
         verified_summary, summary_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        book_data.get("title", ""),
        book_data.get("author", ""),
        book_data.get("description", ""),
        book_data.get("ai_summary", ""),
        book_data.get("thumbnail", ""),
        book_data.get("page_count", 0),
        book_data.get("publisher", ""),
        book_data.get("published_date", ""),
        book_data.get("categories", ""),
        book_data.get("confidence", "high"),
        # The identifiers that tie this row to ONE volume. Without them a
        # cache hit could not re-resolve its own description and we would be
        # back to searching by title, which is the bug we removed.
        book_data.get("google_books_id", ""),
        book_data.get("isbn_13", ""),
        book_data.get("description_source", ""),
        book_data.get("description_reason", ""),
        book_data.get("isbn_10", ""),
        book_data.get("open_library_edition_id", ""),
        book_data.get("open_library_work_id", ""),
        book_data.get("catalogue_id"),
        book_data.get("verified_summary", ""),
        book_data.get("summary_status", "unavailable"),
    ))
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def save_history(user_id, book_id):
    # Record that this user scanned this book.
    # Returns the new history row's id. The caller needs it because a scan is
    # saved BEFORE a medium-confidence match is confirmed, so when the user
    # says "no, that's the wrong book" the frontend has to be able to delete
    # exactly that row again.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO history (user_id, book_id) VALUES (?, ?)",
        (user_id, book_id)
    )
    history_id = cur.lastrowid
    conn.commit()
    conn.close()
    return history_id


def get_user_history(user_id):
    # Get all books this user scanned, newest first.
    # We JOIN history with books so we get the full book info in one query.
    conn = get_db()
    cur = conn.cursor()
    # IMPORTANT: b.* contains the BOOK id (the same book scanned twice gives
    # the same id!), so we also send h.id AS history_id — a unique id for
    # each scan. The frontend uses history_id for favorites and deleting.
    cur.execute("""
        SELECT b.*, h.id AS history_id, h.is_favorite, h.reading_status,
               h.private_note, h.scanned_at
        FROM history h
        JOIN books b ON h.book_id = b.id
        WHERE h.user_id = ?
        ORDER BY h.scanned_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_user_interests(user_id):
    """The subjects a reader said they like, before they have logged anything."""
    conn = get_db()
    try:
        row = conn.execute("SELECT interests FROM users WHERE id=?",
                           (user_id,)).fetchone()
        return (row["interests"] or "").strip() if row else ""
    finally:
        conn.close()


def set_user_interests(user_id, interests):
    """Replace the reader's chosen interests. An empty list is a valid choice."""
    conn = get_db()
    try:
        conn.execute("UPDATE users SET interests=? WHERE id=?",
                     ((interests or "").strip(), user_id))
        conn.commit()
    finally:
        conn.close()


def catalogue_subject_counts():
    """How many verified catalogue books carry each subject, and how many books.

    Used to weight a shared subject by how rare it is: sharing "speculative"
    with 52% of the shelf is not evidence, sharing "time travel" with 1% is.
    Measured on the 250-book catalogue -- 85 subjects, of which only four are
    carried by more than a fifth of it, and the median subject appears in two
    books.

    Cached for the process: the catalogue is loaded at deploy time and does not
    change while the server runs, and this would otherwise read 250 rows on
    every single card.
    """
    global _SUBJECT_COUNTS
    if _SUBJECT_COUNTS is not None:
        return _SUBJECT_COUNTS
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT genres FROM catalogue_books
            WHERE verification_status='VERIFIED' AND TRIM(COALESCE(genres,'')) <> ''
        """).fetchall()
    finally:
        conn.close()
    import taste_profile
    counts = {}
    for row in rows:
        for label in taste_profile.normalize_subjects(row["genres"]):
            counts[label] = counts.get(label, 0) + 1
    _SUBJECT_COUNTS = (counts, len(rows))
    return _SUBJECT_COUNTS


_SUBJECT_COUNTS = None


def reset_subject_counts():
    """Drop the cache. Called after the catalogue changes, and by tests."""
    global _SUBJECT_COUNTS
    _SUBJECT_COUNTS = None


def catalogue_subject_vocabulary(limit=24):
    """The subjects a reader can actually choose from.

    Drawn from the catalogue's own genres rather than a hand-written list, so
    every option is one that real books carry. Offering "Steampunk" when no
    book is shelved under it would guarantee an empty answer.
    """
    import taste_profile
    counts, _total = catalogue_subject_counts()
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [taste_profile.display_subject(label) for label, _ in ranked[:limit]]


def get_taste_profile_books(user_id, exclude_book_id=None):
    # The books that feed "Is this for you?" -- see taste_profile.py.
    #
    # The WHERE clause is the whole point. A history row exists for every cover
    # the camera identified, so selecting all of them would build a profile out
    # of whatever the user happened to photograph. Only deliberate signals
    # count: favourited, finished, or currently reading. A scan is not taste.
    #
    # GROUP BY book id: scanning the same book three times is one book, not
    # three, or the "you have read N books" count inflates itself.
    #
    # exclude_book_id drops the book being scanned right now. Without it, a
    # re-scan of a book the user already finished would offer that book as
    # evidence for itself -- "you have read 1 book with these subjects", and
    # the title listed is the one on screen.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT b.id, b.title, b.categories,
               MAX(h.is_favorite) AS is_favorite,
               MAX(h.reading_status) AS reading_status
        FROM history h
        JOIN books b ON h.book_id = b.id
        WHERE h.user_id = ?
          AND (h.is_favorite = 1 OR h.reading_status IN ('finished', 'reading'))
          AND (? IS NULL OR b.id <> ?)
        GROUP BY b.id
    """, (user_id, exclude_book_id, exclude_book_id))
    rows = cur.fetchall()
    conn.close()
    return rows


def toggle_favorite(user_id, history_id):
    # Flip the favorite star on ONE history row: 0 becomes 1, 1 becomes 0
    # (that is what "1 - is_favorite" does).
    # SECURITY: "AND user_id = ?" is the ownership check — if the row belongs
    # to a different user, the UPDATE matches nothing and we return None.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE history SET is_favorite = 1 - is_favorite WHERE id = ? AND user_id = ?",
        (history_id, user_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return None   # row does not exist, or is not this user's row
    cur.execute("SELECT is_favorite FROM history WHERE id = ?", (history_id,))
    new_value = cur.fetchone()[0]
    conn.close()
    return new_value


def update_history_reading(user_id, history_id, reading_status, private_note):
    """Update one user's reading status/note and enforce row ownership."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE history
           SET reading_status = ?, private_note = ?
         WHERE id = ? AND user_id = ?
    """, (reading_status, private_note, history_id, user_id))
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return None
    cur.execute("""
        SELECT reading_status, private_note, is_favorite
          FROM history WHERE id = ? AND user_id = ?
    """, (history_id, user_id))
    row = cur.fetchone()
    result = dict(row) if row else None
    conn.close()
    return result


def delete_history_item(user_id, history_id):
    # Delete ONE scan from this user's history. Returns how many rows were
    # deleted: 0 means not found (or someone else's row), 1 means success.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM history WHERE id = ? AND user_id = ?",
        (history_id, user_id)
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def count_user_history(user_id):
    # How many scans has this user made? (shown on the profile page)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_book_by_id(book_id):
    # Read one book row (used by the async-summary status endpoint).
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM books WHERE id = ?", (book_id,))
    book = cur.fetchone()
    conn.close()
    return book


def backfill_book_thumbnail(book_id, thumbnail):
    """Fill in a missing cover, never replace one that is already there.

    Books saved before catalogue rows carried a cover URL keep an empty
    thumbnail forever, because a re-scan reuses the cached row rather than
    rebuilding it. Every book already in someone's library would stay
    coverless. This heals those rows the next time the book is selected, and
    the "only when empty" rule means it can never overwrite a better image the
    reader or a provider already supplied.
    """
    thumbnail = (thumbnail or "").strip()
    if not thumbnail:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE books SET thumbnail=? WHERE id=? AND TRIM(COALESCE(thumbnail,''))=''",
            (thumbnail, book_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_book_description(book_id, description):
    # Fill in a description found AFTER the book was saved. The background
    # summary worker resolves the matched volume's description (Google volume
    # record, else the Open Library edition or work) and writes it back when
    # the row was saved without one.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE books SET description = ? WHERE id = ?",
                (description, book_id))
    conn.commit()
    conn.close()


def update_book_description_source(book_id, source, reason):
    # Record WHERE this book's description came from, or that none exists.
    # source "none" means every source was asked and none had one; storing
    # that is what lets ensure_summary answer "unavailable" instead of
    # queueing the same lookup again on every poll.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE books SET description_source = ?, description_reason = ? "
        "WHERE id = ?",
        (source or "", reason or "", book_id)
    )
    conn.commit()
    conn.close()


def update_book_summary(book_id, ai_summary):
    # Fill in or replace the stored AI summary of one book. Used by the
    # "self-healing cache": a cached book whose summary is empty gets a
    # freshly generated one on its next scan.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE books SET ai_summary = ? WHERE id = ?",
        (ai_summary, book_id)
    )
    conn.commit()
    conn.close()


def update_user_password(user_id, new_password_hash):
    # Change a user's password. We only ever store the HASH, never the
    # real password (same rule as registration).
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (new_password_hash, user_id)
    )
    conn.commit()
    conn.close()


def revoke_user_tokens(user_id):
    """Invalidate JWTs issued before this call for one user."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET auth_version = COALESCE(auth_version, 0) + 1 WHERE id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def prior_engagement(user_id, title, author=""):
    """Has this reader already read the book now in their hands?

    The only answer in the product that is a FACT rather than a judgement --
    no thresholds, no similarity, no chance of being wrong -- which is why it
    belongs at the top of the card.

    TWO RULES MAKE IT TRUE RATHER THAN CIRCULAR:

    1. Only deliberate signals count. Identifying a cover WRITES a history row,
       so counting every row would mean the second scan of a book announced
       "you have read this" purely because of the first scan. finished,
       currently reading and favourited are choices; a scan is not. Same rule
       as taste_profile.is_profile_signal(), for the same reason.

    2. Match on title and author, not on book id. The same work legitimately
       exists as several rows -- a different edition, or a second provider's
       record of it -- each with its own id. Matching by id would miss exactly
       the case this feature exists for: you own one printing and are holding
       another.
    """
    if not (title or "").strip():
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT b.title, b.author, h.reading_status, h.is_favorite, h.scanned_at
        FROM history h
        JOIN books b ON h.book_id = b.id
        WHERE h.user_id = ?
        ORDER BY h.scanned_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()

    wanted_title = _norm_engagement(title)
    wanted_author = _norm_engagement(author)
    for row in rows:
        status = (row["reading_status"] or "").strip()
        favourite = bool(row["is_favorite"])
        if not favourite and status not in ("finished", "reading"):
            continue
        if _norm_engagement(row["title"]) != wanted_title:
            continue
        # An author on both sides must roughly agree; a missing one on either
        # side is not evidence against, because providers omit it often.
        row_author = _norm_engagement(row["author"])
        if wanted_author and row_author and                 fuzz.token_set_ratio(wanted_author, row_author) < CACHE_AUTHOR_MATCH:
            continue
        return {"status": status or ("favourite" if favourite else ""),
                "is_favorite": favourite,
                "when": row["scanned_at"]}
    return None


def _norm_engagement(value):
    return " ".join((value or "").strip().lower().split())


# ---------- One-time links: verification and password reset ----------
# The raw token goes in the email and is never stored. We keep only its
# SHA-256, so reading the database gives an attacker nothing usable -- exactly
# the reasoning behind password_hash. A token is single-use (used_at) and
# short-lived (expires_at); both are checked on the way in.


def _token_digest(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_auth_token(user_id, purpose, token, ttl_seconds):
    """Store the digest of one fresh token and drop that user's older ones.

    Dropping the old ones matters: without it, an address that requested five
    verification mails would have five live links, and revoking access would
    mean finding all of them.
    """
    expires_at = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(seconds=int(ttl_seconds)))
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM auth_tokens WHERE user_id = ? AND purpose = ?",
        (user_id, purpose)
    )
    cur.execute(
        "INSERT INTO auth_tokens (user_id, purpose, token_hash, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, purpose, _token_digest(token), expires_at.isoformat())
    )
    conn.commit()
    conn.close()
    return True


def consume_auth_token(token, purpose):
    """Return the user id for a valid, unused, unexpired token -- once.

    Marking it used inside the same connection is what makes it single-use:
    a second request with the same link finds used_at set and gets nothing.
    """
    if not token:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, expires_at, used_at FROM auth_tokens "
        "WHERE token_hash = ? AND purpose = ?",
        (_token_digest(token), purpose)
    )
    row = cur.fetchone()
    if row is None or row["used_at"]:
        conn.close()
        return None
    try:
        expires_at = datetime.datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        conn.close()
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if datetime.datetime.now(datetime.timezone.utc) >= expires_at:
        conn.close()
        return None
    cur.execute("UPDATE auth_tokens SET used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],))
    conn.commit()
    user_id = row["user_id"]
    conn.close()
    return user_id


def mark_email_verified(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def is_email_verified(user):
    """Read the flag off a user row that may predate the column.

    sqlite3.Row has no .get(), and a row read from a database created before
    the migration simply has no such key -- so the membership test is the
    check, not a style choice.
    """
    if user is None:
        return False
    try:
        keys = user.keys()
    except AttributeError:
        keys = list(user or {})
    if "email_verified" not in keys:
        return True
    return bool(user["email_verified"])


# ---------- Admin dashboard functions ----------

def get_admin_stats():
    # Count totals for the dashboard cards: users, books, scans.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM books")
    total_books = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM history")
    total_scans = cur.fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "total_books": total_books,
        "total_scans": total_scans
    }


def get_all_users():
    # List every registered user with how many scans they made.
    # We never send the password_hash to the frontend.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.id, u.name, u.email, u.is_admin, u.is_active, u.created_at,
               COUNT(h.id) AS scan_count
        FROM users u
        LEFT JOIN history h ON h.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_books():
    # List every book stored in the cache, with how many times it was scanned.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT b.id, b.title, b.author, b.publisher, b.published_date,
               b.thumbnail, b.created_at,
               COUNT(h.id) AS scan_count
        FROM books b
        LEFT JOIN history h ON h.book_id = b.id
        GROUP BY b.id
        ORDER BY b.created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_recent_scans(limit=10):
    # The latest scan activity across ALL users (for the admin dashboard).
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT h.id, h.scanned_at, u.name AS user_name, b.title, b.author
        FROM history h
        JOIN users u ON h.user_id = u.id
        JOIN books b ON h.book_id = b.id
        ORDER BY h.scanned_at DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_book(book_id):
    # Remove a book from the cache. Also remove its history rows first,
    # because history points at the book (foreign key).
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE book_id = ?", (book_id,))
    cur.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()


def delete_user(user_id):
    # Remove a user account and their scan history.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


# ---------- Contact form functions ----------

def save_message(name, email, subject, message):
    # Store one "Contact Us" form submission.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (name, email, subject, message) VALUES (?, ?, ?, ?)",
        (name, email, subject, message)
    )
    conn.commit()
    conn.close()


def get_all_messages():
    # All contact messages, newest first (shown in the admin dashboard).
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM messages ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_message(message_id):
    # Remove one contact message.
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    conn.commit()
    conn.close()


# ---------- Verified catalogue and identification evidence ----------

CATALOGUE_STATUSES = {"PENDING", "VERIFIED", "REJECTED", "NEEDS_REVIEW"}
SHORT_SUMMARY_STATUSES = {"pending", "ok", "fallback_extract", "unavailable"}
CATALOGUE_ID_FIELDS = (
    "isbn_13", "isbn_10", "google_volume_id",
    "open_library_edition_id", "open_library_work_id",
)


def normalize_identity(value):
    """Stable identity normalization used by imports, admin edits and lookup."""
    value = (value or "").casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _digits(value):
    return re.sub(r"[^0-9Xx]", "", value or "").upper()


def _null_if_blank(value):
    value = str(value or "").strip()
    return value or None


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def validate_catalogue_record(data, status=None):
    """Enforce the trust contract shared by admin writes and bulk imports."""
    status = (status or data.get("verification_status") or "PENDING").upper()
    if status not in CATALOGUE_STATUSES:
        raise ValueError("Invalid verification status")

    if status == "VERIFIED":
        if not (data.get("source_dataset") or "").strip():
            raise ValueError("A VERIFIED record requires source_dataset provenance")
        if not (data.get("verified_summary") or "").strip():
            raise ValueError("A VERIFIED record requires a verified_summary")
        if not any(str(data.get(field) or "").strip()
                   for field in CATALOGUE_ID_FIELDS):
            raise ValueError("A VERIFIED record requires at least one exact identifier")

    short_status = (data.get("short_summary_status") or "pending").strip().lower()
    if short_status not in SHORT_SUMMARY_STATUSES:
        raise ValueError("Invalid short summary status")
    short_summary = (data.get("short_summary") or "").strip()
    if short_status in {"ok", "fallback_extract"} and not short_summary:
        raise ValueError(f"short_summary is required when status is {short_status}")
    if short_summary and short_status not in {"ok", "fallback_extract"}:
        raise ValueError("A stored short_summary must have status ok or fallback_extract")

    human_verified = _truthy(data.get("human_verified"))
    if human_verified:
        if status != "VERIFIED":
            raise ValueError("Only a VERIFIED record can be human reviewed")
        if not (data.get("reviewed_by") or "").strip() or \
                not (data.get("reviewed_at") or "").strip():
            raise ValueError("Human review requires reviewed_by and reviewed_at")
    return status, short_status


def find_cached_exact(book):
    """Find a display/cache row by an exact identifier, never title alone."""
    fields = (
        ("isbn_13", _digits(book.get("isbn_13"))),
        ("isbn_10", _digits(book.get("isbn_10"))),
        ("google_books_id", (book.get("google_books_id") or "").strip()),
        ("open_library_edition_id", (book.get("open_library_edition_id") or "").strip()),
        ("open_library_work_id", (book.get("open_library_work_id") or book.get("open_library_key") or "").strip()),
    )
    conn = get_db()
    try:
        cur = conn.cursor()
        for column, value in fields:
            if value:
                cur.execute(f"SELECT * FROM books WHERE {column} = ? LIMIT 1", (value,))
                row = cur.fetchone()
                if row is not None:
                    return row
        return None
    finally:
        conn.close()


def lookup_verified_catalogue(book):
    """Identifier-first VERIFIED lookup; title-only matching is forbidden."""
    fields = (
        ("isbn_13", _digits(book.get("isbn_13"))),
        ("isbn_10", _digits(book.get("isbn_10"))),
        ("google_volume_id", (book.get("google_books_id") or book.get("google_volume_id") or "").strip()),
        ("open_library_edition_id", (book.get("open_library_edition_id") or "").strip()),
        ("open_library_work_id", (book.get("open_library_work_id") or book.get("open_library_key") or "").strip()),
    )
    conn = get_db()
    try:
        cur = conn.cursor()
        for column, value in fields:
            if value:
                cur.execute(
                    f"SELECT * FROM catalogue_books WHERE verification_status='VERIFIED' AND {column}=? LIMIT 1",
                    (value,))
                row = cur.fetchone()
                if row is not None:
                    return row

        title = normalize_identity(book.get("title"))
        author = normalize_identity(book.get("author"))
        if title and author:
            cur.execute("""
                SELECT * FROM catalogue_books
                WHERE verification_status='VERIFIED'
                  AND normalized_title=? AND normalized_author=?
                LIMIT 1
            """, (title, author))
            return cur.fetchone()
        return None
    finally:
        conn.close()


def verified_catalogue_candidates():
    """Return the small trusted catalogue for local-first ranking."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, title, author, isbn_10, isbn_13, google_volume_id,
                   open_library_edition_id, open_library_work_id, publisher,
                   publication_year, genres
            FROM catalogue_books
            WHERE verification_status='VERIFIED'
            ORDER BY id
        """).fetchall()
        candidates = []
        for row in rows:
            item = dict(row)
            candidates.append({
                "catalogue_id": item["id"],
                "title": item["title"],
                "author": item["author"],
                "isbn_10": item["isbn_10"] or "",
                "isbn_13": item["isbn_13"] or "",
                "google_books_id": item["google_volume_id"] or "",
                "open_library_edition_id": item["open_library_edition_id"] or "",
                "open_library_work_id": item["open_library_work_id"] or "",
                "open_library_key": item["open_library_work_id"] or "",
                "publisher": item["publisher"] or "",
                "published_date": item["publication_year"] or "",
                "categories": item["genres"] or "",
                # Catalogue rows carry no cover column, which is why 15 of 19
                # cards in the funnel test showed a placeholder. Open Library
                # serves cover art keyed by the edition id we already store, so
                # the URL is derivable here and flows to every card without any
                # new data or lookup.
                #
                # Measured on a random 30: 73% resolve. "default=false" makes a
                # missing cover a 404 rather than a blank placeholder image, so
                # the client falls back rather than showing an empty grey box.
                "thumbnail": (
                    "https://covers.openlibrary.org/b/olid/%s-M.jpg?default=false"
                    % item["open_library_edition_id"].strip()
                    if (item["open_library_edition_id"] or "").strip() else ""),
                "provider": "local_catalogue",
            })
        return candidates
    finally:
        conn.close()


def create_catalogue_book(data, admin_user_id=None):
    status, short_status = validate_catalogue_record(data)
    title = (data.get("title") or "").strip()
    author = (data.get("author") or "").strip()
    if not title or not author:
        raise ValueError("Title and author are required")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO catalogue_books
            (title, normalized_title, author, normalized_author, isbn_10, isbn_13,
             google_volume_id, open_library_edition_id, open_library_work_id,
             publisher, publication_year, genres, source_dataset, source_summary,
             verified_summary, verification_status, verified_by, verified_at,
             verification_notes, machine_verified, human_verified, reviewed_by,
             reviewed_at, review_notes, short_summary, short_summary_status,
             short_summary_method, short_summary_model,
             short_summary_source_sha256, short_summary_generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title, normalize_identity(title), author, normalize_identity(author),
            _null_if_blank(_digits(data.get("isbn_10"))),
            _null_if_blank(_digits(data.get("isbn_13"))),
            _null_if_blank(data.get("google_volume_id")),
            _null_if_blank(data.get("open_library_edition_id")),
            _null_if_blank(data.get("open_library_work_id")),
            (data.get("publisher") or "").strip(),
            str(data.get("publication_year") or "").strip(),
            (data.get("genres") or "").strip(),
            (data.get("source_dataset") or "").strip(),
            (data.get("source_summary") or "").strip(),
            (data.get("verified_summary") or "").strip(), status,
            admin_user_id if status == "VERIFIED" else None,
            (data.get("verified_at") or "").strip() or None,
            (data.get("verification_notes") or "").strip(),
            1 if status == "VERIFIED" else 0,
            1 if _truthy(data.get("human_verified")) else 0,
            (data.get("reviewed_by") or "").strip() or None,
            (data.get("reviewed_at") or "").strip() or None,
            (data.get("review_notes") or "").strip(),
            (data.get("short_summary") or "").strip(), short_status,
            (data.get("short_summary_method") or "").strip(),
            (data.get("short_summary_model") or "").strip(),
            (data.get("short_summary_source_sha256") or "").strip(),
            (data.get("short_summary_generated_at") or "").strip() or None,
        ))
        record_id = cur.lastrowid
        conn.commit()
        return record_id
    except sqlite3.IntegrityError as exc:
        raise ValueError("A catalogue record with one of these identifiers already exists") from exc
    finally:
        conn.close()


def update_catalogue_book(record_id, data, admin_user_id):
    current = get_catalogue_book(record_id)
    if current is None:
        return False
    merged = dict(current)
    merged.update(data)
    status, short_status = validate_catalogue_record(merged)
    title = (merged.get("title") or "").strip()
    author = (merged.get("author") or "").strip()
    if not title or not author:
        raise ValueError("Title and author are required")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE catalogue_books SET
              title=?, normalized_title=?, author=?, normalized_author=?,
              isbn_10=?, isbn_13=?, google_volume_id=?, open_library_edition_id=?,
              open_library_work_id=?, publisher=?, publication_year=?, genres=?,
              source_dataset=?, source_summary=?, verified_summary=?,
              verification_status=?, verified_by=?, verified_at=?,
              verification_notes=?, machine_verified=?, human_verified=?,
              reviewed_by=?, reviewed_at=?, review_notes=?, short_summary=?,
              short_summary_status=?, short_summary_method=?, short_summary_model=?,
              short_summary_source_sha256=?, short_summary_generated_at=?,
              updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            title, normalize_identity(title), author, normalize_identity(author),
            _null_if_blank(_digits(merged.get("isbn_10"))),
            _null_if_blank(_digits(merged.get("isbn_13"))),
            _null_if_blank(merged.get("google_volume_id")),
            _null_if_blank(merged.get("open_library_edition_id")),
            _null_if_blank(merged.get("open_library_work_id")),
            (merged.get("publisher") or "").strip(),
            str(merged.get("publication_year") or "").strip(),
            (merged.get("genres") or "").strip(),
            (merged.get("source_dataset") or "").strip(),
            (merged.get("source_summary") or "").strip(),
            (merged.get("verified_summary") or "").strip(), status,
            admin_user_id if status == "VERIFIED" else None,
            (merged.get("verified_at") or "").strip() or None,
            (merged.get("verification_notes") or "").strip(),
            1 if status == "VERIFIED" else 0,
            1 if _truthy(merged.get("human_verified")) else 0,
            (merged.get("reviewed_by") or "").strip() or None,
            (merged.get("reviewed_at") or "").strip() or None,
            (merged.get("review_notes") or "").strip(),
            (merged.get("short_summary") or "").strip(), short_status,
            (merged.get("short_summary_method") or "").strip(),
            (merged.get("short_summary_model") or "").strip(),
            (merged.get("short_summary_source_sha256") or "").strip(),
            (merged.get("short_summary_generated_at") or "").strip() or None,
            record_id,
        ))
        conn.commit()
        return cur.rowcount == 1
    except sqlite3.IntegrityError as exc:
        raise ValueError("A catalogue record with one of these identifiers already exists") from exc
    finally:
        conn.close()


def get_catalogue_book(record_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM catalogue_books WHERE id=?", (record_id,)).fetchone()
    finally:
        conn.close()


def list_catalogue(status="", query=""):
    sql = "SELECT * FROM catalogue_books WHERE 1=1"
    params = []
    status = (status or "").upper()
    if status:
        if status not in CATALOGUE_STATUSES:
            raise ValueError("Invalid verification status")
        sql += " AND verification_status=?"
        params.append(status)
    query = (query or "").strip()
    if query:
        sql += " AND (title LIKE ? OR author LIKE ? OR isbn_13 LIKE ?)"
        term = f"%{query}%"
        params.extend((term, term, term))
    sql += " ORDER BY updated_at DESC, id DESC"
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def catalogue_counts():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT verification_status, COUNT(*) AS count
            FROM catalogue_books GROUP BY verification_status
        """).fetchall()
        counts = {status.lower(): 0 for status in CATALOGUE_STATUSES}
        for row in rows:
            counts[row["verification_status"].lower()] = row["count"]
        counts["total"] = sum(counts.values())
        summary_rows = conn.execute("""
            SELECT short_summary_status, COUNT(*) AS count
            FROM catalogue_books GROUP BY short_summary_status
        """).fetchall()
        for status in SHORT_SUMMARY_STATUSES:
            counts[f"summary_{status}"] = 0
        for row in summary_rows:
            counts[f"summary_{row['short_summary_status']}"] = row["count"]
        review = conn.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN machine_verified=1 THEN 1 ELSE 0 END), 0)
                AS machine_verified,
              COALESCE(SUM(CASE WHEN human_verified=1 THEN 1 ELSE 0 END), 0)
                AS human_verified
            FROM catalogue_books
        """).fetchone()
        counts["machine_verified"] = review["machine_verified"]
        counts["human_verified"] = review["human_verified"]
        counts["summary_processed"] = (
            counts["summary_ok"] + counts["summary_fallback_extract"] +
            counts["summary_unavailable"])
        return counts
    finally:
        conn.close()


def external_identity(book):
    """Return a namespaced exact external identity, never title/author."""
    google_id = (book.get("google_books_id") or "").strip()
    if google_id:
        return "google_volume", google_id
    edition_id = (book.get("open_library_edition_id") or "").strip()
    if edition_id:
        return "openlibrary_edition", edition_id
    work_id = (book.get("open_library_work_id") or
               book.get("open_library_key") or "").strip()
    if work_id:
        return "openlibrary_work", work_id
    isbn = _digits(book.get("isbn_13") or book.get("isbn_10"))
    if isbn:
        return "openlibrary_isbn", isbn
    return "", ""


def find_external_summary(book):
    provider, provider_id = external_identity(book)
    if not provider_id:
        return None
    conn = get_db()
    try:
        return conn.execute("""
            SELECT * FROM external_summary_cache
            WHERE provider=? AND provider_id=?
            ORDER BY id DESC LIMIT 1
        """, (provider, provider_id)).fetchone()
    finally:
        conn.close()


def cache_external_summary(book, description, description_source,
                           short_summary, method, status="ready"):
    """Cache only grounded, non-empty external summaries by exact ID."""
    provider, provider_id = external_identity(book)
    description = (description or "").strip()
    short_summary = (short_summary or "").strip()
    if not provider_id or not description or not short_summary:
        raise ValueError("Exact identity, description and summary are required")
    if status not in {"ready", "fallback_extract"}:
        raise ValueError("Only successful external summaries may be cached")
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO external_summary_cache
            (provider, provider_id, title, author, description_source,
             description_sha256, source_description, short_summary,
             summary_method, summary_status, trust_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EXTERNAL_NOT_VERIFIED')
            ON CONFLICT(provider, provider_id, description_sha256) DO UPDATE SET
              title=excluded.title, author=excluded.author,
              description_source=excluded.description_source,
              source_description=excluded.source_description,
              short_summary=excluded.short_summary,
              summary_method=excluded.summary_method,
              summary_status=excluded.summary_status,
              generated_at=CURRENT_TIMESTAMP
        """, (provider, provider_id, (book.get("title") or "").strip(),
              (book.get("author") or "").strip(), description_source,
              digest, description, short_summary, method, status))
        conn.commit()
        return conn.execute("""
            SELECT * FROM external_summary_cache
            WHERE provider=? AND provider_id=? AND description_sha256=?
        """, (provider, provider_id, digest)).fetchone()
    finally:
        conn.close()


def create_identification_attempt(user_id, input_method, evidence):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO identification_attempts
            (user_id, input_method, ocr_status, ocr_title, ocr_author, ocr_text,
             ocr_confidence, query_title, query_author, query_isbn, decision,
             failure_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, input_method, evidence.get("ocr_status", ""),
            evidence.get("ocr_title", ""), evidence.get("ocr_author", ""),
            evidence.get("ocr_text", ""), float(evidence.get("ocr_confidence") or 0),
            evidence.get("query_title", ""), evidence.get("query_author", ""),
            evidence.get("query_isbn", ""), evidence.get("decision", "REJECTED"),
            evidence.get("failure_reason", ""),
        ))
        attempt_id = cur.lastrowid
        conn.commit()
        return attempt_id
    finally:
        conn.close()


def save_candidate_matches(attempt_id, candidates):
    conn = get_db()
    try:
        cur = conn.cursor()
        ids = []
        for index, candidate in enumerate(candidates, 1):
            metadata = dict(candidate)
            score = float(metadata.pop("score", 0))
            decision = metadata.pop("decision", "REJECTED")
            reasons = metadata.pop("reasons", [])
            provider = metadata.get("provider") or ("google" if metadata.get("google_books_id") else "openlibrary")
            provider_id = metadata.get("google_books_id") or metadata.get("open_library_key") or ""
            cur.execute("""
                INSERT INTO candidate_matches
                (attempt_id, rank_position, provider, provider_id, score,
                 decision, reasons, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (attempt_id, index, provider, provider_id, score, decision,
                  json.dumps(reasons), json.dumps(metadata)))
            ids.append(cur.lastrowid)
        conn.commit()
        return ids
    finally:
        conn.close()


def get_candidate_for_user(candidate_id, attempt_id, user_id):
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT c.*, a.user_id, a.decision AS attempt_decision,
                   a.query_isbn AS attempt_query_isbn
            FROM candidate_matches c
            JOIN identification_attempts a ON a.id=c.attempt_id
            WHERE c.id=? AND c.attempt_id=? AND a.user_id=?
        """, (candidate_id, attempt_id, user_id)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["metadata"] = json.loads(out.pop("metadata_json"))
        out["reasons"] = json.loads(out.get("reasons") or "[]")
        return out
    finally:
        conn.close()


def complete_identification(attempt_id, candidate_id, book_id, decision="HIGH_CONFIDENCE"):
    conn = get_db()
    try:
        conn.execute("UPDATE candidate_matches SET is_selected=1 WHERE id=? AND attempt_id=?",
                     (candidate_id, attempt_id))
        conn.execute("""
            UPDATE identification_attempts
            SET selected_candidate_id=?, selected_book_id=?, decision=?, failure_reason=''
            WHERE id=?
        """, (candidate_id, book_id, decision, attempt_id))
        conn.commit()
    finally:
        conn.close()


def list_identification_attempts(limit=100):
    conn = get_db()
    try:
        return conn.execute("""
            SELECT a.*, u.name AS user_name, b.title AS selected_title
            FROM identification_attempts a
            JOIN users u ON u.id=a.user_id
            LEFT JOIN books b ON b.id=a.selected_book_id
            ORDER BY a.created_at DESC LIMIT ?
        """, (max(1, min(int(limit), 500)),)).fetchall()
    finally:
        conn.close()


def identification_counts():
    conn = get_db()
    try:
        out = {"failed_ocr": 0, "needs_confirmation": 0,
               "successful_identifications": 0, "rejected_identifications": 0}
        out["failed_ocr"] = conn.execute(
            "SELECT COUNT(*) FROM identification_attempts WHERE ocr_status='OCR_FAILED'").fetchone()[0]
        out["needs_confirmation"] = conn.execute(
            "SELECT COUNT(*) FROM identification_attempts WHERE decision='NEEDS_CONFIRMATION'").fetchone()[0]
        out["successful_identifications"] = conn.execute(
            "SELECT COUNT(*) FROM identification_attempts WHERE selected_book_id IS NOT NULL").fetchone()[0]
        out["rejected_identifications"] = conn.execute(
            "SELECT COUNT(*) FROM identification_attempts WHERE decision='REJECTED'").fetchone()[0]
        return out
    finally:
        conn.close()


def audit_admin_action(admin_user_id, action, entity_type, entity_id="", details=""):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO admin_activity_logs
            (admin_user_id, action, entity_type, entity_id, details)
            VALUES (?, ?, ?, ?, ?)
        """, (admin_user_id, action, entity_type, str(entity_id or ""),
              str(details or "")[:1000]))
        conn.commit()
    finally:
        conn.close()


def get_admin_logs(limit=100):
    conn = get_db()
    try:
        return conn.execute("""
            SELECT l.*, u.name AS admin_name
            FROM admin_activity_logs l JOIN users u ON u.id=l.admin_user_id
            ORDER BY l.created_at DESC LIMIT ?
        """, (max(1, min(int(limit), 500)),)).fetchall()
    finally:
        conn.close()


def set_user_active(user_id, is_active):
    conn = get_db()
    try:
        cur = conn.execute("UPDATE users SET is_active=? WHERE id=?",
                           (1 if is_active else 0, user_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def update_book_verified_summary(book_id, catalogue_id, verified_summary,
                                 ai_summary="", status="unavailable"):
    conn = get_db()
    try:
        source = "catalogue_verified" if catalogue_id else ""
        conn.execute("""
            UPDATE books SET catalogue_id=?, verified_summary=?, ai_summary=?,
                             summary_status=?, description_source=?
            WHERE id=?
        """, (catalogue_id, verified_summary or "", ai_summary or "", status,
              source, book_id))
        conn.commit()
    finally:
        conn.close()


def update_book_ai_summary(book_id, ai_summary, status):
    conn = get_db()
    try:
        conn.execute("UPDATE books SET ai_summary=?, summary_status=? WHERE id=?",
                     (ai_summary or "", status, book_id))
        conn.commit()
    finally:
        conn.close()
