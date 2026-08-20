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

COPY *.py ./
COPY bookfinder.seed.db ./
COPY --from=frontend /build/dist ./frontend/dist

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
RUN python -c "import ocrpp; ocrpp._get_reader(ocrpp.OCR_DET_TIER, ocrpp.OCR_ESCALATE_REC_TIER)"

# The database lives here. Point BOOKLENS_DB_PATH at a mounted volume to make
# the library survive a redeploy; leave it and the seed catalogue is used fresh
# on every boot.
ENV BOOKLENS_DB_PATH=/data/bookfinder.db
ENV BOOKLENS_ENV=production
ENV HOST=0.0.0.0
VOLUME /data

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

# NOT gunicorn. app.py creates the tables and the admin account inside its
# __main__ block, so importing it as a WSGI module (gunicorn app:app) starts a
# server with no schema and no admin, and every database request returns 500.
# Running it directly also uses the waitress server already configured there.
CMD ["python", "app.py"]
