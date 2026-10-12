"""dashboard/routes.py's `ist` Jinja filter — every timestamp column is
stored as UTC (dashboard/db.py's _now()), but the admin dashboard's own
templates used to truncate the ISO string (x[:16].replace('T', ' ')),
which silently printed raw UTC clock digits as if they were already
local time. The admin team is India-based and expects IST (UTC+5:30).
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from dashboard import db as dashboard_db
from dashboard.routes import format_ist


def test_ist_filter_shifts_utc_to_ist():
    # 07:52 UTC -> 13:22 IST (+5:30), still the same calendar day here.
    assert format_ist("2026-10-12T07:52:02+00:00") == "2026-10-12 13:22"


def test_ist_filter_can_roll_over_to_the_next_calendar_day():
    # 19:00 UTC -> 00:30 IST the NEXT day — exactly the case the old
    # x[:16] slicing got wrong (it would've shown "19:00" on the 11th,
    # not "00:30" on the 12th).
    assert format_ist("2026-10-11T19:00:00+00:00") == "2026-10-12 00:30"


def test_ist_filter_accepts_a_custom_format():
    assert format_ist("2026-10-12T07:52:02+00:00", "%Y-%m-%d") == "2026-10-12"


def test_ist_filter_handles_naive_timestamps_as_utc():
    # Defensive: a string with no timezone offset is treated as UTC,
    # same as every timestamp this app actually produces.
    assert format_ist("2026-10-12T07:52:02") == "2026-10-12 13:22"


def test_ist_filter_handles_blank_value():
    assert format_ist(None) == ""
    assert format_ist("") == ""


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE messages, conversations, admin_users CASCADE")
    yield


def test_conversations_page_shows_ist_not_raw_utc():
    dashboard_db.create_admin_user("admin@x.com", generate_password_hash("pass12345"), "admin")
    dashboard_db.record_turn("sess-1", "u1", "q", "a", "en", "agent", [], card_shown=False)
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE conversations SET first_seen_at = '2026-10-12T07:52:00+00:00' WHERE session_id = 'sess-1'")

    client = app_module.app.test_client()
    client.post("/admin/login", data={"email": "admin@x.com", "password": "pass12345"})
    resp = client.get("/admin/conversations")
    body = resp.get_data(as_text=True)

    assert "13:22" in body
    assert "07:52" not in body
