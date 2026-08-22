# app.py
# This is the MAIN file. It runs the Flask web server and holds all the
# API routes (URLs) the frontend talks to. It ties together the other files:
#   ocrpp.py       -> read text from the image
#   api.py         -> get book details from the internet
#   whatitsabout_heuristic.py -> select grounded external overview text
#   database.py    -> save and read users / books / history

from flask import (Flask, request, jsonify, make_response,
                   redirect, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from dotenv import load_dotenv
import jwt
import datetime
import logging
import os
import queue
import re
import secrets
import threading
import time
import uuid

# Some modules read configuration at import time, so load the project .env
# before importing them. This also makes startup independent of the shell's
# current working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from PIL import Image, UnidentifiedImageError

from ocrpp import (process_book_cover, OCR_REC_TIER, OCR_ESCALATE_REC_TIER)
from barcode_reader import read_isbn
from api import retrieve_ranked_candidates, hydrate_exact_candidate
from matching import (valid_isbn, normalize_isbn, normalize_match_text,
                      rank_candidates, recover_ocr_candidates,
                      UNSAFE_EDITION_RE,
                      HIGH_CONFIDENCE, NEEDS_CONFIRMATION, REJECTED)
from result_content import language_for_client
import taste_profile
import livesignals
from whatitsabout_heuristic import (METHOD as EXTERNAL_OVERVIEW_METHOD,
                                    build_external_overview)
from thefuzz import fuzz

from database import (CACHE_AUTHOR_MATCH, backfill_book_thumbnail,
                      prior_engagement, catalogue_subject_counts,
                      catalogue_subject_vocabulary,
                      get_taste_profile_books, get_user_interests,
                      set_user_interests,
                      init_db, create_user, get_user_by_email, get_user_by_id,
                      save_book, save_history, get_user_history,
                      toggle_favorite, update_history_reading,
                      delete_history_item, count_user_history,
                      update_user_password,
                      revoke_user_tokens,
                      get_book_by_id, update_book_description,
                      update_book_description_source,
                      get_admin_stats, get_all_users, get_all_books,
                      get_recent_scans, delete_book, delete_user,
                      save_message, get_all_messages, delete_message,
                      find_cached_exact, lookup_verified_catalogue,
                      verified_catalogue_candidates,
                      create_catalogue_book, update_catalogue_book,
                      get_catalogue_book, list_catalogue, catalogue_counts,
                      external_identity, find_external_summary,
                      cache_external_summary,
                      create_identification_attempt, save_candidate_matches,
                      get_candidate_for_user, complete_identification,
                      list_identification_attempts, identification_counts,
                      audit_admin_action, get_admin_logs, set_user_active,
                      update_book_verified_summary, update_book_ai_summary,
                      create_auth_token, consume_auth_token,
                      mark_email_verified, is_email_verified)
import mailer

# ----- Logging -----
# logging is the grown-up version of print(): every line gets a timestamp
# and a level (INFO / WARNING / ERROR), so real problems stand out in the
# server output and can later be redirected to a file.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# ----- App setup -----
app = Flask(__name__, static_folder=None)
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")
# The verified shelf's covers, committed to the repository. Not under
# frontend/dist, which the Docker build regenerates from source.
CATALOGUE_COVERS = os.path.join(BASE_DIR, "catalogue_covers")

UPLOAD_FOLDER = (os.environ.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "uploads"))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)   # make the uploads folder if missing

app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp"}
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", "24000000"))

def get_or_create_secret(var_name, generated_value):
    # Read a secret from the environment (.env). If it does not exist yet,
    # save the freshly generated one INTO .env so it stays the SAME after
    # every restart. Why that matters: login tokens are signed with
    # SECRET_KEY — if the key changed on restart, every user's login
    # would suddenly stop working.
    value = os.environ.get(var_name)
    if not value:
        if os.environ.get("BOOKLENS_ENV", "development").lower() == "production":
            raise RuntimeError(f"{var_name} must be set in production")
        value = generated_value
        with open(os.path.join(BASE_DIR, ".env"), "a", encoding="utf-8") as f:
            f.write(f"{var_name}={value}\n")
        os.environ[var_name] = value
        logging.warning("%s was missing -> generated one and saved it to .env", var_name)
    return value


# Secret key used to sign login tokens (JWT).
# SECURITY: never hardcoded — a hardcoded secret would let an attacker
# forge valid login tokens for any user.
SECRET_KEY = get_or_create_secret("SECRET_KEY", secrets.token_hex(32))

# Simple email format check: something@something.something
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ----- Login brute-force protection -----
# We remember failed login attempts per email in a dictionary.
# After 5 wrong passwords, that email is locked for 5 minutes.
# This stops attackers from guessing passwords thousands of times.
failed_logins = {}          # email -> {"count": int, "locked_until": timestamp}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300       # 5 minutes


def is_locked_out(email):
    record = failed_logins.get(email)
    if record and record["count"] >= MAX_ATTEMPTS:
        if time.time() < record["locked_until"]:
            return True
        # Lock expired -> reset the counter.
        del failed_logins[email]
    return False


def record_failed_login(email):
    # First remove expired entries so this dictionary cannot grow forever
    # (otherwise an attacker could fill our memory with millions of fake emails).
    now = time.time()
    expired = [e for e, r in failed_logins.items() if now > r["locked_until"]]
    for e in expired:
        del failed_logins[e]

    record = failed_logins.get(email, {"count": 0, "locked_until": 0})
    record["count"] += 1
    record["locked_until"] = now + LOCKOUT_SECONDS
    failed_logins[email] = record


def allowed_file(filename):
    # Only allow image files we listed above.
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_saved_image(filepath, declared_mime=""):
    """Verify content, not just the user-controlled filename."""
    if declared_mime and declared_mime.lower() not in ALLOWED_MIMES:
        return "Only JPG, PNG, and WebP images are allowed."
    try:
        with Image.open(filepath) as image:
            image.verify()
        with Image.open(filepath) as image:
            fmt = (image.format or "").upper()
            width, height = image.size
            if fmt not in {"JPEG", "PNG", "WEBP"}:
                return "The uploaded file is not a supported image."
            if width < 40 or height < 40:
                return "The image is too small to read."
            if width * height > MAX_IMAGE_PIXELS:
                return "The image dimensions are too large."
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return "The uploaded file is not a readable image."
    return ""


# ----- CORS -----
# The frontend (index.html) runs on a different origin than this server, so
# the browser needs these headers. We reflect the origin only when it is on
# our allowlist instead of answering "*" to everyone — auth is a bearer
# token (not cookies) so the practical risk of "*" was low, but an
# allowlist is defense-in-depth and costs nothing. Allowed by default:
# same-machine dev origins and private-LAN origins on the frontend port
# (so a phone on the same wifi can still use the app); extra origins can
# be added via the ALLOWED_ORIGINS env var (comma-separated).
_extra_origins = {o.strip() for o in
                  os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
_local_origins = {"http://localhost:8080", "http://127.0.0.1:8080"}
_allow_private_lan = (os.environ.get("ALLOW_PRIVATE_LAN", "1") == "1" and
                      os.environ.get("BOOKLENS_ENV", "development").lower() != "production")
_PRIVATE_LAN_RE = re.compile(
    r"^http://(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
    r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):8080$")


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if (origin in _local_origins or origin in _extra_origins
            or (_allow_private_lan and _PRIVATE_LAN_RE.match(origin))):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers.add("Vary", "Origin")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    if request.path.startswith("/api/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'")
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
            "form-action 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: https:; connect-src 'self'")
    if (os.environ.get("BOOKLENS_ENV", "development").lower() == "production" and
            os.environ.get("BOOKLENS_HTTPS", "0") == "1"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/") and request.path != "/api/health":
        response.headers["Cache-Control"] = "no-store"
    return response


# Browsers send an OPTIONS request before some requests; answer it with 200 OK.
@app.route("/api/<path:path>", methods=["OPTIONS"])
def handle_options(path):
    return make_response("", 200)


# ----- Error handlers -----
# By default Flask returns HTML error pages. Our frontend expects JSON,
# so we replace the three errors users can actually hit with JSON versions.

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed for this endpoint"}), 405


@app.errorhandler(413)
def file_too_large(e):
    # Raised automatically by Flask when an upload exceeds MAX_CONTENT_LENGTH.
    max_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"code": "upload_too_large",
                    "error": f"Image is too large. Maximum size is {max_mb} MB."}), 413


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ----- Rate limiting -----
# Small in-memory sliding-window limiter (no extra dependency). Protects the
# two abuse-prone endpoints: login (password brute force) and scan (each scan
# costs OCR + external API calls + model inference). Per-IP; counters reset
# when the window slides. In-memory is the right scope for a single-process
# deployment — a multi-worker deployment would move this to Redis (see
# ARCHITECTURE.md).
_rate_buckets = {}


def rate_limited(max_calls, window_seconds):
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # The pytest suite fires many requests from one client on
            # purpose; rate limiting there would test the limiter, not the
            # endpoints. It has its own dedicated test instead.
            if app.config.get("TESTING"):
                return f(*args, **kwargs)
            if os.environ.get("TRUST_PROXY", "0") == "1":
                ip = request.headers.get("X-Forwarded-For",
                                         request.remote_addr or "?").split(",")[0].strip()
            else:
                ip = request.remote_addr or "?"
            key = (f.__name__, ip)
            now = time.time()
            calls = [t for t in _rate_buckets.get(key, []) if now - t < window_seconds]
            if len(calls) >= max_calls:
                retry_after = max(1, int(window_seconds - (now - min(calls))) + 1)
                response = jsonify({
                    "code": "rate_limited",
                    "error": "Too many requests. Please wait a moment and try again."
                })
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                return response
            calls.append(now)
            _rate_buckets[key] = calls
            return f(*args, **kwargs)
        return decorated
    return wrapper


# ----- Login protection -----
# This is a "decorator". Put @token_required above a route and that route
# will only run if the user sent a valid login token.
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"error": "Token is missing"}), 401

        try:
            # Decode the token to find out which user it belongs to.
            data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            current_user = get_user_by_id(data["user_id"])
            if current_user is None:
                return jsonify({"code": "unauthorized", "error": "Session is no longer valid"}), 401
            if "is_active" in current_user.keys() and not current_user["is_active"]:
                return jsonify({"error": "This account is inactive"}), 403
            token_version = int(data.get("auth_version", -1))
            current_version = int(current_user["auth_version"] or 0)
            if token_version != current_version:
                return jsonify({"code": "session_ended", "error": "Session is no longer valid"}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({"code": "session_expired", "error": "Session expired. Please sign in again."}), 401
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return jsonify({"code": "unauthorized", "error": "Session is no longer valid"}), 401

        # Pass the logged-in user to the route as the first argument.
        return f(current_user, *args, **kwargs)

    return decorated


# Put this UNDER @token_required on a route to make it admin-only.
# It checks the is_admin flag of the already-verified user.
def admin_required(f):
    @wraps(f)
    def decorated(current_user, *args, **kwargs):
        if not current_user["is_admin"]:
            return jsonify({"error": "Admin access required"}), 403
        return f(current_user, *args, **kwargs)
    return decorated


# ----- Public routes -----

@app.route("/", methods=["GET"])
def frontend_index():
    index_path = os.path.join(FRONTEND_DIST, "index.html")
    if not os.path.isfile(index_path):
        return jsonify({
            "error": "Frontend build not found. Run the frontend build command first."
        }), 503
    return send_from_directory(FRONTEND_DIST, "index.html")


# The two pages an email link can land on. They serve the same single-page
# app as "/" and let the client read its own query string. These are explicit
# routes rather than a catch-all: a catch-all would swallow unknown /api/*
# paths and answer them with HTML instead of the JSON 404 the frontend expects.
@app.route("/verify", methods=["GET"])
@app.route("/reset", methods=["GET"])
def frontend_email_landing():
    return frontend_index()


@app.route("/assets/<path:filename>", methods=["GET"])
def frontend_asset(filename):
    # send_from_directory keeps asset resolution inside dist/assets and blocks
    # path traversal. Vite fingerprints these files during the build.
    return send_from_directory(os.path.join(FRONTEND_DIST, "assets"), filename)


@app.route("/covers/<int:record_id>.jpg", methods=["GET"])
def catalogue_cover_image(record_id):
    """The verified shelf's own covers, served from this repository.

    Every card, Browse row and starter-shelf tile used to fetch its cover from
    covers.openlibrary.org while rendering -- a request per book per page view
    to somebody else's server, for images that never change. The verified shelf
    is small and fixed, so its covers are downloaded once by
    curate/fetch_covers.py and committed: 60 files, 1.1 MB.

    They live outside frontend/dist on purpose. The Docker build rebuilds the
    frontend from source, which would delete anything kept in there.

    An int converter, not a path one: this route takes a catalogue id and
    nothing else, so no filename from a request ever reaches the filesystem.
    """
    return send_from_directory(CATALOGUE_COVERS, "%d.jpg" % record_id,
                               max_age=60 * 60 * 24 * 30)

@app.route("/api/health", methods=["GET"])
def health():
    # Simple check to see if the server is alive.
    return jsonify({"status": "running"})


# ----- Account verification -----
# How long a link stays usable. Verification is generous because people read
# email hours later; a reset is deliberately tight because it hands over an
# account.
VERIFY_TTL_SECONDS = 24 * 60 * 60
RESET_TTL_SECONDS = 60 * 60

# One sentence, used for every registration outcome. It is worded so that it
# is equally true whether the address was new, already waiting to be
# confirmed, or already had a working account -- which is what stops the
# response from revealing which of those it was.
REGISTERED_MESSAGE = ("If that address can receive mail, a confirmation link "
                      "is on its way. Open it to finish setting up your "
                      "account.")


def registration_response(delivery):
    """The single answer /api/register gives, whatever happened underneath.

    `delivery` describes the MAIL SYSTEM, never the address: a provider outage
    looks the same for an address that exists and one that does not, so
    reporting it honestly leaks nothing.
    """
    if delivery == "failed":
        return jsonify({
            "code": "mail_unavailable",
            "error": ("We could not send the confirmation email just now. "
                      "Your account is saved -- please request the link again "
                      "in a moment.")
        }), 503
    return jsonify({
        "message": REGISTERED_MESSAGE,
        "status": "pending_verification",
        # "logged" means no mail provider is configured and the link went to
        # the server log instead. The frontend shows a developer hint for it,
        # because silently pretending an email was sent is how a broken signup
        # goes unnoticed for a week.
        "delivery": delivery,
    }), 202


def issue_verification(user):
    """Create a fresh verification link for one user and try to send it."""
    if user is None:
        return "failed"
    token = secrets.token_urlsafe(32)
    create_auth_token(user["id"], "verify", token, VERIFY_TTL_SECONDS)
    link = "%s/api/verify-email?token=%s" % (mailer.base_url(), token)
    subject, body = mailer.verification_email(user["name"], link)
    return mailer.send_mail(user["email"], subject, body)


def issue_password_reset(user):
    if user is None:
        return "failed"
    token = secrets.token_urlsafe(32)
    create_auth_token(user["id"], "reset", token, RESET_TTL_SECONDS)
    link = "%s/reset?token=%s" % (mailer.base_url(), token)
    subject, body = mailer.reset_email(user["name"], link)
    return mailer.send_mail(user["email"], subject, body)


@app.route("/api/register", methods=["POST"])
@rate_limited(5, 60)     # 5 sign-ups per IP per minute is plenty for humans
def register():
    # silent=True means bad/missing JSON gives None instead of crashing.
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid request body"}), 400

    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    # All three fields are required.
    if not name or not email or not password:
        return jsonify({"error": "Name, email and password are required"}), 400

    # Validate the email format so junk like "abc" cannot register.
    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    # Length rules: passwords too short are weak; absurdly long inputs
    # are a sign of abuse, so we cap everything at sensible sizes.
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if len(name) > 100 or len(email) > 200 or len(password) > 200:
        return jsonify({"error": "Input too long"}), 400

    # Never store the real password; Werkzeug creates a salted one-way hash.
    password_hash = generate_password_hash(password)

    # WHY THIS ROUTE NEVER SAYS "email already registered".
    # It used to answer 409 with exactly that, which let anyone test an address
    # and learn whether it had an account here -- the very thing /api/login
    # goes out of its way not to reveal. Every path below now ends in the same
    # response, and the address owner is the only one who learns anything,
    # because what they learn arrives in their inbox.
    existing = get_user_by_email(email)
    if existing is not None:
        if is_email_verified(existing):
            # Someone tried to sign up with an address that already works.
            # Telling the owner is useful; telling the sender is not.
            subject, body = mailer.existing_account_email(existing["name"])
            delivery = mailer.send_mail(email, subject, body)
        else:
            delivery = issue_verification(existing)
        return registration_response(delivery)

    user_id = create_user(name, email, password_hash)
    if user_id is None:
        # Lost a race against a concurrent signup for the same address.
        # Same answer as every other branch.
        return registration_response(mailer.would_send())

    delivery = issue_verification(get_user_by_id(user_id))
    return registration_response(delivery)


@app.route("/api/login", methods=["POST"])
@rate_limited(8, 60)     # blocks password brute-forcing; humans never hit this
def login():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid request body"}), 400

    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    # Brute-force protection: locked out after too many wrong passwords.
    if is_locked_out(email):
        return jsonify({"error": "Too many failed attempts. Try again in 5 minutes."}), 429

    user = get_user_by_email(email)
    # SECURITY NOTE: we give the SAME error for wrong email and wrong
    # password, so an attacker cannot find out which emails are registered.
    if user is None:
        record_failed_login(email)
        return jsonify({"error": "Invalid email or password"}), 401

    if "is_active" in user.keys() and not user["is_active"]:
        return jsonify({"error": "Invalid email or password"}), 401

    # Compare the typed password against the stored hash.
    if not check_password_hash(user["password_hash"], password):
        record_failed_login(email)
        return jsonify({"error": "Invalid email or password"}), 401

    # Only now -- after the password has been proved -- is it safe to say
    # anything about the state of this account. Checking verification before
    # the password would tell an attacker the address exists.
    if not is_email_verified(user):
        return jsonify({
            "code": "email_unverified",
            "error": "Confirm your email address before signing in."
        }), 403

    # Successful login -> clear any failed-attempt record.
    failed_logins.pop(email, None)

    # Build a token that proves who this user is. It expires in 24 hours.
    # iat = "issued at", exp = "expires" (both standard JWT claims).
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode(
        {
            "user_id": user["id"],
            "auth_version": int(user["auth_version"] or 0),
            "iat": now,
            "exp": now + datetime.timedelta(
                hours=max(1, min(int(os.environ.get("JWT_EXPIRY_HOURS", "24")), 168)))
        },
        SECRET_KEY,
        algorithm="HS256"   # note: a STRING when encoding
    )

    return jsonify({
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "is_admin": user["is_admin"]
        }
    })


@app.route("/api/logout", methods=["POST"])
@token_required
def logout(current_user):
    # Incrementing auth_version invalidates this token (and any other token
    # issued for the account) without maintaining a complicated session table.
    revoke_user_tokens(current_user["id"])
    return jsonify({"message": "Signed out successfully"})


@app.route("/api/verify-email", methods=["GET"])
def verify_email():
    # The link in the email lands HERE, not on a frontend route, and then
    # redirects. That is deliberate: this app serves only "/" and "/assets",
    # with a 404 handler that answers JSON, so a link straight to a client-side
    # path would have shown the user a raw JSON error.
    user_id = consume_auth_token(request.args.get("token", ""), "verify")
    if user_id is None:
        return redirect("/verify?state=invalid")
    mark_email_verified(user_id)
    return redirect("/verify?state=ok")


@app.route("/api/resend-verification", methods=["POST"])
@rate_limited(3, 900)
def resend_verification():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    user = get_user_by_email(email) if EMAIL_REGEX.match(email) else None
    delivery = mailer.would_send()
    if user is not None and not is_email_verified(user):
        delivery = issue_verification(user)
    # Same answer for "no such address", "already verified" and "link resent".
    return registration_response(delivery)


@app.route("/api/forgot-password", methods=["POST"])
@rate_limited(3, 900)
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    user = get_user_by_email(email) if EMAIL_REGEX.match(email) else None
    delivery = mailer.would_send()
    if user is not None:
        delivery = issue_password_reset(user)
    if delivery == "failed":
        return jsonify({
            "code": "mail_unavailable",
            "error": "We could not send the reset email just now. Please try again shortly."
        }), 503
    return jsonify({
        "message": ("If that address has an account, a reset link is on its "
                    "way. It expires in an hour."),
        "delivery": delivery,
    }), 202


@app.route("/api/reset-password", methods=["POST"])
@rate_limited(8, 900)
def reset_password():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid request body"}), 400
    token = str(data.get("token", ""))
    password = str(data.get("password", ""))
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if len(password) > 200:
        return jsonify({"error": "Input too long"}), 400

    user_id = consume_auth_token(token, "reset")
    if user_id is None:
        return jsonify({
            "code": "reset_link_invalid",
            "error": "That link has expired or has already been used. Please request a new one."
        }), 400

    update_user_password(user_id, generate_password_hash(password))
    # Anyone holding an old token for this account loses it. Whoever asked for
    # the reset may well be locking somebody else out on purpose.
    revoke_user_tokens(user_id)
    # Completing a reset proves the address receives mail, which is the same
    # thing verification proves -- so an account that reset its password is
    # verified by that act.
    mark_email_verified(user_id)
    return jsonify({"message": "Password updated. Please sign in with your new password."})


# Remembers when each IP address last sent a contact message.
# Allows max 1 message per minute per IP, so a script cannot flood our inbox.
last_contact_time = {}


@app.route("/api/contact", methods=["POST"])
def contact():
    # Public route: anyone can send a message from the Contact Us page.
    ip = request.remote_addr or "unknown"
    if time.time() - last_contact_time.get(ip, 0) < 60:
        return jsonify({"error": "Please wait a minute before sending another message"}), 429

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid request body"}), 400

    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    subject = str(data.get("subject", "")).strip()
    message = str(data.get("message", "")).strip()

    if not name or not email or not message:
        return jsonify({"error": "Name, email and message are required"}), 400

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    # Cap the sizes so nobody can fill our database with megabytes of junk.
    if len(name) > 100 or len(subject) > 200 or len(message) > 2000:
        return jsonify({"error": "Input too long"}), 400

    save_message(name, email, subject, message)
    last_contact_time[ip] = time.time()
    return jsonify({"message": "Thank you! Your message has been received."}), 201


# ----- Protected routes -----

@app.route("/api/profile", methods=["GET"])
@token_required
def profile(current_user):
    return jsonify({
        "id": current_user["id"],
        "name": current_user["name"],
        "email": current_user["email"],
        "created_at": current_user["created_at"],
        "scan_count": count_user_history(current_user["id"]),
        "interests": get_user_interests(current_user["id"]),
    })


@app.route("/api/interests", methods=["GET"])
@token_required
def available_interests(current_user):
    """Subjects a reader may choose, drawn from the catalogue's own shelves."""
    return jsonify({
        "available": catalogue_subject_vocabulary(),
        "chosen": [s.strip() for s in
                   (get_user_interests(current_user["id"]) or "").split(",")
                   if s.strip()],
    })


@app.route("/api/profile/interests", methods=["POST"])
@token_required
def update_interests(current_user):
    """Save the reader's chosen interests. Editable at any time, and clearable.

    These are a COLD-START signal only. Once the reader has books of their own,
    taste_profile prefers those and these fall silent -- so a stale choice made
    at signup cannot keep speaking over real reading.
    """
    data = request.get_json(silent=True) or {}
    chosen = data.get("interests", [])
    if not isinstance(chosen, list):
        return jsonify({"error": "interests must be a list"}), 400
    if len(chosen) > 8:
        return jsonify({"error": "Choose up to 8 interests"}), 400

    # Only subjects the catalogue actually uses. This keeps the field from
    # becoming free-text storage and guarantees a choice can match something.
    allowed = {s.lower(): s for s in catalogue_subject_vocabulary()}
    cleaned = []
    for item in chosen:
        label = allowed.get(str(item).strip().lower())
        if label and label not in cleaned:
            cleaned.append(label)
    set_user_interests(current_user["id"], ", ".join(cleaned))
    return jsonify({"interests": cleaned})


@app.route("/api/profile/password", methods=["POST"])
@token_required
def change_password(current_user):
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid request body"}), 400

    current_password = str(data.get("current_password", ""))
    new_password = str(data.get("new_password", ""))

    # SECURITY: ask for the CURRENT password again. A valid token alone is
    # not enough — if someone grabbed an unlocked laptop, they still could
    # not change the password and lock the real owner out.
    # We answer 403 (not 401): in this app 401 always means "your login
    # token is bad", which makes the frontend log the user out.
    if not check_password_hash(current_user["password_hash"], current_password):
        return jsonify({"error": "Current password is incorrect"}), 403

    # Same strength rules as registration.
    if len(new_password) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400
    if len(new_password) > 200:
        return jsonify({"error": "Input too long"}), 400

    update_user_password(current_user["id"], generate_password_hash(new_password))
    revoke_user_tokens(current_user["id"])
    return jsonify({"message": "Password changed. Please sign in again.",
                    "reauthenticate": True})


@app.route("/api/profile", methods=["DELETE"])
@token_required
def delete_account(current_user):
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid request body"}), 400

    # Deleting an account is permanent, so we ask for the password again.
    # 403 (not 401) for the same reason as in change_password above.
    password = str(data.get("password", ""))
    if not check_password_hash(current_user["password_hash"], password):
        return jsonify({"error": "Password is incorrect"}), 403

    # Admin accounts are managed in the dashboard; deleting the only admin
    # here would lock everyone out of it (same rule as the dashboard).
    if current_user["is_admin"]:
        return jsonify({"error": "Admin accounts cannot be deleted here"}), 400

    delete_user(current_user["id"])   # also deletes their history rows
    return jsonify({"message": "Account deleted"})


@app.route("/api/history", methods=["GET"])
@token_required
def history(current_user):
    rows = get_user_history(current_user["id"])
    # Convert each database row into a normal dictionary so jsonify can send it.
    items = [dict(row) for row in rows]
    return jsonify(items)


@app.route("/api/history/export", methods=["GET"])
@token_required
def export_history(current_user):
    # Download the user's library as a CSV file ("my data" feature). Columns
    # follow the common book-list import shape (Goodreads-style): title,
    # author, publisher, year, favorite flag, and when it was scanned.
    import csv
    import io
    rows = get_user_history(current_user["id"])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Title", "Author", "Genres", "Publisher", "Published",
                     "Favorite", "Reading Status", "Private Note", "Scanned At"])
    for row in rows:
        r = dict(row)
        writer.writerow([r.get("title", ""), r.get("author", ""),
                         r.get("categories", ""),
                         r.get("publisher", ""), r.get("published_date", ""),
                         "yes" if r.get("is_favorite") else "no",
                         r.get("reading_status", "identified"),
                         r.get("private_note", ""),
                         # scanned_at (from history), NOT created_at. The row
                         # is b.* JOIN history, so created_at is the BOOK's
                         # cache-creation time — on a cache hit that is when
                         # somebody else first scanned the title, which made
                         # every shared book export the wrong date.
                         r.get("scanned_at", "")])
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=booklens_library.csv"
    return resp


@app.route("/api/history/<int:history_id>/favorite", methods=["POST"])
@token_required
def favorite_history_item(current_user, history_id):
    # Star / unstar one scan. toggle_favorite only touches rows owned by
    # this user, so nobody can change another user's favorites.
    new_value = toggle_favorite(current_user["id"], history_id)
    if new_value is None:
        return jsonify({"error": "History item not found"}), 404
    return jsonify({"is_favorite": new_value})


@app.route("/api/history/<int:history_id>/reading", methods=["PATCH"])
@token_required
def update_reading_item(current_user, history_id):
    """Save a private reading status and note for one owned history row."""
    data = request.get_json(silent=True) or {}
    allowed = {"identified", "want_to_read", "reading", "finished"}
    status = str(data.get("reading_status") or "identified").strip().lower()
    note_value = data.get("private_note", "")
    if status not in allowed:
        return jsonify({"error": "Invalid reading status"}), 400
    if not isinstance(note_value, str):
        return jsonify({"error": "Private note must be text"}), 400
    note = note_value.strip()
    if len(note) > 1000:
        return jsonify({"error": "Private note must be 1000 characters or fewer"}), 400
    updated = update_history_reading(current_user["id"], history_id,
                                     status, note)
    if updated is None:
        return jsonify({"error": "History item not found"}), 404
    return jsonify(updated)


@app.route("/api/history/<int:history_id>", methods=["DELETE"])
@token_required
def remove_history_item(current_user, history_id):
    deleted = delete_history_item(current_user["id"], history_id)
    if deleted == 0:
        return jsonify({"error": "History item not found"}), 404
    return jsonify({"message": "Removed from history"})


# ----- Admin routes (admin accounts only) -----

@app.route("/api/admin/stats", methods=["GET"])
@token_required
@admin_required
def admin_stats(current_user):
    # Totals for the dashboard cards + the latest scan activity.
    stats = get_admin_stats()
    stats.update(identification_counts())
    counts = catalogue_counts()
    stats.update({f"catalogue_{key}": value for key, value in counts.items()})
    stats["recent_scans"] = [dict(row) for row in get_recent_scans(10)]
    return jsonify(stats)


@app.route("/api/admin/users", methods=["GET"])
@token_required
@admin_required
def admin_users(current_user):
    rows = get_all_users()
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/books", methods=["GET"])
@token_required
@admin_required
def admin_books(current_user):
    rows = get_all_books()
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/books/<int:book_id>", methods=["DELETE"])
@token_required
@admin_required
def admin_delete_book(current_user, book_id):
    delete_book(book_id)
    audit_admin_action(current_user["id"], "DELETE", "cached_book", book_id)
    return jsonify({"message": "Book deleted"})


@app.route("/api/admin/messages", methods=["GET"])
@token_required
@admin_required
def admin_messages(current_user):
    # All contact-form messages for the admin inbox.
    rows = get_all_messages()
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/messages/<int:message_id>", methods=["DELETE"])
@token_required
@admin_required
def admin_delete_message(current_user, message_id):
    delete_message(message_id)
    audit_admin_action(current_user["id"], "DELETE", "message", message_id)
    return jsonify({"message": "Message deleted"})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@token_required
@admin_required
def admin_delete_user(current_user, user_id):
    # Safety check: an admin should not be able to delete their own account.
    if user_id == current_user["id"]:
        return jsonify({"error": "You cannot delete your own account"}), 400
    delete_user(user_id)
    audit_admin_action(current_user["id"], "DELETE", "user", user_id)
    return jsonify({"message": "User deleted"})


def validate_catalogue_payload(data):
    limits = {
        "title": 240, "author": 180, "publisher": 180,
        "publication_year": 20, "genres": 500, "source_dataset": 240,
        "source_summary": 20000, "verified_summary": 20000,
        "verification_notes": 4000, "google_volume_id": 120,
        "open_library_edition_id": 120, "open_library_work_id": 120,
        "verified_at": 64, "reviewed_by": 180, "reviewed_at": 64,
        "review_notes": 4000, "short_summary": 4000,
        "short_summary_method": 120, "short_summary_model": 240,
        "short_summary_source_sha256": 64,
        "short_summary_generated_at": 64,
    }
    for field, maximum in limits.items():
        if len(str(data.get(field) or "")) > maximum:
            return f"{field.replace('_', ' ').title()} is too long"
    for field in ("isbn_10", "isbn_13"):
        value = str(data.get(field) or "").strip()
        if value and not valid_isbn(value):
            return f"{field.replace('_', '-').upper()} is invalid"
    return ""


@app.route("/api/admin/catalogue", methods=["GET"])
@token_required
@admin_required
def admin_catalogue(current_user):
    try:
        rows = list_catalogue(request.args.get("status", ""),
                              request.args.get("q", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/catalogue", methods=["POST"])
@token_required
@admin_required
def admin_create_catalogue(current_user):
    data = request.get_json(silent=True) or {}
    error = validate_catalogue_payload(data)
    if error:
        return jsonify({"error": error}), 400
    try:
        record_id = create_catalogue_book(data, current_user["id"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    audit_admin_action(current_user["id"], "CREATE", "catalogue_book",
                       record_id, data.get("verification_status", "PENDING"))
    return jsonify({"message": "Catalogue record created", "id": record_id}), 201


@app.route("/api/admin/catalogue/<int:record_id>", methods=["POST"])
@token_required
@admin_required
def admin_update_catalogue(current_user, record_id):
    data = request.get_json(silent=True) or {}
    error = validate_catalogue_payload(data)
    if error:
        return jsonify({"error": error}), 400
    try:
        updated = update_catalogue_book(record_id, data, current_user["id"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    if not updated:
        return jsonify({"error": "Catalogue record not found"}), 404
    audit_admin_action(current_user["id"], "UPDATE", "catalogue_book",
                       record_id, data.get("verification_status", ""))
    return jsonify({"message": "Catalogue record updated"})


@app.route("/api/admin/identifications", methods=["GET"])
@token_required
@admin_required
def admin_identifications(current_user):
    return jsonify([dict(row) for row in list_identification_attempts(150)])


@app.route("/api/admin/audit-logs", methods=["GET"])
@token_required
@admin_required
def admin_audit_logs(current_user):
    return jsonify([dict(row) for row in get_admin_logs(150)])


@app.route("/api/admin/users/<int:user_id>/active", methods=["POST"])
@token_required
@admin_required
def admin_set_user_active(current_user, user_id):
    if user_id == current_user["id"]:
        return jsonify({"error": "You cannot deactivate your own account"}), 400
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("is_active"), bool):
        return jsonify({"error": "is_active must be true or false"}), 400
    if not set_user_active(user_id, data["is_active"]):
        return jsonify({"error": "User not found"}), 404
    audit_admin_action(current_user["id"], "SET_ACTIVE", "user", user_id,
                       str(data["is_active"]))
    return jsonify({"message": "User status updated"})


@app.route("/api/admin/system", methods=["GET"])
@token_required
@admin_required
def admin_system(current_user):
    # This used to `import summarizer` purely to report whether the FLAN-T5
    # checkpoint had loaded. The deployable build does not ship summarizer.py,
    # torch or transformers -- the model is not on the request path -- so that
    # import raised and the System tab returned 500. The status is reported
    # from the filesystem instead, which is all it ever needed.
    configured_model = os.environ.get("SUMMARIZER_MODEL_DIR")
    checkpoint_available = bool(
        configured_model and os.path.exists(configured_model) or
        os.path.exists("models/flan-t5-base-booksum") or
        os.path.exists("models/flan-t5-finetuned"))
    return jsonify({
        "database": "available",
        "catalogue": catalogue_counts(),
        "google_books_configured": bool(os.environ.get("GOOGLE_BOOKS_API_KEY")),
        "summarizer_shipped": False,
        "flan_t5_available": checkpoint_available,
        "flan_t5_load_policy": "not_shipped_not_used_by_production_overview",
        "external_overview_method": EXTERNAL_OVERVIEW_METHOD,
        "ocr_primary": True,
        "barcode_role": "optional_fallback",
    })



def find_current_external_summary(book):
    """Return only cache rows produced by the active deterministic method."""
    cached = find_external_summary(book)
    if cached is None:
        return None
    return cached if cached["summary_method"] == EXTERNAL_OVERVIEW_METHOD else None


# ----- Background summary worker -----
# The summarizer needs ~15-25s of CPU per book (beam-4 decode), plus the
# description lookup. Doing that inside the request froze every first scan for
# 40-60s, so the scan/search endpoints now return the matched book
# IMMEDIATELY with summary_status="pending", and this single worker thread
# writes the summary into the books row afterwards. ONE worker on purpose:
# two concurrent generations would fight for the same CPU cores and both
# finish slower than they would in a queue. The frontend polls
# GET /api/books/<id>/summary until it flips to "ready".
_summary_queue = queue.Queue()
_summary_inflight = set()
_summary_lock = threading.Lock()
_summary_worker_started = False


def _run_summary_job(book_id, book):
    # Tier 1 never reaches this worker. Tier 2 considers Google Books and Open
    # Library only through the selected record's exact IDs, then extracts one
    # safe adjacent sentence window. No model is loaded and no prose is made up.
    if book.get("catalogue_id"):
        update_book_ai_summary(book_id, "", "unavailable")
        logging.warning("Runtime model blocked for Tier-1 book %s", book_id)
        return

    provider, provider_id = external_identity(book)
    if not provider_id:
        update_book_ai_summary(book_id, "", "unavailable")
        update_book_description_source(book_id, "none", "no_exact_external_identifier")
        return

    overview_result = build_external_overview(book)
    source_text = overview_result.get("source_text") or ""
    source = overview_result.get("source")
    reason = overview_result.get("reason") or ""
    if source_text:
        # Store safe plain text so cached/library views never show provider
        # markup. It remains the exact selected provider's grounded content.
        update_book_description(book_id, source_text)
    summary = (overview_result.get("overview") or "").strip()
    if not summary:
        # Failed/empty requests are deliberately not written to the external
        # cache; a later explicit selection may retry after the provider heals.
        update_book_ai_summary(book_id, "", "unavailable")
        update_book_description_source(book_id, source or "none", reason)
        logging.info("EXTERNAL OVERVIEW unavailable for %s:%s (%s)",
                     provider, provider_id, reason)
        return

    update_book_description(book_id, source_text)
    update_book_description_source(book_id, source, "")
    update_book_ai_summary(book_id, summary, "ready")
    cache_external_summary(book, source_text, source, summary,
                           EXTERNAL_OVERVIEW_METHOD, "ready")
    logging.info("EXTERNAL OVERVIEW ready for %s:%s (%s, source=%s)",
                 provider, provider_id, EXTERNAL_OVERVIEW_METHOD, source)


def _summary_worker():
    while True:
        job = _summary_queue.get()
        try:
            _run_summary_job(*job)
        except Exception:
            # Leave ai_summary empty — the status endpoint re-queues a book
            # whose summary is missing and not in flight, so a transient
            # failure heals on the next poll.
            logging.exception("Background summary failed for book %s", job[0])
        finally:
            with _summary_lock:
                _summary_inflight.discard(job[0])
            _summary_queue.task_done()


def enqueue_summary(book_id, book):
    # Queue a summary job exactly once per book. `book` is the whole matched
    # book dict, not just strings, because resolving a description needs the
    # volume's IDENTIFIERS (google_books_id / isbn_13 / open_library_key) —
    # those are what tie the description to this exact volume.
    # Under TESTING the job runs inline (no thread), so tests stay deterministic.
    global _summary_worker_started
    if app.config.get("TESTING"):
        try:
            _run_summary_job(book_id, book)
        except Exception:
            logging.exception("Inline summary failed for book %s", book_id)
        return
    with _summary_lock:
        if book_id in _summary_inflight:
            return
        _summary_inflight.add(book_id)
        if not _summary_worker_started:
            threading.Thread(target=_summary_worker, daemon=True,
                             name="summary-worker").start()
            _summary_worker_started = True
    _summary_queue.put((book_id, book))


def ensure_summary(book_row):
    # SELF-HEALING CACHE: cached books normally come back with their stored
    # summary. If it is empty (old rows were reset after we improved the
    # summarizer, or a background job failed), queue a regeneration and let
    # the frontend poll for it. Returns (book, summary_status).
    # Statuses: "ready" | "pending" | "unavailable".
    book = dict(book_row)   # sqlite3.Row cannot be modified -> dict copy
    if (book.get("ai_summary") or "").strip():
        return book, "ready"
    if not book.get("catalogue_id") and book.get("summary_status") == "pending":
        return book, "pending"
    # Tier-1 summaries are precomputed once during catalogue construction.
    # A missing stored summary is an honest unavailable state, never a reason
    # to load FLAN-T5 during a user request.
    return book, "unavailable"


def edition_evidence(book, scanned_isbn="", exact_isbn=None):
    """Two SEPARATE facts about the object on the card.

    They get confused constantly, so they are kept apart here:

      identity   -- do we know WHICH physical copy the reader is holding?
                    True only when an ISBN taken off the object (barcode or
                    typed) is one of the matched record's ISBNs.

      page_basis -- what does this record's page_count actually DESCRIBE?

    Confirming identity does NOT make the page count exact, and that is the
    whole point of this function. Open Library returns page_count as
    number_of_pages_median -- a median ACROSS the work's editions -- and picks
    isbn_13 as "the first 13-digit ISBN, in no useful order" from every edition
    of the work. So an Open Library candidate can legitimately match a scanned
    ISBN while its page count remains a cross-edition average. Treating that as
    "352 pages, about 9 hours, this is your copy" would be exactly the kind of
    unearned confidence the refusal behaviour exists to avoid.

    Google is different: parse_book() reads pageCount and industryIdentifiers
    from the SAME volume record, so when the scanned ISBN matches a Google
    volume, the page count does belong to that ISBN's edition.

    catalogue_books has no page-count column at all, so a Tier-1 row supplies
    nothing here and the row simply hides. It is never labelled a median,
    because that is not where it would have come from.
    """
    query = normalize_isbn(scanned_isbn or "")
    if exact_isbn is None:
        # Same comparison score_candidate() makes in the frozen matching core,
        # using the same normaliser. Preferred from there when available.
        known = {normalize_isbn(book.get("isbn_13") or ""),
                 normalize_isbn(book.get("isbn_10") or "")}
        known.discard("")
        exact_isbn = bool(query and query in known)

    pages = int(book.get("page_count") or 0)
    if pages <= 0:
        basis = "unknown"
    elif book.get("google_books_id"):
        # A specific volume record. Exact only when the reader's own ISBN is
        # the one on it.
        basis = "isbn_edition" if exact_isbn else "google_volume"
    elif (book.get("open_library_work_id") or book.get("open_library_key")
          or book.get("open_library_edition_id")):
        basis = "ol_work_median"
    elif book.get("catalogue_id"):
        basis = "catalogue_record"
    else:
        basis = "unknown"

    return {
        "identity": "isbn_confirmed" if exact_isbn else "unconfirmed",
        "page_basis": basis,
    }


def live_for_client(book_id, title, author="", stored_pages=0):
    """What Open Library says about this book today, ready for the card.

    Measured before it was built: 98 of the 100 benchmark books carry a rating,
    median 117 raters. On 100 books published in 2026, Open Library knows 86 and
    rates NONE -- so a new book has no rating anywhere and the card must say so
    instead of inventing one.

    Also carries a page count taken as a median across editions, which matters
    here because 113 of the 127 cached books have none at all and cannot show a
    reading time without it. The stored value wins when it exists; this only
    fills the gap.
    """
    try:
        signals = livesignals.get(book_id, title, author)
    except Exception:                                          # noqa: BLE001
        # A third-party bonus must never cost the reader their result.
        logging.warning("live signals unavailable for %r", title)
        return None
    payload = livesignals.for_client(signals)
    if payload and stored_pages:
        # Never overrule a page count we already hold; only fill in.
        payload["page_count"] = 0
    return payload


def already_read(user_id, title, author=""):
    """The one line on the card that is a fact rather than a judgement.

    Returned as its own field instead of being folded into for_you: taste is
    an inference from subjects and can be wrong, this cannot, and the card
    must be able to say so with different weight.
    """
    return prior_engagement(user_id, title, author)


def taste_for_client(user_id, categories, book_id=None, title=""):
    """The "Is this for you?" block for one scan result.

    Built from the signed-in user's OWN history and favourites -- never seeded,
    never shared between accounts. A new account legitimately gets the
    cold-start state; that is the honest answer, not a bug to paper over.
    """
    # How often each subject appears across the catalogue, so a shared label
    # can be weighted by how much it actually distinguishes. Cached in
    # database.py, so this is a dictionary lookup rather than a query.
    counts, catalogue_size = catalogue_subject_counts()
    if not user_id:
        # No signed-in user means no library to compare against. Cold start is
        # the truthful answer; it must never raise and lose the whole scan.
        return taste_profile.assess(categories, [], title,
                                    subject_counts=counts,
                                    catalogue_size=catalogue_size)
    history = [dict(r) for r in get_taste_profile_books(user_id, book_id)]
    return taste_profile.assess(categories, history, title,
                                get_user_interests(user_id),
                                subject_counts=counts,
                                catalogue_size=catalogue_size)


def classify_ocr(result):
    title = (result.get("probable_title") or "").strip()
    confidence = float(result.get("confidence_score") or 0)
    if not title:
        return "OCR_FAILED"
    if confidence < 0.55 or len(title) < 3:
        return "OCR_LOW_CONFIDENCE"
    return "OCR_SUCCESS"


def candidate_for_client(candidate, candidate_id=None):
    """Return useful comparison metadata without internal ranking fields."""
    fields = ("title", "author", "thumbnail", "publisher", "published_date",
              "page_count", "categories", "isbn_10", "isbn_13",
              "google_books_id", "open_library_edition_id",
              "open_library_work_id", "open_library_key", "provider",
              "catalogue_id",
              "score", "decision", "reasons", "score_breakdown")
    out = {key: candidate.get(key) for key in fields if key in candidate}
    language = language_for_client(candidate.get("description", ""))
    if language:
        out["description_language"] = language
    if candidate_id is not None:
        out["candidate_id"] = candidate_id
    return out


def retrieve_local_candidates(title, author="", isbn="", full_text="",
                              text_lines=None, limit=5):
    """Match one OCR pass against Tier 1, including raw-text recovery."""
    catalogue = verified_catalogue_candidates()
    local = rank_candidates(catalogue, title, author, isbn, limit=limit)
    if local["decision"] != REJECTED:
        local["tier"] = "local_catalogue"
        return local
    if full_text or text_lines:
        recovered = recover_ocr_candidates(
            catalogue, title, author, full_text, text_lines, limit=limit)
        if recovered["decision"] != REJECTED:
            recovered["tier"] = RECOVERY_TIER
            return recovered
    return local


# The tier name recover_ocr_candidates results are filed under. Kept as a
# constant because two functions now have to agree on what "this match came
# from scrambled text" means.
RECOVERY_TIER = "local_catalogue_ocr_recovery"


def candidate_identity(candidate):
    """The same identity rule rank_candidates de-duplicates on.

    Reused rather than reinvented: if the two disagreed, a book found by both
    Google and the catalogue would appear twice in one chooser.
    """
    return (candidate.get("google_books_id") or
            candidate.get("open_library_key") or
            normalize_isbn(candidate.get("isbn_13")) or
            "%s|%s" % (normalize_match_text(candidate.get("title")),
                       normalize_match_text(candidate.get("author"))))


def merge_recovery_with_external(recovery, external, limit=5):
    """Combine a scrambled-text recovery with what the providers returned.

    WHY THIS EXISTS. recover_ocr_candidates matches catalogue rows against the
    RAW cover text, which includes the back-cover blurb. Praise quotes name
    other books, so a cover reading "...for readers of Cormac McCarthy's The
    Road" recovered The Road at 79.9 and, because that is not a rejection, the
    funnel stopped and never asked Google -- the reader was shown one wrong
    book with no way to reach the right one. Measured on the 100-cover
    benchmark, every wrong short-circuit came from this path.

    So a recovery result is now evidence, not a verdict. Its candidates are
    kept and shown, but the providers are always consulted as well.
    """
    if external.get("decision") == HIGH_CONFIDENCE:
        # An exact provider identity outranks a guess assembled from loose
        # words on a cover.
        external["tier"] = "external"
        return external

    external_candidates = list(external.get("candidates") or [])
    if not external_candidates:
        # Providers had nothing to add: behave exactly as before, so a network
        # outage cannot make identification worse than it already was.
        return recovery

    merged = []
    seen = set()
    for candidate in external_candidates + list(recovery.get("candidates") or []):
        identity = candidate_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        candidate = dict(candidate)
        # Nothing in this list may auto-accept. Half of it was recovered from
        # jumbled text, and the reader is the one who can see the cover.
        candidate["decision"] = NEEDS_CONFIRMATION
        merged.append(candidate)

    # Recovery scores and provider scores come from the same score_candidate()
    # scale, so they are comparable. Deliberately NOT re-ranked through
    # rank_candidates: that would score the recovered rows against the garbled
    # OCR title they already failed, throwing away every book recovery gets
    # right.
    merged.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return {
        "decision": NEEDS_CONFIRMATION,
        "candidates": merged[:max(1, int(limit))],
        "tier": "local_recovery_plus_external",
        "rejected_count": int(external.get("rejected_count") or 0),
    }


def retrieve_tiered_candidates(title, author="", isbn="", full_text="", limit=5,
                               text_lines=None):
    """Use Tier 1 exclusively unless the verified local catalogue misses.

    "Misses" now includes a match that only the raw-text recovery could make.
    A confident, directly-matched catalogue row still answers on its own --
    that is the whole speed argument for having a local catalogue.
    """
    local = retrieve_local_candidates(title, author, isbn, full_text,
                                      text_lines, limit=limit)
    if local["decision"] != REJECTED and local.get("tier") != RECOVERY_TIER:
        return local

    external = retrieve_ranked_candidates(title, author, isbn, full_text,
                                          limit=limit, text_lines=text_lines)
    if local["decision"] == REJECTED:
        external["tier"] = "external"
        return external
    return merge_recovery_with_external(local, external, limit=limit)


def finalize_candidate(current_user, attempt_id, candidate_id,
                       match_method="candidate_confirmation"):
    stored = get_candidate_for_user(candidate_id, attempt_id, current_user["id"])
    if stored is None:
        return jsonify({"error": "Candidate not found"}), 404
    if stored["decision"] == REJECTED:
        return jsonify({"error": "Rejected candidates cannot be selected"}), 400

    candidate = dict(stored["metadata"])
    candidate["score"] = stored["score"]
    candidate["reasons"] = stored["reasons"]
    is_local = candidate.get("provider") == "local_catalogue"
    if is_local:
        catalogue = get_catalogue_book(candidate.get("catalogue_id"))
        if catalogue is None or catalogue["verification_status"] != "VERIFIED":
            return jsonify({"error": "Verified catalogue record is unavailable"}), 409
        external_cached = None
    else:
        catalogue = lookup_verified_catalogue(candidate)
        external_cached = find_current_external_summary(candidate) if not catalogue else None
        # A known catalogue ID or exact external cache hit needs no network.
        if not catalogue and not external_cached:
            candidate = hydrate_exact_candidate(candidate)
            catalogue = lookup_verified_catalogue(candidate)
            external_cached = find_current_external_summary(candidate) if not catalogue else None
    verified_summary = (catalogue["verified_summary"] or "").strip() if catalogue else ""
    short_summary = ((catalogue["short_summary"] or "").strip() if catalogue
                     else ((external_cached["short_summary"] or "").strip()
                           if external_cached else ""))
    stored_short_status = (catalogue["short_summary_status"] or "") if catalogue else ""
    _, external_id = external_identity(candidate)
    if catalogue:
        summary_status = ("ready" if short_summary and
                          stored_short_status in {"ok", "fallback_extract"}
                          else "unavailable")
    elif external_cached:
        summary_status = "ready"
    else:
        summary_status = "pending" if external_id else "unavailable"

    existing = find_cached_exact(candidate)
    if existing is None:
        book_data = {
            "title": candidate.get("title", ""),
            "author": candidate.get("author", ""),
            # Provider descriptions are display-only and labelled as such.
            "description": (external_cached["source_description"]
                            if external_cached else candidate.get("description", "")),
            "ai_summary": short_summary,
            "thumbnail": candidate.get("thumbnail", ""),
            "page_count": candidate.get("page_count", 0),
            "publisher": candidate.get("publisher", ""),
            "published_date": candidate.get("published_date", ""),
            "categories": candidate.get("categories", ""),
            "confidence": "high" if stored["decision"] == HIGH_CONFIDENCE else "confirmed",
            "google_books_id": candidate.get("google_books_id", ""),
            "isbn_10": candidate.get("isbn_10", ""),
            "isbn_13": candidate.get("isbn_13", ""),
            "open_library_edition_id": candidate.get("open_library_edition_id", ""),
            "open_library_work_id": candidate.get("open_library_work_id", "") or candidate.get("open_library_key", ""),
            "catalogue_id": catalogue["id"] if catalogue else None,
            "verified_summary": verified_summary,
            "summary_status": summary_status,
            "description_source": ("catalogue_verified" if catalogue else
                                   (external_cached["description_source"]
                                    if external_cached else "")),
            "description_reason": "" if catalogue or external_cached else "external_summary_pending",
        }
        book_id = save_book(book_data)
    else:
        book_id = existing["id"]
        # Heal a row saved before its source had a cover. Only fills an empty
        # thumbnail, so it can never replace a better image.
        backfill_book_thumbnail(book_id, candidate.get("thumbnail", ""))
        # Clear any legacy API/Wikipedia-generated summary on every selection;
        # only the current verified catalogue record may repopulate it.
        update_book_verified_summary(
            book_id, catalogue["id"] if catalogue else None,
            verified_summary, short_summary, summary_status)
        if external_cached:
            update_book_description(book_id, external_cached["source_description"])
            update_book_description_source(
                book_id, external_cached["description_source"], "")

    history_id = save_history(current_user["id"], book_id)
    complete_identification(attempt_id, candidate_id, book_id,
                            HIGH_CONFIDENCE if stored["decision"] == HIGH_CONFIDENCE
                            else "USER_CONFIRMED")
    row = dict(get_book_by_id(book_id))
    if not catalogue and not external_cached and external_id:
        enqueue_summary(book_id, row)
        row = dict(get_book_by_id(book_id))
    return jsonify({
        "status": "success",
        "source": "local_catalogue" if catalogue else "external_candidate",
        "match_method": match_method,
        "attempt_id": attempt_id,
        "history_id": history_id,
        "confidence": "high" if stored["decision"] == HIGH_CONFIDENCE else "confirmed",
        "book": row,
        # Evidence from the user's own library. See taste_profile.py.
        "for_you": taste_for_client(current_user["id"],
                                    row.get("categories", ""), row.get("id"),
                                    row.get("title", "")),
        "already_read": already_read(current_user["id"], row.get("title", ""),
                                     row.get("author", "")),
        "live": live_for_client(row.get("id"), row.get("title", ""),
                                row.get("author", ""), row.get("page_count") or 0),
        "edition_evidence": edition_evidence(
            row, stored.get("attempt_query_isbn", ""),
            # score_candidate() in the frozen matching core already decided
            # whether the query ISBN is one of this candidate's ISBNs. Reuse
            # its answer instead of making the comparison a second time.
            exact_isbn=(candidate.get("score_breakdown") or {}).get("exact_isbn")),
        "catalogue_status": "VERIFIED" if catalogue else "NOT_IN_VERIFIED_CATALOGUE",
        "summary_trust": ("CATALOGUE_VERIFIED" if catalogue else
                          "EXTERNAL_NOT_VERIFIED"),
        "summary_status": row.get("summary_status") or summary_status,
        "summary_message": ("" if (row.get("ai_summary") or "").strip() else
                            ("External summary is being prepared from the exact selected record."
                             if row.get("summary_status") == "pending" else
                             "A grounded summary is not available for this book.")),
    })


# Words that appear on covers in their own right and are never a surname. The
# author guess below is the first block SMALLER than the title, which on a cover
# reading "The ALCHEMIST ... PAULO COELHO" is the word "The".
_NOT_AN_AUTHOR = {
    "the", "a", "an", "of", "and", "by", "in", "on", "to", "for", "from",
    "new", "no", "is", "it", "all", "with", "his", "her", "you", "your",
}


def usable_ocr_author(value):
    """Return the OCR author only when it could plausibly be a name.

    A WRONG author is worse than no author at all, and the difference is not
    subtle. score_candidate subtracts 35 for an author mismatch and
    rank_candidates rejects outright below 35 similarity, so "The" offered as
    the author of The Alchemist does not merely fail to help -- it throws Paulo
    Coelho's novel out of the results, and the reader is shown "The Alchemist
    Cocktail Book" instead. Blanking it costs only the author bonus, and a
    candidate with no author supplied is still offered for confirmation.

    Measured on 100 real covers: 18 produced an author like this, and dropping
    it recovered 3 books that had been showing the WRONG title confidently.
    """
    value = (value or "").strip()
    if not value:
        return ""
    words = re.findall(r"[A-Za-z']+", value.lower())
    if not words:
        # Digits only: a price, a barcode, or an ISBN printed on the cover.
        return ""
    if all(word in _NOT_AN_AUTHOR or len(word) <= 2 for word in words):
        return ""
    return value


def drop_derived_products(candidates, keep_when_empty=True):
    """Remove study guides and summaries when the real book is also on offer.

    score_candidate already rejects these, but rank_candidates is not the only
    way a candidate reaches the screen: when it rejects everything,
    retrieve_ranked_candidates falls back to recover_ocr_candidates, and the
    recovery path applies no derived-edition gate at all. That is how
    "Summary: Atomic Habits by James Clear" ended up in a chooser next to the
    real Atomic Habits.

    Only ever removes rows while a genuine book remains. If every candidate
    looks derived, they are all kept: the reader can see the covers and decide,
    and silently emptying the list would turn a poor answer into no answer.
    """
    kept = [c for c in candidates or []
            if not UNSAFE_EDITION_RE.search(" ".join(filter(None, (
                c.get("title") or "", c.get("publisher") or ""))))]
    if kept or keep_when_empty:
        return kept if kept else list(candidates or [])
    return []


def collapse_duplicate_editions(candidates):
    """Show each book once, however many editions the providers returned.

    Google gives every printing its own volume id, so the ranking layer -- which
    de-duplicates on identity, and correctly treats two ids as two records --
    happily hands over five rows that all say "Rich Dad Poor Dad". Measured on
    the 100-cover benchmark, 34 choosers repeated a title that way, and to a
    reader that does not look thorough, it looks broken.

    The reader is choosing WHICH BOOK they are holding, not which printing, so
    the highest-scoring row per title+author survives and the rest are dropped.
    Anyone who needs an exact printing scans the barcode, which resolves by ISBN
    and never reaches this path. Ordering is preserved: the list arrives sorted
    by score, and keeping the first occurrence keeps the best one.
    """
    collapsed = []
    for candidate in candidates or []:
        title = normalize_match_text(candidate.get("title"))
        author = normalize_match_text(candidate.get("author"))
        duplicate = False
        for kept in collapsed:
            if normalize_match_text(kept.get("title")) != title:
                continue
            kept_author = normalize_match_text(kept.get("author"))
            # Same title is not enough on its own: "The Hobbit" names both
            # Tolkien's novel and a video-game strategy guide, and "Stephen
            # King" is both an author and the title of a book ABOUT him. The
            # authors have to agree too -- loosely, because providers write the
            # same person as "F. Scott Fitzgerald", "F Scott Fitzgerald" and
            # "Francis Scott Fitzgerald". CACHE_AUTHOR_MATCH is the threshold
            # database.py already uses for exactly this judgement.
            if not author or not kept_author or                     fuzz.token_set_ratio(author, kept_author) >= CACHE_AUTHOR_MATCH:
                duplicate = True
                break
        if not duplicate:
            collapsed.append(candidate)
    return collapsed


def begin_candidate_funnel(current_user, evidence, ranked, input_method):
    decision = ranked.get("decision", REJECTED)
    evidence = dict(evidence)
    evidence["decision"] = decision
    evidence["failure_reason"] = ranked.get("error", "") if decision == REJECTED else ""
    # An offer made ENTIRELY of study guides and box sets is not an answer, it
    # is a wrong answer wearing a chooser. Verity was read as "HOOVER COLLEEN"
    # -- the author's name, not the title -- and the only candidate that came
    # back was a Colleen Hoover ebook bundle. Refusing is the honest outcome
    # there, and it is the outcome this product is built around.
    candidates = collapse_duplicate_editions(
        drop_derived_products(ranked.get("candidates", []), keep_when_empty=False))
    if not candidates:
        decision = REJECTED
        evidence["decision"] = REJECTED
        evidence["failure_reason"] = evidence.get("failure_reason") or (
            "Only derived editions matched, not the book itself.")

    attempt_id = create_identification_attempt(current_user["id"], input_method, evidence)
    candidate_ids = save_candidate_matches(attempt_id, candidates)
    client_candidates = [candidate_for_client(c, cid)
                         for c, cid in zip(candidates, candidate_ids)]
    # Every option, not just the first. The chooser is where most scans land,
    # and "you have already read that one" is the single most useful thing the
    # app can say while someone is deciding between three similar covers.
    for shown in client_candidates:
        shown["already_read"] = already_read(
            current_user["id"], shown.get("title", ""), shown.get("author", ""))

    if decision == HIGH_CONFIDENCE and candidate_ids:
        return finalize_candidate(current_user, attempt_id, candidate_ids[0], input_method)
    if decision == NEEDS_CONFIRMATION:
        # When there is exactly one candidate the interface shows the full result
        # card with a confirm bar rather than a chooser with a single option, so
        # the card needs its content BEFORE anything is written. Nothing here
        # saves: the evidence is computed from the candidate the reader is being
        # asked about, and the book enters their library only on confirmation.
        if client_candidates:
            top = client_candidates[0]
            top["for_you"] = taste_for_client(
                current_user["id"], top.get("categories", ""), None,
                top.get("title", ""))

            top["edition_evidence"] = edition_evidence(
                top, evidence.get("query_isbn", ""),
                exact_isbn=(top.get("score_breakdown") or {}).get("exact_isbn"))
        return jsonify({
            "status": "needs_confirmation",
            "decision": NEEDS_CONFIRMATION,
            "confidence": "medium",
            "attempt_id": attempt_id,
            "ocr": {
                "status": evidence.get("ocr_status", ""),
                "extracted_title": evidence.get("ocr_title", ""),
                "extracted_author": evidence.get("ocr_author", ""),
                "confidence_score": evidence.get("ocr_confidence", 0),
            },
            "candidates": client_candidates,
            "message": "Please select the exact book before any summary is shown.",
        })
    return jsonify({
        "status": "partial", "decision": REJECTED, "confidence": "low",
        "attempt_id": attempt_id, "book": None,
        # Already recorded against the attempt; sending it lets the refusal
        # screen give advice that fits the actual failure instead of one
        # generic apology. Retaking the photo helps when the cover could not be
        # READ; it does not help when the text was read fine and no candidate
        # cleared the gates.
        "failure_reason": evidence.get("failure_reason", ""),
        "ocr": {"status": evidence.get("ocr_status", ""),
                "extracted_title": evidence.get("ocr_title", ""),
                "extracted_author": evidence.get("ocr_author", "")},
        "message": ranked.get("error") or
                   "No candidate was strong enough to verify. Edit the search and try again.",
    })


def prune_uploads(folder, keep=0):
    # Delete uploaded cover photos, keeping only the newest `keep`.
    # DEFAULT keep=0: "Photos are never stored" is a promise on the landing
    # page, so every upload is removed as soon as its scan finishes.
    # Debugging a bad scan needs the photo the OCR actually read, so
    # KEEP_UPLOADS_FOR_DEBUG=N (env) retains the newest N alongside the
    # SCAN log lines — for development only.
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)]
        files = [f for f in files if os.path.isfile(f)]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass   # pruning must never break a scan response


# How many uploads to retain (0 = delete immediately after each scan).
KEEP_UPLOADS = int(os.environ.get("KEEP_UPLOADS_FOR_DEBUG", "0") or 0)
# Clear anything a crash or an earlier debug session left behind, so the
# no-storage promise holds from the moment the server starts.
prune_uploads(UPLOAD_FOLDER, keep=KEEP_UPLOADS)


@app.route("/api/scan", methods=["POST"])
@rate_limited(15, 60)
@token_required
def scan_verified(current_user):
    """OCR-first identification with an explicit confirmation boundary."""
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "Only JPG, PNG, and WebP images are allowed."}), 400

    safe = secure_filename(file.filename) or "cover.jpg"
    extension = safe.rsplit(".", 1)[-1].lower()
    filename = f"{uuid.uuid4().hex}.{extension}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    try:
        file.save(filepath)
        validation_error = validate_saved_image(filepath, file.mimetype)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        tiers = [OCR_REC_TIER]
        if OCR_ESCALATE_REC_TIER != OCR_REC_TIER:
            tiers.append(OCR_ESCALATE_REC_TIER)
        best = None
        best_status = "OCR_FAILED"
        used_tier = OCR_REC_TIER
        ocr_attempts = []
        selected = None
        # A match that only the raw-text recovery could make is held here
        # instead of ending the search. See merge_recovery_with_external().
        recovery_fallback = None
        for tier in tiers:
            result = process_book_cover(filepath, rec_tier=tier)
            status = classify_ocr(result)
            ocr_attempts.append((result, status, tier))
            logging.info(
                "SCAN %s | rec=%s status=%s title=%r author=%r | full=%r",
                filename, tier, status,
                (result.get("probable_title") or "")[:120],
                (result.get("probable_author") or "")[:80],
                (result.get("full_text") or "")[:160])
            if best is None or float(result.get("confidence_score") or 0) > \
                    float(best.get("confidence_score") or 0):
                best, best_status, used_tier = result, status, tier

            # OCR confidence only measures how clearly Paddle read letters;
            # it does not prove those letters were split into title/author
            # correctly. Verify every pass against the catalogue, including
            # its raw lines, and escalate whenever the *book match* rejects.
            pass_title = (result.get("probable_title") or "").strip()
            # Raw value is kept for the audit trail; only the QUERY is cleaned.
            pass_author_raw = (result.get("probable_author") or "").strip()
            pass_author = usable_ocr_author(pass_author_raw)
            pass_text = (result.get("full_text") or "").strip()
            pass_lines = result.get("text_lines") or []
            if pass_title or pass_text or pass_lines:
                local = retrieve_local_candidates(
                    pass_title, pass_author, "", pass_text, pass_lines)
                if local["decision"] != REJECTED:
                    # Low OCR confidence may still recover a plausible book,
                    # but it can never silently auto-save one.
                    if status != "OCR_SUCCESS" and \
                            local["decision"] == HIGH_CONFIDENCE:
                        local = dict(local)
                        local["decision"] = NEEDS_CONFIRMATION
                        local["candidates"] = [dict(candidate)
                                               for candidate in local["candidates"]]
                        for candidate in local["candidates"]:
                            candidate["decision"] = NEEDS_CONFIRMATION
                            candidate.setdefault("reasons", []).append(
                                "OCR confidence is low; confirmation required")
                    if local.get("tier") == RECOVERY_TIER:
                        # Recovered from the whole cover, blurb included, so
                        # it is the weakest evidence the catalogue can offer.
                        # Keep it, but let the better recogniser and then the
                        # providers have their say before answering.
                        if recovery_fallback is None:
                            recovery_fallback = (result, status, tier, local)
                        logging.info(
                            "SCAN %s | rec=%s recovered from raw cover text; "
                            "not answering on that alone", filename, tier)
                    else:
                        selected = (result, status, tier, local)
                        best, best_status, used_tier = result, status, tier
                        logging.info("SCAN %s | rec=%s matched tier=%s decision=%s",
                                     filename, tier, local.get("tier"),
                                     local.get("decision"))
                        break

            # A confident but mis-grouped title is not success. Continue to
            # the complementary recogniser when the matcher cannot verify it.
            if tier != tiers[-1]:
                logging.info("SCAN %s | rec=%s local match rejected; escalating",
                             filename, tier)

        if selected is not None:
            best, best_status, used_tier, ranked = selected
            title = (best.get("probable_title") or "").strip()
            author = (best.get("probable_author") or "").strip()
            confidence = float(best.get("confidence_score") or 0)
            full_text = (best.get("full_text") or "").strip()
            evidence = {
                "ocr_status": best_status, "ocr_title": title,
                "ocr_author": author, "ocr_text": full_text,
                "ocr_confidence": confidence, "ocr_tier": used_tier,
                "query_title": title, "query_author": usable_ocr_author(author),
            }
            return begin_candidate_funnel(current_user, evidence, ranked, "ocr")

        # Tier 1 missed. Try external providers for every readable, confident
        # OCR pass (best confidence first). The provider fetch and its raw-text
        # recovery are generic, so books outside the verified catalogue can
        # benefit too. Stop at the first candidate set that passes the gate.
        external_rejection = None
        external_attempts = sorted(
            (attempt for attempt in ocr_attempts if attempt[1] == "OCR_SUCCESS"),
            key=lambda attempt: float(attempt[0].get("confidence_score") or 0),
            reverse=True)
        for result, status, tier in external_attempts:
            pass_title = (result.get("probable_title") or "").strip()
            pass_author_raw = (result.get("probable_author") or "").strip()
            pass_author = usable_ocr_author(pass_author_raw)
            pass_text = (result.get("full_text") or "").strip()
            ranked = retrieve_ranked_candidates(
                pass_title, pass_author, "", pass_text,
                text_lines=result.get("text_lines") or [])
            ranked["tier"] = "external"
            if recovery_fallback is not None:
                # Show both. The reader can see the cover; the funnel cannot.
                ranked = merge_recovery_with_external(recovery_fallback[3], ranked)
            if ranked["decision"] != REJECTED:
                evidence = {
                    "ocr_status": status, "ocr_title": pass_title,
                    "ocr_author": pass_author_raw, "ocr_text": pass_text,
                    "ocr_confidence": float(result.get("confidence_score") or 0),
                    "ocr_tier": tier, "query_title": pass_title,
                    "query_author": pass_author,
                }
                return begin_candidate_funnel(current_user, evidence, ranked, "ocr")
            external_rejection = external_rejection or ranked

        if recovery_fallback is not None:
            # No OCR pass was confident enough to query a provider with, so
            # the recovered match is all there is. Answering with it is what
            # this code did before providers were consulted at all.
            result, status, tier, ranked = recovery_fallback
            evidence = {
                "ocr_status": status,
                "ocr_title": (result.get("probable_title") or "").strip(),
                "ocr_author": (result.get("probable_author") or "").strip(),
                "ocr_text": (result.get("full_text") or "").strip(),
                "ocr_confidence": float(result.get("confidence_score") or 0),
                "ocr_tier": tier,
                "query_title": (result.get("probable_title") or "").strip(),
                "query_author": usable_ocr_author(result.get("probable_author")),
            }
            return begin_candidate_funnel(current_user, evidence, ranked, "ocr")

        best = best or {}
        title = (best.get("probable_title") or "").strip()
        author = (best.get("probable_author") or "").strip()
        confidence = float(best.get("confidence_score") or 0)
        full_text = (best.get("full_text") or "").strip()
        evidence = {
            "ocr_status": best_status, "ocr_title": title,
            "ocr_author": author, "ocr_text": full_text,
            "ocr_confidence": confidence, "ocr_tier": used_tier,
            "query_title": title, "query_author": usable_ocr_author(author),
        }

        # Existing barcode support is opt-in and runs only after OCR.
        if request.form.get("allow_barcode_fallback") == "1":
            isbn = read_isbn(filepath)
            if isbn:
                evidence["query_isbn"] = isbn
                ranked = retrieve_tiered_candidates(
                    title, usable_ocr_author(author), isbn, full_text,
                    text_lines=best.get("text_lines") or [])
                return begin_candidate_funnel(
                    current_user, evidence, ranked, "optional_isbn_fallback")

        # A confident read that every strict candidate gate rejected remains
        # editable in the existing fallback UI. Low/failed OCR uses the more
        # direct instruction to type a title.
        if best_status == "OCR_SUCCESS" and external_rejection is not None:
            return begin_candidate_funnel(
                current_user, evidence, external_rejection, "ocr")

        attempt_id = create_identification_attempt(
            current_user["id"], "ocr",
            {**evidence, "decision": REJECTED,
             "failure_reason": "ocr_needs_user_edit"})
        return jsonify({
            "status": "ocr_review", "attempt_id": attempt_id,
            "decision": REJECTED, "confidence": "low", "book": None,
            "ocr": {"status": best_status, "extracted_title": title,
                    "extracted_author": author,
                    "confidence_score": confidence, "tier": used_tier,
                    "full_text": full_text},
            "message": ("We could not read the book title clearly from this image. "
                        "Please type the book title to continue."),
        })
    except Exception:
        logging.exception("OCR-first scan pipeline error")
        return jsonify({"error": "Something went wrong while processing the image"}), 500
    finally:
        prune_uploads(UPLOAD_FOLDER, keep=KEEP_UPLOADS)


OL_COVER = "https://covers.openlibrary.org/b/%s/%s-M.jpg?default=false"


def catalogue_cover(row):
    """Our own copy, served from this repository.

    The verified shelf is 60 fixed books, so their covers were downloaded once
    by curate/fetch_covers.py and committed -- 1.1 MB for all of them. A
    catalogue card now needs no network at all: description, subjects and cover
    all come from the machine serving the page.

    No filesystem check here. If a file is ever missing the client falls through
    to the Open Library URL below and then to the placeholder, which is the same
    chain it already walks -- and checking would mean a stat() per row while
    rendering a grid.
    """
    return "/covers/%d.jpg" % row["id"] if row.get("id") else ""


def catalogue_cover_fallback(row):
    """Open Library by ISBN, kept as the safety net behind our own copy.

    Audited over all 250 verified books, before the shelf was cut to 60: the
    edition cover 404s for 90 of them, and 48 of those 90 have a perfectly good
    cover filed under the ISBN -- The Da Vinci Code among them. So a third of
    Browse and a third of the starter shelf were showing "Cover unavailable"
    for books whose cover we were simply asking for the wrong way.
    64% -> 83% for one extra URL.

    It goes to the client as a fallback rather than being resolved here because
    BookCover already walks src -> fallback -> placeholder, and resolving it
    server-side would mean an HTTP request per row while rendering a grid.
    """
    isbn = (row.get("isbn_13") or row.get("isbn_10") or "").strip()
    return OL_COVER % ("isbn", isbn) if isbn else ""


def catalogue_for_reader(row):
    """Reader-facing fields only.

    The admin route returns the whole record -- verification_status, source
    dataset, audit fields, the unverified source_summary. None of that belongs
    to a reader, and shipping it here would leak the review pipeline through a
    public endpoint. This allow-list is the boundary; add to it deliberately.
    """
    row = dict(row)
    return {
        "id": row.get("id"),
        "title": row.get("title", ""),
        "author": row.get("author", ""),
        "publisher": row.get("publisher", ""),
        "published_date": row.get("publication_year", ""),
        "categories": row.get("genres", ""),
        "isbn_13": row.get("isbn_13", ""),
        "thumbnail": catalogue_cover(row),
        "thumbnail_fallback": catalogue_cover_fallback(row),
    }


@app.route("/api/catalogue", methods=["GET"])
@token_required
def browse_catalogue(current_user):
    """Browse the verified books without scanning anything.

    The verified records were reachable only by photographing a cover or
    typing an exact title, which made the most trustworthy data in the product
    invisible. Reading here writes nothing and identifies nothing.
    """
    query = (request.args.get("q") or "").strip()[:120]
    rows = list_catalogue("VERIFIED", query)

    # With no search term the order was updated_at DESC -- which is to say, the
    # order the review pipeline happened to touch them in. For a reader who has
    # told us what they read, the shelf can open somewhere better: nearest
    # first, by the same score the card uses. A SEARCH is left alone; someone
    # typing "Gatsby" wants Gatsby, not something like it.
    if not query:
        counts, catalogue_size = catalogue_subject_counts()
        history = [dict(r) for r in get_taste_profile_books(current_user["id"])]
        nearest = taste_profile.closest_from_shelf(
            history, [dict(r) for r in rows], limit=0, per_reason=None,
            subject_counts=counts, catalogue_size=catalogue_size)
        if nearest:
            order = {item["book"]["id"]: position
                     for position, item in enumerate(nearest)}
            # Everything the score cannot rank keeps its existing place, after
            # what it can. Nothing is dropped from the shelf.
            rows = sorted(rows, key=lambda r: order.get(r["id"], len(order)))

    limit = 60
    return jsonify({
        "total": len(rows),
        "showing": min(len(rows), limit),
        "books": [catalogue_for_reader(r) for r in rows[:limit]],
    })


@app.route("/api/catalogue/<int:record_id>/read", methods=["POST"])
@token_required
def mark_catalogue_book_read(current_user, record_id):
    """Record that the reader has already read a verified book.

    This is what makes a new account useful. "Is this for you?" answers from
    books the reader engaged with, so a fresh account has nothing to answer
    from and the cold-start state used to be a dead end -- it asked them to
    save books while offering no way to do it.

    Marking a book read is a deliberate act, which is exactly the signal
    taste_profile counts. It is NOT an identification: nothing is scanned, no
    candidate is confirmed, and no accuracy claim is made about it.
    """
    row = get_catalogue_book(record_id)
    if row is None or row["verification_status"] != "VERIFIED":
        return jsonify({"error": "Book not found"}), 404

    status = str((request.get_json(silent=True) or {}).get("status")
                 or "finished").strip().lower()
    if status not in {"finished", "reading", "want_to_read"}:
        return jsonify({"error": "Invalid reading status"}), 400

    book = catalogue_for_reader(row)
    book_id = save_book({
        "title": book["title"], "author": book["author"], "description": "",
        "ai_summary": "", "thumbnail": book["thumbnail"], "page_count": 0,
        "publisher": book["publisher"], "published_date": book["published_date"],
        "categories": book["categories"], "confidence": "high",
        "catalogue_id": record_id, "isbn_13": book["isbn_13"],
    })
    history_id = save_history(current_user["id"], book_id)
    update_history_reading(current_user["id"], history_id, status, "")
    return jsonify({"book_id": book_id, "history_id": history_id,
                    "reading_status": status,
                    "profile_books": len(get_taste_profile_books(current_user["id"]))})


@app.route("/api/catalogue/<int:record_id>", methods=["GET"])
@token_required
def catalogue_detail(current_user, record_id):
    """One verified book, with the same "Is this for you?" evidence as a scan."""
    row = get_catalogue_book(record_id)
    if row is None or row["verification_status"] != "VERIFIED":
        return jsonify({"error": "Book not found"}), 404
    book = catalogue_for_reader(row)
    book["summary"] = (row["short_summary"] or "").strip()
    return jsonify({
        "book": book,
        "for_you": taste_for_client(current_user["id"], book["categories"],
                                    None, book["title"]),
        "already_read": already_read(current_user["id"], book["title"],
                                     book.get("author", "")),
    })


@app.route("/api/for-you", methods=["POST"])
@token_required
def for_you(current_user):
    """Re-answer "Is this for you?", and offer the books worth asking about.

    Reads; never writes. The starter shelf writes through the existing
    /api/catalogue/<id>/read route, so the two halves stay separable: this one
    can be called as often as the panel needs without touching a library.

    Keyed on the SUBJECTS rather than on a book id because the confirm card
    computes for_you for a candidate that has not been saved yet -- there is no
    id to key on at the moment the panel is on screen. The subjects came from
    us in the first place, and they are only ever compared against this
    reader's own history, so nothing here can reach another account.
    """
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()[:300]
    categories = payload.get("categories") or ""
    if isinstance(categories, (list, tuple)):
        categories = [str(c)[:60] for c in categories[:40]]
    else:
        categories = str(categories)[:2000]

    try:
        book_id = int(payload.get("book_id"))
    except (TypeError, ValueError):
        book_id = None

    # Which catalogue ids the panel has already shown. "I have not read any of
    # these" is only honest if the next six are actually different ones.
    seen = set()
    for value in (payload.get("exclude_ids") or [])[:60]:
        try:
            seen.add(int(value))
        except (TypeError, ValueError):
            continue

    wanted = payload.get("want_starters")
    wanted = 6 if wanted is None else max(0, min(int(wanted or 0), 12))

    assessment = taste_for_client(current_user["id"], categories, book_id, title)

    starters, targeted = [], False
    # The shelf belongs to the two states it can actually resolve. A book with
    # no subjects is the PUBLISHER's gap: no amount of reading history answers
    # it, so offering books to tap there would promise something untrue.
    offerable = (taste_profile.STATE_COLD_START,
                 taste_profile.STATE_INTEREST_MATCH)
    if wanted and assessment["state"] in offerable:
        counts, catalogue_size = catalogue_subject_counts()
        rows = [dict(r) for r in list_catalogue("VERIFIED")
                if r["id"] not in seen]
        # Never offer a book this reader has already told us about -- tapping
        # it would change nothing and would look broken.
        mine = [r["title"] for r in get_taste_profile_books(current_user["id"])]
        picked, targeted = taste_profile.starter_candidates(
            categories, rows, exclude_titles=[title] + mine, limit=wanted,
            subject_counts=counts, catalogue_size=catalogue_size)
        starters = [catalogue_for_reader(r) for r in picked]

    return jsonify({"for_you": assessment, "starters": starters,
                    "targeted": targeted})


@app.route("/api/closest", methods=["GET"])
@token_required
def closest_on_our_shelf(current_user):
    """The nearest books on our own shelf to what this reader has read.

    Exists for the moment identification refuses. "I do not know what this is"
    is the honest answer and it leaves the reader holding nothing, which is a
    poor place to end -- the profile that answers "is this for you?" can also
    answer "then what here is close to what you like", and it is the same
    arithmetic pointed backwards. See taste_profile.closest_from_shelf.

    Deliberately NOT called recommendations, and deliberately not derived from
    any other account: 10 users and 23 history rows cannot support that, and
    pretending otherwise would be the one claim in this product that no
    measurement backs.
    """
    counts, catalogue_size = catalogue_subject_counts()
    history = [dict(r) for r in get_taste_profile_books(current_user["id"])]
    picked = taste_profile.closest_from_shelf(
        history, [dict(r) for r in list_catalogue("VERIFIED")],
        subject_counts=counts, catalogue_size=catalogue_size)

    books = []
    for item in picked:
        book = catalogue_for_reader(item["book"])
        # The reason travels with the book. A suggestion the reader cannot
        # check is exactly the kind of claim this product does not make.
        book["reason"] = item["reason"]
        book["because"] = item["because"]
        books.append(book)
    return jsonify({"books": books, "profile_books": len(history)})


@app.route("/api/books/<int:book_id>/summary", methods=["GET"])
@token_required
def book_summary_status(current_user, book_id):
    # Polling endpoint for the async summary: the scan/search response says
    # summary_status="pending", and the frontend asks here every few seconds
    # until the background worker has written the summary.
    row = get_book_by_id(book_id)
    if row is None:
        return jsonify({"error": "Book not found"}), 404
    book = dict(row)
    summary = (book.get("ai_summary") or "").strip()
    if summary:
        return jsonify({"status": "ready", "summary": summary,
                        "verified_summary": book.get("verified_summary", ""),
                        "description": book.get("description", ""),
                        "description_source": book.get("description_source") or None})
    if not book.get("catalogue_id") and book.get("summary_status") == "pending":
        with _summary_lock:
            inflight = book_id in _summary_inflight
        if not inflight:
            enqueue_summary(book_id, book)
        return jsonify({"status": "pending",
                        "summary_trust": "EXTERNAL_NOT_VERIFIED"})
    if not book.get("catalogue_id") and \
            book.get("summary_status") == "model_unavailable":
        return jsonify({"status": "model_unavailable",
                        "summary_trust": "EXTERNAL_NOT_VERIFIED",
                        "description": book.get("description", ""),
                        "description_source": book.get("description_source") or None,
                        "reason": book.get("description_reason") or "",
                        "message": "The external overview is temporarily unavailable."})
    reason = book.get("description_reason") or ""
    if reason == "non_english_source":
        message = ("The exact provider description is not in English, and no "
                   "exact English ISBN/work description was available.")
    elif reason == "source_language_uncertain":
        message = ("The source language could not be verified, so BookLens "
                   "did not present it as an English overview.")
    else:
        message = "Stored short summary is not available for this book."
    return jsonify({
        "status": "unavailable",
        "reason": reason or "stored_short_summary_missing",
        "description": book.get("description", ""),
        "description_source": book.get("description_source") or None,
        "message": message,
    })
    if False:
        # Not queued and not done — the server restarted mid-queue or the
        # job failed. Queue it again so the summary self-heals.
        pass


@app.route("/api/search-by-title", methods=["POST"])
@rate_limited(15, 60)
@token_required
def search_by_title_verified(current_user):
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    author = str(data.get("author") or "").strip()
    isbn = normalize_isbn(str(data.get("isbn") or ""))
    if not title and not isbn:
        return jsonify({"error": "Please type a book title"}), 400
    if len(title) > 240 or len(author) > 160 or len(isbn) > 13:
        return jsonify({"error": "Input too long"}), 400
    if isbn and not valid_isbn(isbn):
        return jsonify({"error": "Please enter a valid ISBN-10 or ISBN-13"}), 400

    evidence = {
        "query_title": title, "query_author": author, "query_isbn": isbn,
        "ocr_status": "", "ocr_title": "", "ocr_author": "",
        "ocr_text": "", "ocr_confidence": 0,
    }
    ranked = retrieve_tiered_candidates(title, author, isbn, title)
    return begin_candidate_funnel(current_user, evidence, ranked, "manual")


@app.route("/api/identify/confirm", methods=["POST"])
@rate_limited(20, 60)
@token_required
def confirm_identification(current_user):
    data = request.get_json(silent=True) or {}
    try:
        attempt_id = int(data.get("attempt_id"))
        candidate_id = int(data.get("candidate_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid attempt and candidate are required"}), 400
    return finalize_candidate(current_user, attempt_id, candidate_id,
                              "user_confirmation")


def create_default_admin():
    # Make sure one admin account always exists so the dashboard can be used.
    # SECURITY: there is NO hardcoded default password. The password comes
    # from .env; if it is missing we generate a strong random one and save
    # it there (the owner reads it from the .env file).
    admin_password = get_or_create_secret("ADMIN_PASSWORD", secrets.token_urlsafe(10))
    admin = get_user_by_email("admin@bookai.com")
    if admin is None:
        create_user(
            "Administrator",
            "admin@bookai.com",
            generate_password_hash(admin_password),
            is_admin=1,
            # Nobody is going to open a confirmation link for this account:
            # it is created by the server, for the server's owner.
            email_verified=1
        )
        logging.info("Default admin created -> email: admin@bookai.com "
                     "(password is in the .env file)")
    elif check_password_hash(admin["password_hash"], "admin123"):
        # This database still has the old insecure default password from an
        # earlier version -> replace it with the strong one from .env.
        update_user_password(admin["id"], generate_password_hash(admin_password))
        logging.warning("Admin password 'admin123' was replaced -> "
                        "new password is in the .env file")
    admin = get_user_by_email("admin@bookai.com")
    if admin is not None and not is_email_verified(admin):
        # Upgrading an existing installation: this row predates verification.
        mark_email_verified(admin["id"])


# ----- Start the server -----
if __name__ == "__main__":
    init_db()                # create database tables
    create_default_admin()   # make sure the admin account exists
    logging.info("Starting server...")
    # SECURITY: debug mode is OFF by default. Flask's debug mode shows an
    # interactive debugger in the browser on errors, which would let anyone
    # run Python code on our server. Enable it only for development by
    # setting the environment variable FLASK_DEBUG=1.
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Which network interface to listen on. The default stays loopback, so
    # nothing is exposed unless it is asked for. Set HOST=0.0.0.0 to also
    # answer on the local network — that is what a PHONE needs: the core use
    # case is photographing a cover, and the frontend builds its API URL from
    # window.location.hostname, so a phone at http://192.168.x.x:8080 calls
    # http://192.168.x.x:5000/api. Binding only to 127.0.0.1 refused those
    # calls even though the CORS allowlist above deliberately permits
    # private-LAN origins. In Docker the published port likewise reaches
    # nothing unless this is set.
    host = os.environ.get("HOST", "127.0.0.1")

    # Cloud hosts (Render, Railway) inject the port they health-check and
    # mark the deploy FAILED if nothing answers there, even when the app is
    # running. Default stays 5000 so local behaviour is unchanged.
    port = int(os.environ.get("PORT", "5000"))

    if debug_mode:
        # Development only: Flask's reloader + debugger.
        app.run(debug=True, host=host, port=port)
    else:
        # Production-grade WSGI server (waitress): multi-threaded, no dev
        # server warning, and it does not fall over under a burst of
        # requests the way app.run() does. threads=8: scans are long
        # (OCR + APIs + model), so a few threads keep light requests
        # (login, history) responsive while scans run.
        from waitress import serve
        logging.info("Serving with waitress on http://%s:%s", host, port)
        if host == "0.0.0.0":
            logging.info("Reachable from other devices on this network "
                         "(phone: use this machine's LAN IP on port 8080)")
        serve(app, host=host, port=port, threads=8)
