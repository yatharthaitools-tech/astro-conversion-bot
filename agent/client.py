"""Thin wrapper around the raw Vertex AI generateContent call, with
function-calling (tools) enabled. Deliberately reuses the same
service-account auth as integrations/gemini_client.py (the simpler
single-shot client used elsewhere) rather than duplicating credential
handling — accessing its "private" helpers directly is intentional here,
not accidental coupling.
"""
import requests

from integrations import gemini_client as _base


def is_configured() -> bool:
    return _base.is_configured()


def call(system_instruction: str, contents: list, tools: list) -> dict:
    """One raw generateContent call with tools enabled. Returns the raw
    Vertex AI response dict. Raises on failure — caller handles fallback."""
    function_declarations = [
        {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
        for t in tools
    ]
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        # 800 wasn't enough for a "thinking" model (gemini-3.6-flash):
        # confirmed live, thinking alone consumed 756 of the 800-token
        # budget on an ordinary reply, leaving ~0 for visible text and
        # truncating to finishReason=MAX_TOKENS with empty content.
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
        "labels": {"feature": _base.GEMINI_BILLING_FEATURE},
    }
    if function_declarations:
        payload["tools"] = [{"functionDeclarations": function_declarations}]
    response = requests.post(
        _base._endpoint_url(),
        headers=_base._auth_headers(),
        json=payload,
        timeout=_base.REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()
