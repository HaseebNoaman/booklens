---
title: BookLens
emoji: 📖
colorFrom: green
colorTo: gray
sdk: docker
app_port: 5000
pinned: false
---

# BookLens

Cover-first book identification that knows when to stay silent, plus a personal
library and an "is this for you" check.

This folder is the **deployable application only**. Experiments, evaluation
artefacts, the catalogue builder and the research documentation live in the
archive repo (`booklens_catalogue_improved`) and are not needed to run or ship
this.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cd frontend && corepack pnpm install --frozen-lockfile && corepack pnpm run build && cd ..
cp bookfinder.seed.db bookfinder.db
python app.py
```

Then open http://127.0.0.1:5000. Set `HOST=0.0.0.0` to reach it from a phone on
the same network — that is the core use case, photographing a cover.

## Run the tests

```bash
python -m pytest tests -q
```

## Deploy

The whole app is one process: Flask serves the API *and* the built React bundle,
so there is one deployment and one URL. No separate frontend host, no CORS.

### What the host must provide

| requirement | why |
|---|---|
| **1 GB RAM or more** | measured 712 MB with PaddleOCR loaded, and more once the escalation recogniser loads. A 512 MB tier is killed on the first scan. |
| **Long request timeout** | the slowest cover in a 20-book test took 43 s. Anything capping requests at 10-30 s will cut scans off. |
| **Start command `python app.py`** | **not gunicorn.** `init_db()` and `create_default_admin()` run inside app.py's `__main__` block, so importing it as a WSGI module gives a server with no tables and no admin, and every request 500s. |
| **`$PORT`** | read automatically; defaults to 5000 locally. |

Environment variables: `SECRET_KEY` and `ADMIN_PASSWORD` are mandatory when
`BOOKLENS_ENV=production` — the app refuses to start without them —
plus `GOOGLE_BOOKS_API_KEY` for books outside the local catalogue.

**Email is required for signup to work at all.** A new account stays unusable
until its address is confirmed, so a deployment with no mail configured can
only be signed into with accounts the server created itself.

| variable | why it matters |
|---|---|
| **`APP_BASE_URL`** | the origin the emailed links point at. **Set this, or every confirmation link points at `http://127.0.0.1:5000`** and no visitor can ever finish signing up. |
| `MAIL_PROVIDER` | `resend`, `brevo` or `smtp`. Prefer the HTTPS ones: free hosts commonly block outbound SMTP ports, and `smtplib` does not fail fast when they do -- it hangs until the socket times out. |
| `MAIL_API_KEY` | the provider's key. Not needed for `smtp`. |
| `MAIL_FROM` | for example `BookLens <onboarding@resend.dev>`. |

Leave `MAIL_PROVIDER` empty and the link is written to the server log instead of
being sent, which is what keeps the flow testable locally. The signup response
says which of the two happened rather than claiming success either way. Full
list in `.env.example`.

### Option A — a host that builds Python (Render, Railway, a VPS)

No Docker needed. `frontend/dist` is committed for exactly this case, so the
host never has to run pnpm.

- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`

**Rebuild the frontend before pushing any frontend change**, or the deployed
bundle will be the previous one:

```bash
cd frontend && corepack pnpm run build && cd ..
git add frontend/dist && git commit -m "Rebuild frontend"
```

### Option B — Docker

```bash
docker build -t booklens .
docker run -p 5000:5000 --env-file .env -v booklens-data:/data booklens
```

The image builds the frontend itself and pre-bakes the OCR weights, so the first
scan does not stall downloading them. Mount a volume at `/data` to keep the
library across restarts; without one the seed catalogue is reinstalled on every
boot and accounts are lost.

Some hosts build the Dockerfile for you, which is a way to use it without
installing Docker locally.

### After the first deploy

```bash
python seed_demo_account.py
```

Creates the clearly-labelled demo account and prints its password once.
