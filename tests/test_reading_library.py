import database
from test_api_flows import auth, client, register_and_login  # noqa: F401


def _history_item(client, token):
    book_id = database.save_book({
        "title": "Dune", "author": "Frank Herbert", "summary_status": "unavailable"
    })
    user = database.get_user_by_email("reader@example.com")
    return database.save_history(user["id"], book_id)


def test_user_can_save_reading_status_and_private_note(client):
    token = register_and_login(client)
    history_id = _history_item(client, token)
    response = client.patch(
        f"/api/history/{history_id}/reading", headers=auth(token),
        json={"reading_status": "want_to_read",
              "private_note": "Read after the semester."},
    )
    assert response.status_code == 200
    assert response.get_json()["reading_status"] == "want_to_read"
    history = client.get("/api/history", headers=auth(token)).get_json()
    assert history[0]["private_note"] == "Read after the semester."


def test_reading_update_validates_status_and_note_length(client):
    token = register_and_login(client)
    history_id = _history_item(client, token)
    invalid = client.patch(
        f"/api/history/{history_id}/reading", headers=auth(token),
        json={"reading_status": "abandoned", "private_note": ""},
    )
    assert invalid.status_code == 400
    too_long = client.patch(
        f"/api/history/{history_id}/reading", headers=auth(token),
        json={"reading_status": "reading", "private_note": "x" * 1001},
    )
    assert too_long.status_code == 400


def test_user_cannot_update_another_users_history(client):
    first_token = register_and_login(client)
    history_id = _history_item(client, first_token)
    second_token = register_and_login(client, "second@example.com")
    response = client.patch(
        f"/api/history/{history_id}/reading", headers=auth(second_token),
        json={"reading_status": "finished", "private_note": "not mine"},
    )
    assert response.status_code == 404
