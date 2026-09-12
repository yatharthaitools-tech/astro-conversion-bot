"""Single shared-password admin login, via a Flask session cookie.

Deliberately lightweight — this dashboard has one operator, not a team
with per-person accounts/roles like astrohelp's admin-app (JWT + bcrypt +
KAM/CS roles). A shared password behind a signed session cookie is enough
to keep the dashboard from being wide open; upgrade to real per-admin
accounts if/when more than one person needs access with different
permissions.
"""
import os
from functools import wraps

from flask import redirect, session, url_for

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')


def is_configured() -> bool:
    return bool(ADMIN_PASSWORD)


def check_password(candidate: str) -> bool:
    return is_configured() and candidate == ADMIN_PASSWORD


def is_logged_in() -> bool:
    return session.get('is_admin') is True


def log_in() -> None:
    session['is_admin'] = True


def log_out() -> None:
    session.pop('is_admin', None)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('dashboard.login'))
        return view(*args, **kwargs)
    return wrapped
