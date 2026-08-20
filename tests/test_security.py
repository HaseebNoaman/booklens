import io
from pathlib import Path

from test_api_flows import auth, client, png_bytes, register_and_login  # noqa: F401


def test_invalid_login_is_generic(client):
    register_and_login(client)
    response = client.post("/api/login", json={"email": "missing@example.com",
                                                "password": "wrongpass"})
    assert response.status_code == 401
    assert response.get_json()["error"] == "Invalid email or password"


def test_disguised_non_image_is_rejected(client):
    token = register_and_login(client)
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (io.BytesIO(b"not an image"), "cover.jpg")},
                           content_type="multipart/form-data")
    assert response.status_code == 400
    assert "readable image" in response.get_json()["error"]


def test_invalid_extension_is_rejected(client):
    token = register_and_login(client)
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (png_bytes(), "cover.txt")},
                           content_type="multipart/form-data")
    assert response.status_code == 400


def test_security_headers_are_present(client):
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_built_frontend_is_served_with_browser_csp(client, tmp_path, monkeypatch):
    import app as app_module

    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>BookLens</main>", encoding="utf-8")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", str(frontend))
    response = client.get("/")
    assert response.status_code == 200
    assert b"BookLens" in response.data
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_protected_route_requires_token(client):
    assert client.get("/api/history").status_code == 401


def test_logout_invalidates_the_server_side_session(client):
    token = register_and_login(client)
    assert client.get("/api/history", headers=auth(token)).status_code == 200
    assert client.post("/api/logout", headers=auth(token)).status_code == 200
    response = client.get("/api/history", headers=auth(token))
    assert response.status_code == 401
    assert response.get_json()["code"] == "session_ended"


def test_password_change_invalidates_old_token(client):
    token = register_and_login(client)
    changed = client.post("/api/profile/password", headers=auth(token), json={
        "current_password": "strongpass", "new_password": "newstrongpass"
    })
    assert changed.status_code == 200
    assert changed.get_json()["reauthenticate"] is True
    assert client.get("/api/profile", headers=auth(token)).status_code == 401
    assert client.post("/api/login", json={
        "email": "reader@example.com", "password": "newstrongpass"
    }).status_code == 200


def test_rate_limit_response_includes_retry_after(client):
    import app as app_module

    app_module._rate_buckets.clear()

    @app_module.rate_limited(1, 60)
    def limited_action():
        return "ok"

    old_testing = app_module.app.config["TESTING"]
    app_module.app.config["TESTING"] = False
    try:
        with app_module.app.test_request_context("/limited", environ_base={"REMOTE_ADDR": "203.0.113.10"}):
            assert limited_action() == "ok"
            response = limited_action()
            assert response.status_code == 429
            assert response.headers["Retry-After"]
            assert response.get_json()["code"] == "rate_limited"
    finally:
        app_module.app.config["TESTING"] = old_testing
        app_module._rate_buckets.clear()


def test_sql_like_input_is_stored_as_data(client):
    response = client.post("/api/register", json={
        "name": "Robert'); DROP TABLE users;--",
        "email": "safe@example.com", "password": "strongpass"})
    assert response.status_code == 201
    assert client.post("/api/login", json={"email": "safe@example.com",
                                           "password": "strongpass"}).status_code == 200


def test_oversized_upload_is_rejected(client):
    token = register_and_login(client)
    client.application.config["MAX_CONTENT_LENGTH"] = 128
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (io.BytesIO(b"x" * 1024), "cover.jpg")},
                           content_type="multipart/form-data")
    assert response.status_code == 413


def test_unsafe_filename_is_not_retained(client, monkeypatch):
    import app as app_module
    token = register_and_login(client)
    monkeypatch.setattr(app_module, "process_book_cover", lambda *a, **k: {
        "probable_title": "", "probable_author": "", "full_text": "",
        "confidence_score": 0.0})
    response = client.post("/api/scan", headers=auth(token),
                           data={"image": (png_bytes(), "../../unsafe cover.png")},
                           content_type="multipart/form-data")
    assert response.status_code == 200
    assert not list(Path(app_module.UPLOAD_FOLDER).glob("*unsafe*"))
