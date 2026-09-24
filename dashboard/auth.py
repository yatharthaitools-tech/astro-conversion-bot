"""Google sign-in for the admin dashboard, via a Flask session cookie.

The login page renders Google's "Sign in with Google" button (Google
Identity Services), which POSTs a signed ID token back to us. We verify
that token against GOOGLE_OAUTH_CLIENT_ID and only let the email in if
it's on the allowlist — ADMIN_ALLOWED_EMAILS (exact addresses) and/or
ADMIN_ALLOWED_DOMAIN (a whole Google Workspace domain, checked against
the token's `hd` claim so a personal gmail can't pass as the domain).
No roles — anyone allowed in sees the whole dashboard.
"""
import os
from functools import wraps

from flask import redirect, session, url_for
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

GOOGLE_OAUTH_CLIENT_ID = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
ADMIN_ALLOWED_EMAILS = {
    e.strip().lower() for e in os.environ.get('ADMIN_ALLOWED_EMAILS', '').split(',') if e.strip()
}
ADMIN_ALLOWED_DOMAIN = os.environ.get('ADMIN_ALLOWED_DOMAIN', '').strip().lower()


def is_configured() -> bool:
    return bool(GOOGLE_OAUTH_CLIENT_ID) and bool(ADMIN_ALLOWED_EMAILS or ADMIN_ALLOWED_DOMAIN)


def is_allowed(email: str, hosted_domain: str) -> bool:
    email = (email or '').lower()
    if email in ADMIN_ALLOWED_EMAILS:
        return True
    return bool(ADMIN_ALLOWED_DOMAIN) and (hosted_domain or '').lower() == ADMIN_ALLOWED_DOMAIN


def verify_google_credential(credential: str):
    """Returns the verified email if the ID token is valid and the account
    is allowed in, else None. Signature, audience, issuer and expiry are
    all checked by verify_oauth2_token."""
    if not is_configured() or not credential:
        return None
    try:
        claims = id_token.verify_oauth2_token(credential, GoogleAuthRequest(), GOOGLE_OAUTH_CLIENT_ID)
    except ValueError:
        return None
    if not claims.get('email_verified'):
        return None
    email = claims.get('email', '')
    if not is_allowed(email, claims.get('hd', '')):
        return None
    return email


def is_logged_in() -> bool:
    return bool(session.get('admin_email'))


def current_admin_email() -> str:
    return session.get('admin_email', '')


def log_in(email: str) -> None:
    session.clear()
    session['admin_email'] = email


def log_out() -> None:
    session.pop('admin_email', None)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('dashboard.login'))
        return view(*args, **kwargs)
    return wrapped
