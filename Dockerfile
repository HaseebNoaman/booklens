# BookLens — deployable image.
#
# Two stages: node builds the React bundle, python runs the API and serves that
# bundle. Nothing from the research repo is present: no torch, no transformers,
# no FLAN checkpoint. See requirements.txt for why.

# ---------- stage 1: build the frontend ----------
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && corepack pnpm install --frozen-lockfile
COPY frontend/ ./
RUN corepack pnpm run build

# ---------- stage 2: the application ----------
FROM python:3.13-slim
WORKDIR /app

# No apt packages are needed.
#
# The GUI build of OpenCV is what pulls in libgl1 and libglib2.0-0, and those
# are system libraries you cannot install without root -- which is one of the
# reasons shared hosting cannot run this. requirements.txt uses the headless
# build instead: identical cv2 API minus the window functions, none of which
# this app or PaddleOCR calls. Smaller image, no system dependency.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installed as root, before the USER switch below: /usr/local/bin is root-owned.
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Run as a normal user, not root.
#
# This is not a hardening nicety, it is what makes the image start at all on
# Hugging Face Spaces, which runs every container as uid 1000. Built as root,
# /app and /data belong to root, so at runtime the entrypoint cannot create the
# database directory, its fallback directory is unwritable too, and `set -e`
# kills the container during boot -- before a single log line about the app.
# Everything below therefore belongs to this user, including HOME.
RUN useradd --create-home --uid 1000 booklens \
    && mkdir -p /data \
    && chown -R booklens:booklens /app /data
USER booklens
ENV HOME=/home/booklens

COPY --chown=booklens:booklens *.py ./
COPY --chown=booklens:booklens bookfinder.seed.db ./
COPY --from=frontend --chown=booklens:booklens /build/dist ./frontend/dist

# The verified shelf's own covers -- 60 files, 1.2 MB, committed by
# curate/fetch_covers.py and served by app.py's /covers/<id>.jpg route.
#
# Easy to leave out, and invisible until deployment: on a development machine
# the folder is simply there, so the route works. Inside an image built without
# this line every one of the 60 covers 404s, BookCover's chain falls through to
# covers.openlibrary.org, and the app is back to a request per book per page
# view to somebody else's server -- which is the exact thing committing the
# covers was meant to stop.
#
# It is NOT copied into frontend/dist: stage 1 rebuilds that directory from
# source and would discard anything placed there.
COPY --chown=booklens:booklens catalogue_covers/ ./catalogue_covers/

# Pre-bake the OCR weights into the IMAGE.
#
# This matters more than it looks. ocrpp.py builds the mobile reader at module
# level, and PaddleOCR downloads its weights from the internet on first use into
# ~/.paddlex. Without this step a fresh container fetches ~27 MB before it can
# answer at all -- and worse, the ESCALATION recogniser (PP-OCRv6_medium, 134 MB)
# loads lazily on the first scan that fails the first pass. That would put a
# 134 MB download in the middle of a live request, on precisely the refusal path
# that is the most important thing to demonstrate.
#
# Warming both readers here moves all of it to build time. Runtime is then
# offline for OCR.
#
# This runs AFTER the USER switch on purpose: PaddleOCR caches into $HOME, so
# baking as root would leave 134 MB in /root/.paddlex that uid 1000 cannot read,
# and the download would happen again on the first live scan anyway.
RUN python -c "import ocrpp; ocrpp._get_reader(ocrpp.OCR_DET_TIER, ocrpp.OCR_ESCALATE_REC_TIER)"

# The database lives here. Point BOOKLENS_DB_PATH at a mounted volume to make
# the library survive a redeploy; leave it and the seed catalogue is used fresh
# on every boot.
ENV BOOKLENS_DB_PATH=/data/bookfinder.db
ENV BOOKLENS_ENV=production
ENV HOST=0.0.0.0
VOLUME /data

ENTRYPOINT ["docker-entrypoint.sh"]

# NOT gunicorn. app.py creates the tables and the admin account inside its
# __main__ block, so importing it as a WSGI module (gunicorn app:app) starts a
# server with no schema and no admin, and every database request returns 500.
# Running it directly also uses the waitress server already configured there.
CMD ["python", "app.py"]
