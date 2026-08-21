"""Every admin tab must load, and only for an admin.

The dashboard has seven tabs backed by these endpoints. One of them —
/api/admin/system — returned 500 in the deployable build because it imported
summarizer.py to report whether the FLAN-T5 checkpoint had loaded, and that
module is deliberately not shipped. Nothing caught it, because the tests only
exercised the endpoints the user-facing flow touches.
"""
import database

from test_api_flows import auth, client, register_and_login  # noqa: F401

ADMIN_ENDPOINTS = (
    "/api/admin/stats",
    "/api/admin/users",
    "/api/admin/books",
    "/api/admin/messages",
    "/api/admin/catalogue",
    "/api/admin/identifications",
    "/api/admin/audit-logs",
    "/api/admin/system",
)


def admin_token(client):
    from werkzeug.security import generate_password_hash
    database.create_user("Admin", "boss@example.com",
                         generate_password_hash("adminpass123"), is_admin=1,
                         email_verified=1)
    return client.post("/api/login", json={"email": "boss@example.com",
                                           "password": "adminpass123"}).get_json()["token"]


def test_every_admin_tab_loads(client):
    token = admin_token(client)
    for endpoint in ADMIN_ENDPOINTS:
        response = client.get(endpoint, headers=auth(token))
        assert response.status_code == 200, "%s -> %s" % (endpoint, response.status_code)


def test_the_system_tab_does_not_need_the_summarizer(client):
    # The regression: it imported a module the deployable build does not ship.
    token = admin_token(client)
    data = client.get("/api/admin/system", headers=auth(token)).get_json()
    assert data["summarizer_shipped"] is False
    assert data["external_overview_method"]
    assert "catalogue" in data


def test_a_normal_user_cannot_reach_the_dashboard(client):
    token = register_and_login(client)
    for endpoint in ADMIN_ENDPOINTS:
        response = client.get(endpoint, headers=auth(token))
        assert response.status_code in (401, 403), \
            "%s leaked to a normal user (%s)" % (endpoint, response.status_code)


def test_the_dashboard_needs_a_token_at_all(client):
    for endpoint in ADMIN_ENDPOINTS:
        assert client.get(endpoint).status_code in (401, 403)
