"""The Gemini tool-calling loop — same shape as AstroHelp's orchestrator.py.
One chat turn is a manual loop against the Vertex AI API, capped at
MAX_ITERATIONS. This file only ever sees tool_schemas.py's pure data;
the real handler dispatch happens through executor.execute(), which is
the only path from a model-requested tool call to a real handler.
"""
import logging

from agent import client, executor as agent_executor, prompt, tool_schemas

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6

_IMAGE_TURN_NOTE = (
    "[The visitor attached the image above. Look at it, but do NOT act on it yet: your ONLY "
    "job this turn is to ask what their concern is. Briefly acknowledge the image in a few "
    "words based on what is actually visible (never assume it is a payment, error or reading "
    "photo), then ask ONE direct question about what they need help with. Do not offer to "
    "connect them with an astrologer, do not call any tool, do not make claims about payments "
    "or issues, and do not give any other information until they answer.]"
)
_IMAGE_UNAVAILABLE_NOTE = (
    "[The visitor tried to share an image but it could not be loaded, so you cannot see it. "
    "Do NOT guess what it shows. Tell them it didn't come through and ask them to resend "
    "it or describe the problem in words.]"
)


def _history_to_contents(history):
    contents = []
    for turn in history or []:
        sender = turn.get("sender") or turn.get("role")
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        role = "model" if sender == "bot" else "user"
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents


def run_chat_turn(question: str, history, ctx, turn_number: int, past_warmup: bool,
                   image_data: str = None, image_mime: str = None,
                   image_unavailable: bool = False):
    """Returns answer text, or None if Gemini is unconfigured/unavailable
    (callers fall back to a rule-based responder). Side effects — a UI
    action to surface, whether the thread should close, the tool trace —
    land on ctx (ctx.ui_action, ctx.show_feedback, ctx.trace); the caller
    reads those directly off the same ctx object passed in.

    image_data/image_mime (base64 str + a real image/* mime type, from
    app.py's load_current_photo) attach the actual photo the visitor just
    shared to THIS turn's content, so the model genuinely looks at it
    instead of only ever seeing the '[Shared a photo: <url>]' text marker
    — that text alone told the model nothing about what's actually in the
    picture, which is what let a hardcoded client-side assumption (the old
    "must be for a face/palm reading" framing this replaced) stand in for
    real understanding.
    """
    if not client.is_configured():
        return None

    system_instruction = prompt.build_system_prompt(ctx.language, turn_number, past_warmup, ctx.user_name)
    contents = _history_to_contents(history)
    current_parts = [{"text": question}]
    if image_data and image_mime:
        # Image first, then an explicit instruction after it: with only a bare
        # '[Shared a photo: <url>]' text part the model leaned on the prompt's
        # examples and earlier chat topics (e.g. calling an unrelated chat
        # screenshot a "payment issue") instead of what the pixels show.
        current_parts = [
            {"inlineData": {"mimeType": image_mime, "data": image_data}},
            {"text": question},
            {"text": _IMAGE_TURN_NOTE},
        ]
    elif image_unavailable:
        current_parts.append({"text": _IMAGE_UNAVAILABLE_NOTE})
    contents.append({"role": "user", "parts": current_parts})

    # On a turn with a photo, tools are withheld entirely: the reply must be
    # just the concern question, never a ticket/connect-card/resolve action
    # taken off a photo the visitor hasn't explained yet.
    tools = [] if (image_data and image_mime) or image_unavailable else tool_schemas.ALL_TOOLS

    for _ in range(MAX_ITERATIONS):
        try:
            response = client.call(system_instruction, contents, tools)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini call failed: %s", exc)
            return None

        try:
            candidate = response["candidates"][0]
            parts = candidate["content"]["parts"]
        except (KeyError, IndexError):
            logger.warning("Unexpected Gemini response shape: %s", response)
            return None

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not function_calls:
            text = "".join(p.get("text", "") for p in parts).strip()
            return text or None

        contents.append({"role": "model", "parts": parts})

        function_response_parts = []
        for fc in function_calls:
            name = fc.get("name")
            args = fc.get("args") or {}
            result = agent_executor.execute(name, args, ctx)
            function_response_parts.append(
                {"functionResponse": {"name": name, "response": result}}
            )

        # Vertex AI rejects role="tool" for function results despite some
        # docs suggesting it — same lesson AstroHelp's orchestrator
        # documents, verified again directly against the real API below.
        contents.append({"role": "user", "parts": function_response_parts})

    logger.warning("Tool loop exhausted %d iterations without a final reply", MAX_ITERATIONS)
    return None
