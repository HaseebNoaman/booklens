# mailer.py
# Sends the two emails this app has: "confirm your address" and "reset your
# password". Nothing else goes out, so this file stays deliberately small.
#
# WHY AN HTTPS API AND NOT smtplib.
# The obvious choice is smtplib, and it is the wrong one here. Free hosting
# platforms routinely block outbound ports 25/465/587 to stop spam from free
# accounts, and when they do, smtplib does not fail quickly -- it hangs until
# the socket times out, so a signup that should take 200ms takes 30 seconds and
# then fails anyway. Port 443 is never blocked, because blocking it would break
# the platform itself. So the default path is an ordinary HTTPS POST to a mail
# provider. SMTP is still supported for anyone running on their own server,
# where it works fine.
#
# WHAT HAPPENS WITH NO PROVIDER CONFIGURED.
# Local development gets "logged": the full link is printed to the log and the
# caller is told the truth, that nothing was actually sent. That keeps the
# whole verification flow testable on a laptop with no account anywhere. What
# it must never do is quietly return success -- see app.py, which shows the
# user a different message for "sent" than for "logged" or "failed".
import logging
import os
import smtplib
from email.message import EmailMessage

import requests

# Timeouts are short on purpose. A signup request is waiting on this call, and
# a mail provider that has not answered in 10 seconds is not about to.
TIMEOUT = 10


def base_url():
    """Where the links in the email should point.

    Falls back to the local server so a developer's link is clickable; in
    production APP_BASE_URL is the deployed origin.
    """
    return os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def _from_address():
    return os.environ.get("MAIL_FROM", "BookLens <onboarding@resend.dev>")


def _send_resend(to, subject, body, api_key):
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": "Bearer %s" % api_key},
        json={"from": _from_address(), "to": [to],
              "subject": subject, "text": body},
        timeout=TIMEOUT,
    )
    if response.status_code < 300:
        return True, ""
    return False, "resend http %s: %s" % (response.status_code, response.text[:200])


def _send_brevo(to, subject, body, api_key):
    sender = _from_address()
    # Brevo wants the name and address as separate fields, so split
    # "BookLens <x@y.z>" if that is the shape we were given.
    name, address = "BookLens", sender
    if "<" in sender and sender.endswith(">"):
        name, address = sender.split("<", 1)[0].strip() or "BookLens", sender.split("<", 1)[1][:-1]
    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": api_key, "accept": "application/json"},
        json={"sender": {"name": name, "email": address},
              "to": [{"email": to}], "subject": subject, "textContent": body},
        timeout=TIMEOUT,
    )
    if response.status_code < 300:
        return True, ""
    return False, "brevo http %s: %s" % (response.status_code, response.text[:200])


def _send_smtp(to, subject, body):
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")

    message = EmailMessage()
    message["From"] = _from_address()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=TIMEOUT) as server:
        server.starttls()
        if user:
            server.login(user, password)
        server.send_message(message)
    return True, ""


def would_send():
    """What send_mail() would report if it were called right now.

    The routes that deliberately send NOTHING -- a reset requested for an
    address with no account, a resend for one already confirmed -- still have
    to report a delivery state, and it must be the same state a real send
    would have produced. Returning a hardcoded "sent" while a configured-less
    server returns "logged" for real addresses is exactly the difference an
    attacker needs to tell the two apart, which is the leak this whole design
    exists to close.
    """
    provider = os.environ.get("MAIL_PROVIDER", "").strip().lower()
    api_key = os.environ.get("MAIL_API_KEY", "").strip()
    if not provider or (provider in ("resend", "brevo") and not api_key):
        return "logged"
    return "sent"


def send_mail(to, subject, body):
    """Send one email.

    Returns "sent", "logged" or "failed" -- three outcomes, not a boolean,
    because the caller shows the user something different for each. Collapsing
    "logged" into "sent" would tell a real user to check an inbox that will
    never receive anything.
    """
    provider = os.environ.get("MAIL_PROVIDER", "").strip().lower()
    api_key = os.environ.get("MAIL_API_KEY", "").strip()

    if not provider or (provider in ("resend", "brevo") and not api_key):
        logging.warning(
            "MAIL NOT CONFIGURED -- not sending. To: %s | %s\n%s",
            to, subject, body)
        return "logged"

    try:
        if provider == "resend":
            ok, detail = _send_resend(to, subject, body, api_key)
        elif provider == "brevo":
            ok, detail = _send_brevo(to, subject, body, api_key)
        elif provider == "smtp":
            ok, detail = _send_smtp(to, subject, body)
        else:
            logging.error("Unknown MAIL_PROVIDER %r -- not sending.", provider)
            return "failed"
    except Exception as exc:                                   # noqa: BLE001
        # Any provider failure is one outcome to the caller: nothing was sent.
        # Logged at ERROR because a silent mail outage looks exactly like a
        # working app until users start saying they never got the email.
        logging.error("Mail send failed via %s: %s", provider, exc)
        return "failed"

    if ok:
        return "sent"
    logging.error("Mail send failed: %s", detail)
    return "failed"


def verification_email(name, link):
    subject = "Confirm your BookLens address"
    body = (
        "Hi %s,\n\n"
        "Confirm this address to finish setting up your BookLens account:\n\n"
        "%s\n\n"
        "The link works once and expires in 24 hours.\n\n"
        "If you did not sign up, you can ignore this message -- no account "
        "will be usable without this confirmation.\n"
    ) % (name or "there", link)
    return subject, body


def existing_account_email(name):
    """Sent when somebody tries to sign up with an address that already works.

    The signup response cannot say "that address is taken" without leaking who
    has an account here, so the notice goes where only the owner can read it.
    """
    subject = "Someone tried to sign up with your address"
    body = (
        "Hi %s,\n\n"
        "Somebody just tried to create a BookLens account using this address. "
        "You already have one, so nothing was changed and no new account was "
        "made.\n\n"
        "If that was you, just sign in as normal. If it was not, you can "
        "safely ignore this -- whoever it was cannot see your account or use "
        "your address.\n"
    ) % (name or "there",)
    return subject, body


def reset_email(name, link):
    subject = "Reset your BookLens password"
    body = (
        "Hi %s,\n\n"
        "Use this link to choose a new password:\n\n"
        "%s\n\n"
        "The link works once and expires in 1 hour. Signing in again "
        "everywhere else will be required afterwards.\n\n"
        "If you did not ask for this, ignore this message and your password "
        "stays as it is.\n"
    ) % (name or "there", link)
    return subject, body
