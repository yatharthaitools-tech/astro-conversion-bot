"""The one place that resolves a tool name to a handler and dispatches it.
This is also the SECURITY BOUNDARY — same pattern as AstroHelp's
executor.py: it unconditionally overwrites any identity-shaped field the
model supplied in a tool call with the value from the verified session,
before any handler runs. Handlers never trust identity from tool input.

None of our tool schemas (tool_schemas.py) actually expose a user_id or
amount field to the model in the first place — there's no slot for it to
fill in even if it wanted to (same reasoning as AstroHelp never giving a
tool an astrologer_id field). The strip-and-overwrite below is
defense-in-depth against a hallucinated extra field, not the only thing
standing between the model and a spoofed identity/amount.

This is also the only file in agent/ that imports agent.tool_registry
(which in turn imports services/integrations) — orchestrator.py itself
only ever sees the pure-data tool_schemas.py, so the model-facing loop has
no path to a real handler except through this boundary.
"""
import logging

from agent.tool_registry import REGISTRY

logger = logging.getLogger(__name__)

_STRIPPED_KEYS = ("user_id", "amount", "coins", "credit_amount")


def execute(tool_name: str, tool_input: dict, ctx) -> dict:
    handler = REGISTRY.get(tool_name)
    if handler is None:
        return {"error": f"unknown tool: {tool_name}"}

    safe_input = dict(tool_input or {})
    for key in _STRIPPED_KEYS:
        safe_input.pop(key, None)
    safe_input["user_id"] = ctx.user_id  # always the verified session's id, never the model's

    try:
        result = handler(safe_input, ctx)
        ok = "error" not in result
        ctx.trace.append({"tool": tool_name, "ok": ok})
        return result
    except Exception as exc:  # noqa: BLE001 — a broken tool must not crash the turn
        logger.warning("Tool %s failed: %s", tool_name, exc)
        ctx.trace.append({"tool": tool_name, "ok": False})
        return {"error": "tool_failed"}
