"""The signup link, the reset link, and what they must refuse.

These drive the real flow: the mail sender is replaced with a recorder, and
every test pulls the link out of the message body exactly as a reader would
take it out of an inbox. Nothing here marks an account verified by hand --
that shortcut lives in test_api_flows.register_and_login, for the tests that
are about something else entirely.
"""
import pytest

import app as app_module
import database
from test_api_flows import auth, client  # noqa: F401


@pytest.fixture()
def outbox(monkeypatch):
    sent = []

    def recorder(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return "sent"

    monkeypatch.setattr(app_module.mailer, "send_mail", recorder)
    return sent


def link_in(message):
    for word in message["body"].split():
        if word.startswith("http"):
            return word
    raise AssertionError("no link in message: %r" % message["body"])


def path_of(link):
    return link[link.index("/", len("https://")):]


def token_of(link):
    return link.split("token=", 1)[1]


def signup(client, email="new@example.com", password="strongpass"):
    return client.post("/api/register", json={
        "name": "New Reader", "email": email, "password": password})


# ----- verification -----

def test_signup_leaves_the_account_locked_until_the_link_is_opened(client, outbox):
    assert signup(client).status_code == 202

    refused = client.post("/api/login", json={"email": "new@example.com",
                                              "password": "strongpass"})
    assert refused.status_code == 403
    assert refused.get_json()["code"] == "email_unverified"

    assert len(outbox) == 1
    opened = client.get(path_of(link_in(outbox[0])))
    assert opened.status_code == 302
    assert "state=ok" in opened.headers["Location"]

    allowed = client.post("/api/login", json={"email": "new@example.com",
                                              "password": "strongpass"})
    assert allowed.status_code == 200
    assert allowed.get_json()["token"]


def test_the_verification_link_works_only_once(client, outbox):
    signup(client)
    path = path_of(link_in(outbox[0]))
    assert "state=ok" in client.get(path).headers["Location"]
    # A link that stayed live would keep working from a forwarded email long
    # after the account it opened had changed hands.
    assert "state=invalid" in client.get(path).headers["Location"]


def test_an_expired_link_is_refused(client, outbox):
    signup(client)
    user = database.get_user_by_email("new@example.com")
    database.create_auth_token(user["id"], "verify", "stale-token", -1)
    assert database.consume_auth_token("stale-token", "verify") is None
    assert not database.is_email_verified(
        database.get_user_by_email("new@example.com"))


def test_a_token_cannot_be_spent_on_the_other_purpose(client, outbox):
    signup(client)
    token = token_of(link_in(outbox[0]))
    # Confirming an address and taking over an account are not the same act
    # and must not share a key.
    assert database.consume_auth_token(token, "reset") is None


def test_the_raw_token_is_never_stored(client, outbox):
    signup(client)
    token = token_of(link_in(outbox[0]))
    conn = database.get_db()
    rows = conn.execute("SELECT token_hash FROM auth_tokens").fetchall()
    conn.close()
    assert rows
    assert all(row["token_hash"] != token for row in rows)


# ----- account enumeration -----

def test_registering_a_known_address_is_indistinguishable(client, outbox):
    signup(client, email="taken@example.com")
    client.get(path_of(link_in(outbox[0])))
    outbox.clear()

    first = signup(client, email="brand-new@example.com")
    again = signup(client, email="taken@example.com")

    # Same status, same body: the response says nothing about which address
    # already has an account.
    assert first.status_code == again.status_code == 202
    assert first.get_json()["message"] == again.get_json()["message"]
    assert first.get_json()["status"] == again.get_json()["status"]

    # The owner is told, because the inbox is the one place only they can read.
    notices = [m for m in outbox if m["to"] == "taken@example.com"]
    assert len(notices) == 1
    assert "already have one" in notices[0]["body"]


def test_resend_is_silent_about_unknown_addresses(client, outbox):
    response = client.post("/api/resend-verification",
                           json={"email": "nobody@example.com"})
    assert response.status_code == 202
    assert outbox == []


def test_forgot_password_says_the_same_thing_for_an_unknown_address(client, outbox):
    response = client.post("/api/forgot-password",
                           json={"email": "ghost@example.com"})
    assert response.status_code == 202
    assert outbox == []


def test_the_reported_delivery_state_does_not_reveal_the_address(client, monkeypatch):
    """Caught by driving the real server, not by any test written before it.

    With no mail provider configured, a genuine send reports "logged". The
    branches that deliberately send nothing were returning a hardcoded "sent",
    so the two responses differed by exactly one field -- and that field
    answered the only question this design exists to refuse: does this address
    have an account?
    """
    monkeypatch.delenv("MAIL_PROVIDER", raising=False)
    monkeypatch.delenv("MAIL_API_KEY", raising=False)

    signup(client, email="real@example.com")
    known = client.post("/api/forgot-password", json={"email": "real@example.com"})
    unknown = client.post("/api/forgot-password", json={"email": "ghost@example.com"})
    assert known.get_json() == unknown.get_json()

    known_signup = signup(client, email="real@example.com")
    fresh_signup = signup(client, email="other@example.com")
    assert known_signup.get_json() == fresh_signup.get_json()


# ----- password reset -----

def test_reset_sets_the_password_and_ends_existing_sessions(client, outbox):
    signup(client, email="reset@example.com")
    client.get(path_of(link_in(outbox[0])))
    token = client.post("/api/login",
                        json={"email": "reset@example.com",
                              "password": "strongpass"}).get_json()["token"]
    assert client.get("/api/profile", headers=auth(token)).status_code == 200
    outbox.clear()

    assert client.post("/api/forgot-password",
                       json={"email": "reset@example.com"}).status_code == 202
    reset_token = token_of(link_in(outbox[0]))

    done = client.post("/api/reset-password",
                       json={"token": reset_token, "password": "a-new-password"})
    assert done.status_code == 200

    # Whoever asked for the reset may be locking somebody else out on purpose,
    # so the old session has to die with the old password.
    assert client.get("/api/profile", headers=auth(token)).status_code == 401
    assert client.post("/api/login",
                       json={"email": "reset@example.com",
                             "password": "strongpass"}).status_code == 401
    assert client.post("/api/login",
                       json={"email": "reset@example.com",
                             "password": "a-new-password"}).status_code == 200


def test_a_reset_link_works_only_once(client, outbox):
    signup(client, email="once@example.com")
    client.get(path_of(link_in(outbox[0])))
    outbox.clear()
    client.post("/api/forgot-password", json={"email": "once@example.com"})
    reset_token = token_of(link_in(outbox[0]))

    assert client.post("/api/reset-password",
                       json={"token": reset_token,
                             "password": "first-new-pass"}).status_code == 200
    second = client.post("/api/reset-password",
                         json={"token": reset_token,
                               "password": "second-new-pass"})
    assert second.status_code == 400
    assert second.get_json()["code"] == "reset_link_invalid"


def test_a_short_password_is_refused_before_the_token_is_spent(client, outbox):
    signup(client, email="short@example.com")
    client.get(path_of(link_in(outbox[0])))
    outbox.clear()
    client.post("/api/forgot-password", json={"email": "short@example.com"})
    reset_token = token_of(link_in(outbox[0]))

    assert client.post("/api/reset-password",
                       json={"token": reset_token,
                             "password": "tiny"}).status_code == 400
    # The link must survive a rejected attempt, or one typo costs the reader
    # the only way back into their account.
    assert client.post("/api/reset-password",
                       json={"token": reset_token,
                             "password": "long-enough-now"}).status_code == 200


# ----- the accounts the server creates for itself -----

def test_seeded_accounts_are_usable_without_a_mailbox(client):
    from werkzeug.security import generate_password_hash
    database.create_user("Administrator", "admin@bookai.com",
                         generate_password_hash("adminpass123"), is_admin=1,
                         email_verified=1)
    assert client.post("/api/login",
                       json={"email": "admin@bookai.com",
                             "password": "adminpass123"}).status_code == 200
