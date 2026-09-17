"""dashboard/db.py calls psycopg2.connect() directly (no SQLAlchemy), which
rejects the SQLAlchemy-style 'postgresql+psycopg2://' scheme our ops team's
connection strings for other services use ('invalid dsn: missing "=" after
...') — confirmed by hand against a real Postgres instance before this test
was written. _normalize_database_url() strips the '+driver' suffix so a
string copied straight from that template still works.
"""
from dashboard.db import _normalize_database_url


def test_strips_psycopg2_driver_suffix():
    assert (
        _normalize_database_url("postgresql+psycopg2://u:p@host:5432/db")
        == "postgresql://u:p@host:5432/db"
    )


def test_strips_driver_suffix_from_postgres_scheme():
    assert (
        _normalize_database_url("postgres+psycopg2://u:p@host:5432/db")
        == "postgres://u:p@host:5432/db"
    )


def test_leaves_plain_url_unchanged():
    url = "postgresql://u:p@host:5432/db"
    assert _normalize_database_url(url) == url
