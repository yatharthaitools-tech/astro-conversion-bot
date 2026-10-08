"""integrations/link_token.py — encrypted ?token= link param so
user_id/name/ltv never sit as plaintext in a URL (browser history,
server access logs, a forwarded link). Fernet (cryptography package) —
real encryption, not a toy encoding, tested round-trip rather than
mocked.
"""
import time

from integrations import link_token


def test_not_configured_without_a_key(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", "")
    assert link_token.is_configured() is False
    assert link_token.decrypt_identity("anything") is None


def test_round_trips_user_id_name_and_ltv(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())

    token = link_token.encrypt_identity("8236", "durvish", "13924.00")
    # The whole point: the token itself must not contain the plaintext
    # values anywhere a human could read them off.
    assert "8236" not in token
    assert "durvish" not in token
    assert "13924" not in token

    result = link_token.decrypt_identity(token)
    assert result == {"user_id": "8236", "name": "durvish", "ltv": "13924.00"}


def test_rejects_a_tampered_token(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())
    token = link_token.encrypt_identity("8236", "durvish", "13924.00")
    tampered = token[:-4] + ("A" * 4 if not token.endswith("AAAA") else "BBBB")
    assert link_token.decrypt_identity(tampered) is None


def test_rejects_a_token_encrypted_with_a_different_key(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())
    token = link_token.encrypt_identity("8236", "durvish", "13924.00")

    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())
    assert link_token.decrypt_identity(token) is None


def test_rejects_an_expired_token(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())
    monkeypatch.setattr(link_token, "LINK_TOKEN_TTL_SECONDS", 1)

    token = link_token.encrypt_identity("8236", "durvish", "13924.00")
    time.sleep(2)
    assert link_token.decrypt_identity(token) is None


def test_empty_token_string_is_not_configured_error(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())
    assert link_token.decrypt_identity("") is None
    assert link_token.decrypt_identity(None) is None
