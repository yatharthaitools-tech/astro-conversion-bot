"""dashboard/routes.py's /admin/conversations/export-messages.csv — the
regular conversations CSV export is one row per conversation (summary
only: turns, rating, etc.), which can't hold a variable-length
transcript. This is the actual chat content, one row per message, same
language/date filters as the Conversations page itself.
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


def test_export_includes_every_message_not_just_a_summary_row(client):
    _login_admin(client)
    dashboard_db.record_turn("sess-1", "u1", "when will astro come online", "Soon!", "en", "agent", [], card_shown=True)

    resp = client.get("/admin/conversations/export-messages.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.get_data(as_text=True)

    assert "Session,User,Role,Text,Source,Card Shown,Created At" in body
    assert "when will astro come online" in body
    assert "Soon!" in body
    # Two message rows (user + bot) for this one conversation, not one
    # summary row like the regular conversations export.
    assert body.count("sess-1") == 2


def test_export_respects_language_filter(client):
    _login_admin(client)
    dashboard_db.record_turn("sess-en", "u1", "hello", "hi", "en", "agent", [], card_shown=False)
    dashboard_db.record_turn("sess-hi", "u2", "namaste", "namaste", "hi", "agent", [], card_shown=False)

    resp = client.get("/admin/conversations/export-messages.csv?language=hi")
    body = resp.get_data(as_text=True)
    assert "namaste" in body
    assert "hello" not in body
