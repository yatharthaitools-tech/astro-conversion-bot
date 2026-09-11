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


def run_chat_turn(question: str, history, ctx, turn_number: int, past_warmup: bool):
    """Returns answer text, or None if Gemini is unconfigured/unavailable
    (callers fall back to a rule-based responder). Side effects — a UI
    action to surface, whether the thread should close, the tool trace —
    land on ctx (ctx.ui_action, ctx.show_feedback, ctx.trace); the caller
    reads those directly off the same ctx object passed in.
    """
    if not client.is_configured():
        return None

    system_instruction = prompt.build_system_prompt(ctx.language, turn_number, past_warmup)
    contents = _history_to_contents(history)
    contents.append({"role": "user", "parts": [{"text": question}]})

    for _ in range(MAX_ITERATIONS):
        try:
            response = client.call(system_instruction, contents, tool_schemas.ALL_TOOLS)
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
