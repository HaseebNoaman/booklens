"""Create ONE clearly-labelled demo account with a small reading history.

Why this exists: "Is this for you?" is built from the signed-in user's own
finished and favourited books, so a fresh account correctly shows the cold-start
state. That is the honest behaviour and it stays. But a live demo needs an
account where the section actually renders, so this script makes one -- visibly
a demo, isolated from every other account.

    python seed_demo_account.py

Rules this script holds to:
  * It touches exactly one account, identified by DEMO_EMAIL below. It never
    modifies the admin account, and never any account a real person registered.
  * It refuses to run if that address already belongs to a non-demo user.
  * It is never called at startup. Seeding normal accounts would make the
    feature dishonest -- the profile is supposed to be evidence of what THIS
    user read.
  * It is idempotent: run it twice and the demo library is the same, not double.

The library spans several shelves on purpose. A profile that only knew thrillers
reported "no match" for almost anything an examiner picked up, which reads as a
broken feature rather than an honest one. It is still a real reader's library,
not a catch-all: Dune is present but only scanned, so it stays out of the
profile, and a book on a shelf nobody here reads still returns "no match".
"""
import os
import secrets
import sys

from werkzeug.security import generate_password_hash

import database

DEMO_EMAIL = "demo@booklens.local"
DEMO_NAME = "Demo Account (seeded)"

# title, author, subjects, reading_status, favourite
# A reader with range. The thriller/crime core is kept because it makes the
# clearest single demonstration, but a profile that only knows thrillers reports
# "no match" for almost anything an examiner picks off a shelf -- which looks
# like a broken feature rather than an honest one. The added shelves cover the
# genres people actually reach for: fantasy, science fiction, classics,
# literary/historical, and non-fiction.
DEMO_LIBRARY = [
    # --- psychological thriller and crime ---
    ("Gone Girl", "Gillian Flynn",
     "Psychological, Thrillers, Suspense", "finished", True),
    ("Sharp Objects", "Gillian Flynn",
     "Psychological, Thrillers, Crime", "finished", False),
    ("Before I Go to Sleep", "S. J. Watson",
     "Psychological, Thrillers, Memory", "finished", True),
    ("The Girl on the Train", "Paula Hawkins",
     "Psychological, Thrillers, Suspense", "finished", False),
    ("In Cold Blood", "Truman Capote",
     "Crime, True Crime, Journalism", "finished", False),
    ("The Secret History", "Donna Tartt",
     "Psychological, Crime, Campus", "reading", False),
    # --- fantasy and the fantastic ---
    ("The Hobbit", "J. R. R. Tolkien",
     "Fantasy, Adventure, Magic, Quests", "finished", True),
    ("The Ocean at the End of the Lane", "Neil Gaiman",
     "Fantasy, Magic, Horror", "finished", False),
    # --- science fiction ---
    ("Neuromancer", "William Gibson",
     "Science Fiction, Cyberpunk, Dystopian", "finished", False),
    ("The Left Hand of Darkness", "Ursula K. Le Guin",
     "Science Fiction, Speculative, Politics", "reading", False),
    # --- classics and literary/historical ---
    ("Wuthering Heights", "Emily Bronte",
     "Classics, Romance, Gothic", "finished", False),
    ("Beloved", "Toni Morrison",
     "Classics, Historical, Slavery", "finished", True),
    # --- non-fiction ---
    ("Atomic Habits", "James Clear",
     "Self-Help, Business & Economics, Motivational", "finished", False),
    ("Sapiens", "Yuval Noah Harari",
     "History, Anthropology, Civilization", "finished", False),
    # Deliberately left as a bare scan: proves that identifying a book does not
    # by itself put it in the profile. Do not "fix" this by marking it read.
    ("Dune", "Frank Herbert",
     "Science, Space Opera", "identified", False),
]


def seed():
    existing = database.get_user_by_email(DEMO_EMAIL)
    if existing is not None and not (existing["name"] or "").startswith("Demo Account"):
        print("REFUSING: %s already belongs to a non-demo user." % DEMO_EMAIL)
        return 1

    password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(9)
    if existing is None:
        database.create_user(DEMO_NAME, DEMO_EMAIL,
                             generate_password_hash(password), is_admin=0,
                             # There is no inbox behind demo@booklens.local and
                             # never will be, so the confirmation step that a
                             # real signup must pass is granted here instead.
                             email_verified=1)
        user = database.get_user_by_email(DEMO_EMAIL)
        print("Created demo account.")
        print("  email:    %s" % DEMO_EMAIL)
        print("  password: %s" % password)
        print("  Write this down -- it is not stored anywhere in readable form.")
    else:
        # Seeded before verification existed: grant it now rather than leave
        # the demo account unable to sign in.
        if not database.is_email_verified(existing):
            database.mark_email_verified(existing["id"])

        user = existing
        print("Demo account already exists; refreshing its library only.")
        print("  email: %s (password unchanged)" % DEMO_EMAIL)

    # Idempotent: clear only THIS user's history, then rebuild it.
    for row in database.get_user_history(user["id"]):
        database.delete_history_item(user["id"], row["history_id"])

    for title, author, subjects, status, favourite in DEMO_LIBRARY:
        book_id = database.save_book({
            "title": title, "author": author, "description": "",
            "ai_summary": "", "thumbnail": "", "page_count": 0,
            "publisher": "", "published_date": "", "categories": subjects,
            "confidence": "high"})
        history_id = database.save_history(user["id"], book_id)
        database.update_history_reading(user["id"], history_id, status, "")
        if favourite:
            database.toggle_favorite(user["id"], history_id)

    profile = database.get_taste_profile_books(user["id"])
    print("  %d books in the library, %d of them counting toward the profile."
          % (len(DEMO_LIBRARY), len(profile)))
    print("  'Dune' is present but only scanned, so it is correctly excluded.")
    return 0


if __name__ == "__main__":
    database.init_db()
    sys.exit(seed())
