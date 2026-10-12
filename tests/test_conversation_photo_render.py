"""Admin conversation detail page renders an actual <img> for a shared
photo instead of the raw '[Shared a photo: <presigned-s3-url>]' marker
text (which exposed the full presigned URL, query-string signature
included, as plain text in the transcript). dashboard/routes.py's
_with_photo_urls extracts the URL the same way app.py's own
_ATTACHMENT_MARKER_RE does for the live chat.
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from dashboard import db as dashboard_db


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE messages, conversations, admin_users CASCADE")
    yield


@pytest.fixture
def client():
    return app_module.app.test_client()


def _login_admin(client):
    dashboard_db.create_admin_user("admin@x.com", generate_password_hash("pass12345"), "admin")
    client.post("/admin/login", data={"email": "admin@x.com", "password": "pass12345"})


def test_shared_photo_renders_as_image_not_raw_marker(client):
    _login_admin(client)
    photo_url = "https://astrolokal.s3.amazonaws.com/uploads/dad147.jpg?X-Amz-Signature=abc123"
    dashboard_db.record_turn(
        "sess-photo", "u1", f"[Shared a photo: {photo_url}]", "Nice photo!",
        "en", "agent", [], card_shown=False,
    )

    resp = client.get("/admin/conversations/sess-photo")
    body = resp.get_data(as_text=True)

    assert f'<img src="{photo_url}"' in body
    # The raw marker text (with the full signed URL as plain visible
    # text) must not appear anywhere outside that img src attribute.
    assert "[Shared a photo:" not in body


def test_normal_text_message_unaffected(client):
    _login_admin(client)
    dashboard_db.record_turn(
        "sess-text", "u1", "when will astro come online", "Soon!",
        "en", "agent", [], card_shown=False,
    )

    resp = client.get("/admin/conversations/sess-text")
    body = resp.get_data(as_text=True)

    assert "when will astro come online" in body
    assert "<img" not in body
