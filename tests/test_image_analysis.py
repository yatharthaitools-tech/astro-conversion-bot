"""Vision analysis for an uploaded photo — app.py's load_current_photo()
reads the actual image bytes for THIS turn's marker (not an old one from
history) so agent/orchestrator.py can attach them to the Gemini call,
instead of the model only ever seeing the '[Shared a photo: <url>]' text
and guessing (previously papered over by a hardcoded client-side "must be
for a face/palm reading" assumption in static/script.js, now removed).
"""
import base64
import os
from unittest.mock import patch

import pytest

import app as app_module
from agent import orchestrator as agent_orchestrator
from agent.context import SessionContext

# Smallest possible valid PNG (1x1, transparent).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def uploaded_photo():
    name = "test_upload_abc123.png"
    path = os.path.join(app_module.UPLOAD_FOLDER, name)
    with open(path, "wb") as f:
        f.write(_TINY_PNG)
    yield f"{app_module.app.static_url_path}/uploads/{name}"
    os.remove(path)


def test_load_current_photo_reads_bytes_and_mime(uploaded_photo):
    question = f"[Shared a photo: {uploaded_photo}]"
    data, mime = app_module.load_current_photo(question)

    assert mime == "image/png"
    assert base64.b64decode(data) == _TINY_PNG


def test_load_current_photo_ignores_marker_only_in_history_not_question(uploaded_photo):
    # find_last_attachment_url() intentionally looks at history too (for
    # evidence_url), but load_current_photo() must NOT — re-sending the
    # same image bytes on every later turn just because an old marker is
    # still technically findable would be wasteful and wrong.
    data, mime = app_module.load_current_photo("just a normal follow-up message")
    assert data is None and mime is None


def test_load_current_photo_rejects_path_traversal():
    malicious = "[Shared a photo: /static/../../../../etc/passwd]"
    data, mime = app_module.load_current_photo(malicious)
    assert data is None and mime is None


def test_load_current_photo_returns_none_for_missing_file():
    data, mime = app_module.load_current_photo("[Shared a photo: /static/uploads/does-not-exist.png]")
    assert data is None and mime is None


def test_run_chat_turn_attaches_image_as_inline_data(monkeypatch):
    """The image must actually reach the Gemini call as an inlineData
    part on the CURRENT turn's content, not just get silently dropped."""
    monkeypatch.setattr(agent_orchestrator.client, "is_configured", lambda: True)

    captured = {}

    def fake_call(system_instruction, contents, tools):
        captured["contents"] = contents
        return {"candidates": [{"content": {"parts": [{"text": "I can see it, tell me more"}]}}]}

    ctx = SessionContext(user_id="u1", session_id="s1", language="en")
    with patch.object(agent_orchestrator.client, "call", side_effect=fake_call):
        answer = agent_orchestrator.run_chat_turn(
            "[Shared a photo: /static/uploads/x.png]", [], ctx, turn_number=1, past_warmup=True,
            image_data="ZmFrZWJhc2U2NGRhdGE=", image_mime="image/png",
        )

    assert answer == "I can see it, tell me more"
    current_turn_parts = captured["contents"][-1]["parts"]
    assert {"text": "[Shared a photo: /static/uploads/x.png]"} in current_turn_parts
    assert {"inlineData": {"mimeType": "image/png", "data": "ZmFrZWJhc2U2NGRhdGE="}} in current_turn_parts
    # Image comes before the text, followed by the grounding instruction.
    assert "inlineData" in current_turn_parts[0]
    assert current_turn_parts[-1] == {"text": agent_orchestrator._IMAGE_TURN_NOTE}


def test_run_chat_turn_tells_model_when_image_could_not_load(monkeypatch):
    monkeypatch.setattr(agent_orchestrator.client, "is_configured", lambda: True)
    captured = {}

    def fake_call(system_instruction, contents, tools):
        captured["contents"] = contents
        return {"candidates": [{"content": {"parts": [{"text": "didn't come through"}]}}]}

    ctx = SessionContext(user_id="u1", session_id="s1", language="en")
    with patch.object(agent_orchestrator.client, "call", side_effect=fake_call):
        agent_orchestrator.run_chat_turn(
            "[Shared a photo: /static/uploads/x.png]", [], ctx, turn_number=1, past_warmup=True,
            image_unavailable=True,
        )

    parts = captured["contents"][-1]["parts"]
    assert {"text": agent_orchestrator._IMAGE_UNAVAILABLE_NOTE} in parts
    assert not any("inlineData" in p for p in parts)


def test_run_chat_turn_without_image_has_no_inline_data(monkeypatch):
    monkeypatch.setattr(agent_orchestrator.client, "is_configured", lambda: True)

    captured = {}

    def fake_call(system_instruction, contents, tools):
        captured["contents"] = contents
        return {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}

    ctx = SessionContext(user_id="u1", session_id="s1", language="en")
    with patch.object(agent_orchestrator.client, "call", side_effect=fake_call):
        agent_orchestrator.run_chat_turn("hello", [], ctx, turn_number=1, past_warmup=True)

    current_turn_parts = captured["contents"][-1]["parts"]
    assert current_turn_parts == [{"text": "hello"}]
