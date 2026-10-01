"""Per-person admin dashboard logins (email + password) with role-based
access control, backed by dashboard/db.py's admin_users table.

Replaces the old single shared ADMIN_PASSWORD — kept only as a bootstrap
value now (see bootstrap() below): ADMIN_EMAIL/ADMIN_PASSWORD seed exactly
one real admin_users row on first startup, the same way a fresh install
gets its first account. From then on, every login is a real per-person
email+password check against the database, and that first account can
create/manage every other one from the Users page.

Roles (ROLES below) are enforced here via role_required(); routes.py is
the only caller. 'admin' can do everything including manage other users;
'support_agent' handles Conversations + Tickets (including updating ticket
status); 'analyst' gets Analytics plus read-only Conversations/Tickets.
"""
import os
import secrets

from flask import redirect, session, url_for
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash

from dashboard import db

ROLES = ('admin', 'support_agent', 'analyst')
ROLE_LABELS = {'admin': 'Admin', 'support_agent': 'Support Agent', 'analyst': 'Analyst'}

# A fixed-cost dummy hash checked when an email isn't found, so a login
# attempt takes roughly the same time whether or not that email exists —
# without this, response time alone would leak which emails have accounts.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def bootstrap() -> None:
    """Seeds exactly one admin_users row from ADMIN_EMAIL/ADMIN_PASSWORD,
    but only if the table is completely empty — never overwrites or
    duplicates on every restart. Blank env vars + empty table = the
    dashboard has no way to log in at all (by design, same as the old
    ADMIN_PASSWORD-unset behavior) until someone sets them once."""
    email = os.environ.get('ADMIN_EMAIL', '').strip()
    password = os.environ.get('ADMIN_PASSWORD', '')
    if not email or not password:
        return
    if db.count_admin_users() > 0:
        return
    db.create_admin_user(email, generate_password_hash(password), 'admin')


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def authenticate(email: str, password: str) -> dict:
    """Returns the user row on success, else None. Always runs a password
    check (against a dummy hash when the email doesn't exist) so this
    takes the same time either way."""
    user = db.get_admin_user_by_email(email or '')
    candidate_hash = user['password_hash'] if user else _DUMMY_HASH
    ok = check_password_hash(candidate_hash, password or '')
    return user if (user and ok) else None


def log_in(user: dict) -> None:
    session['admin_user_id'] = user['id']


def log_out() -> None:
    session.pop('admin_user_id', None)


def current_user() -> dict:
    """Looked up fresh from the DB on every call (by primary key — cheap)
    rather than cached in the session, so a role change or account
    deletion takes effect on the very next request instead of only after
    the person logs back in."""
    user_id = session.get('admin_user_id')
    if not user_id:
        return None
    return db.get_admin_user_by_id(user_id)


def is_logged_in() -> bool:
    return current_user() is not None


def admin_required(view):
    """Any authenticated dashboard user, regardless of role."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('dashboard.login'))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Authenticated AND one of the given roles — anyone logged in but in
    the wrong role gets a 403 page instead of a login redirect, since
    they're not unauthenticated, just not allowed here."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for('dashboard.login'))
            if user['role'] not in roles:
                from flask import render_template
                return render_template('403.html', show_nav=True), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator
