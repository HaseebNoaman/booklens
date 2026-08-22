"""Rebuild the database that actually ships.

bookfinder.seed.db is the catalogue a fresh install gets: the Dockerfile copies
it, docker-entrypoint.sh installs it when the volume is empty, and the README
tells a reader to copy it. bookfinder.db -- the one this machine develops
against -- is gitignored, because it holds real accounts.

So curation that only reaches bookfinder.db reaches nobody. Every hand-picked
description, every re-tagged genre and the whole 250-to-60 selection lived on
one laptop until this script existed, and a `docker build` would have shipped
the old shelf with its model-written summaries.

    python curate/build_seed.py            # compare the two
    python curate/build_seed.py --confirm  # rebuild the seed

Only catalogue_books is copied. Not users, not history, not the scan audit
trail -- the seed must be safe to commit, and the live database is not.
"""
import argparse
import io
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import database                                    # noqa: E402

LIVE = os.path.join(APP, "bookfinder.db")
SEED = os.path.join(APP, "bookfinder.seed.db")
# Everything a reader or a scan produces. None of it belongs in a shipped file.
PRIVATE = ("users", "history", "books", "messages", "auth_tokens",
           "identification_attempts", "candidate_matches",
           "admin_activity_logs", "live_signals", "external_summary_cache")


def summarise(path, label):
    if not os.path.exists(path):
        print("%-14s missing" % label)
        return
    conn = sqlite3.connect(path)
    try:
        verified = conn.execute(
            "SELECT COUNT(*) FROM catalogue_books "
            "WHERE verification_status='VERIFIED'").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM catalogue_books").fetchone()[0]
        model = conn.execute(
            "SELECT COUNT(*) FROM catalogue_books "
            "WHERE verification_status='VERIFIED' "
            "AND short_summary_method='ai_model'").fetchone()[0]
        leaked = []
        for table in PRIVATE:
            try:
                rows = conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if rows:
                leaked.append("%s=%d" % (table, rows))
        print("%-14s %d verified of %d rows, %d still model-written%s"
              % (label, verified, total, model,
                 ", PRIVATE DATA: " + ", ".join(leaked) if leaked else ""))
        return verified, model, leaked
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    print("before:")
    summarise(LIVE, "  live db")
    summarise(SEED, "  shipped")

    if not args.confirm:
        print("\nDry run. Add --confirm to rebuild the seed from the live catalogue.")
        return

    handle, temporary = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.remove(temporary)
    previous = database.DB_NAME
    try:
        database.DB_NAME = temporary
        database.init_db()                     # schema only, every table empty
    finally:
        database.DB_NAME = previous

    source = sqlite3.connect(LIVE)
    target = sqlite3.connect(temporary)
    try:
        source.row_factory = sqlite3.Row
        rows = [dict(r) for r in source.execute("SELECT * FROM catalogue_books")]
        if rows:
            columns = list(rows[0].keys())
            target.executemany(
                "INSERT INTO catalogue_books (%s) VALUES (%s)"
                % (",".join('"%s"' % c for c in columns),
                   ",".join("?" * len(columns))),
                [tuple(row[c] for c in columns) for row in rows])
            target.commit()
    finally:
        source.close()
        target.close()

    shutil.move(temporary, SEED)
    print("\nafter:")
    summarise(SEED, "  shipped")


if __name__ == "__main__":
    main()
