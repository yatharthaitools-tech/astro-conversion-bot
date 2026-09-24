from unittest import mock

import pytest

from dashboard import auth


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(auth, 'GOOGLE_OAUTH_CLIENT_ID', 'client-123')
    monkeypatch.setattr(auth, 'ADMIN_ALLOWED_EMAILS', {'boss@gmail.com'})
    monkeypatch.setattr(auth, 'ADMIN_ALLOWED_DOMAIN', 'astrolokal.com')


def _claims(**overrides):
    claims = {'email': 'someone@astrolokal.com', 'email_verified': True, 'hd': 'astrolokal.com'}
    claims.update(overrides)
    return claims


def _verify(claims):
    with mock.patch.object(auth.id_token, 'verify_oauth2_token', return_value=claims):
        return auth.verify_google_credential('token')


def test_unconfigured_rejects_everything(monkeypatch):
    monkeypatch.setattr(auth, 'GOOGLE_OAUTH_CLIENT_ID', '')
    assert _verify(_claims()) is None


def test_workspace_domain_allowed(configured):
    assert _verify(_claims()) == 'someone@astrolokal.com'


def test_allowlisted_email_allowed(configured):
    assert _verify(_claims(email='Boss@gmail.com', hd=None)) == 'Boss@gmail.com'


def test_domain_needs_hd_claim_not_just_email_suffix(configured):
    # A personal account can't pass as the domain just by its address.
    assert _verify(_claims(hd=None)) is None


def test_other_accounts_rejected(configured):
    assert _verify(_claims(email='x@gmail.com', hd=None)) is None


def test_unverified_email_rejected(configured):
    assert _verify(_claims(email_verified=False)) is None


def test_invalid_token_rejected(configured):
    with mock.patch.object(auth.id_token, 'verify_oauth2_token', side_effect=ValueError('bad')):
        assert auth.verify_google_credential('token') is None
