#!/bin/sh
# Seed the database on first boot only.
#
# BOOKLENS_DB_PATH may point at a mounted volume that persists across deploys.
# If it is empty -- first ever boot, or an ephemeral filesystem -- install the
# seed catalogue; if a database is already there, leave it alone, because
# overwriting it would delete every user's library.
set -e

DB_PATH="${BOOKLENS_DB_PATH:-/data/bookfinder.db}"
DB_DIR="$(dirname "$DB_PATH")"

# Not every host lets the container write to /data. Free tiers without a
# mounted volume often do not, and the app must still start there rather than
# dying at boot with a permission error. Fall back to a directory we know we
# own; the database is then ephemeral, which is the honest outcome on a host
# that gave us nowhere durable to put it.
if ! mkdir -p "$DB_DIR" 2>/dev/null || [ ! -w "$DB_DIR" ]; then
    DB_DIR="/app/data"
    DB_PATH="$DB_DIR/bookfinder.db"
    mkdir -p "$DB_DIR"
    echo "WARNING: the configured database directory is not writable."
    echo "         Falling back to $DB_PATH, which does NOT survive a restart."
    export BOOKLENS_DB_PATH="$DB_PATH"
fi

if [ ! -f "$DB_PATH" ]; then
    echo "No database at $DB_PATH -- installing the 250-book seed catalogue."
    cp /app/bookfinder.seed.db "$DB_PATH"
else
    echo "Using the existing database at $DB_PATH."
fi

exec "$@"
