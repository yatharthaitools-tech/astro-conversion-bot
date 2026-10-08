"""app.py's / route — two real link formats confirmed against production:
the native app's WebView (?user_id=...&oauth_token=...&user_name=...&
ltv=...) and the "Chat with us" support/CRM link
(?user_id=...&name=...&ltv=..., no oauth_token, and the name param is
spelled differently). Both need to actually reach the page's
data-user-name attribute that static/script.js reads.
"""
import app as app_module


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
