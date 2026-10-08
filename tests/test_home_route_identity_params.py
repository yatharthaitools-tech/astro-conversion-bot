"""app.py's / route — two real link formats confirmed against production:
the native app's WebView (?user_id=...&oauth_token=...&user_name=...&
ltv=...) and the "Chat with us" support/CRM link
(?user_id=...&name=...&ltv=..., no oauth_token, and the name param is
spelled differently). Both need to actually reach the page's
data-user-name attribute that static/script.js reads. Also covers the
newer encrypted ?token= param (integrations/link_token.py), which takes
over from both plaintext formats when present.
"""
import app as app_module
from integrations import link_token


def test_name_param_reaches_data_user_name_attribute():
    client = app_module.app.test_client()
    resp = client.get("/?user_id=8236&name=durvish&ltv=13924.00")
    assert resp.status_code == 200
    assert b'data-user-name="durvish"' in resp.data


def test_legacy_user_name_param_still_works():
    client = app_module.app.test_client()
    resp = client.get("/?user_id=1&oauth_token=tok&user_name=Priya&ltv=100")
    assert resp.status_code == 200
    assert b'data-user-name="Priya"' in resp.data


def test_name_param_wins_when_both_are_present():
    client = app_module.app.test_client()
    resp = client.get("/?user_id=1&name=Durvish&user_name=Legacy")
    assert resp.status_code == 200
    assert b'data-user-name="Durvish"' in resp.data


def test_encrypted_token_populates_data_attributes_and_carries_no_plaintext(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())
    token = link_token.encrypt_identity("8236", "durvish", "13924.00")

    client = app_module.app.test_client()
    resp = client.get(f"/?token={token}")

    assert resp.status_code == 200
    assert b'data-user-id="8236"' in resp.data
    assert b'data-user-name="durvish"' in resp.data
    assert b'data-ltv="13924.00"' in resp.data
    # The URL itself (echoed nowhere in a real response, but this proves
    # the token string that WAS in the URL carries none of these values
    # in the clear) never contains the plaintext.
    assert b"8236" not in token.encode()
    assert b"durvish" not in token.encode()


def test_broken_token_does_not_fall_back_to_coexisting_plaintext_params(monkeypatch):
    # The exact attack this guards against: a crafted link with a
    # garbage token AND spoofed plaintext params alongside it must not
    # let the plaintext params through.
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", link_token.generate_key())

    client = app_module.app.test_client()
    resp = client.get("/?token=not-a-real-token&user_id=attacker&name=Attacker&ltv=999999")

    assert resp.status_code == 200
    assert b'data-user-id="attacker"' not in resp.data
    assert b'data-user-name="Attacker"' not in resp.data
    assert b'data-user-id=""' in resp.data


def test_token_param_ignored_when_link_token_not_configured(monkeypatch):
    monkeypatch.setattr(link_token, "LINK_TOKEN_KEY", "")

    client = app_module.app.test_client()
    resp = client.get("/?token=whatever&user_id=1&name=Priya")

    # No key configured at all -> token is meaningless either way, but
    # since a token param WAS sent, the route still must not silently
    # fall back to plaintext (same no-bypass rule, just the "not
    # configured" branch of it).
    assert resp.status_code == 200
    assert b'data-user-name="Priya"' not in resp.data
