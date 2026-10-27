"""Maps tool name -> handler. This is the only place in agent/ (besides
executor.py itself) that imports integrations/ and services/ — the
orchestrator never sees these, only tool_schemas.py's pure data.

Every handler signature is (safe_input: dict, ctx: SessionContext) -> dict.
safe_input has already passed through executor.py's security boundary by
the time a handler sees it. Handlers return a small dict meant to be fed
back to the model as the tool's result — keep these free of anything
sensitive, since the model (and the chat UI's trace line) can see them.

Handlers that produce a UI action (show a card, open a bottom sheet) set
ctx.ui_action rather than returning the full action payload — the model
only needs a short factual summary to describe what happened, not the
UI's own JSON shape.
"""
import re

from integrations import (
    coin_credit_client,
    faq_client,
    free_coins_client,
    offers_client,
    payment_bottomsheet_client,
    recommend_flow_client,
    redash_client,
)
from services import ltv_service, refund_service, ticket_service


def _handle_get_payment_status(safe_input, ctx):
    return redash_client.get_payment_status(ctx.user_id)


def _handle_get_booking_details(safe_input, ctx):
    return redash_client.get_booking_details(ctx.user_id, safe_input.get("booking_id"))


def _handle_check_refund_eligibility(safe_input, ctx):
    booking_id = safe_input.get("booking_id")
    if not booking_id:
        return {"error": "booking_id is required"}
    return refund_service.check_eligibility(ctx.user_id, booking_id)


def _handle_get_ltv_tier(safe_input, ctx):
    return {"tier": ltv_service.get_tier(ctx.user_id, ctx.ltv)}


def _handle_credit_coins(safe_input, ctx):
    booking_id = safe_input.get("booking_id")  # omitted = no-booking retention gesture
    reason = safe_input.get("reason")
    if not reason:
        return {"error": "reason is required"}

    if not booking_id:
        result = refund_service.decide_retention(ctx.user_id)
    else:
        # v1: LTV-tier + daily-cap only, no booking validation — see
        # services/refund_service.py's decide_v1_refund() docstring.
        result = refund_service.decide_v1_refund(ctx.user_id, ctx.ltv, reason)

    total_coins = result.get("total_coins", 0)
    if total_coins > 0:
        # Surfaces the real free-coins bottomsheet on the app side, then
        # pushes straight into the connect flow too — a credit on its own
        # is a dead end, this is the natural next step while the coins
        # are top of mind. See free_coins_client's docstring for why this
        # is a separate action rather than folded into what the model sees.
        bottomsheet = free_coins_client.build_bottomsheet(total_coins, result["credit_id"])
        connect_action = recommend_flow_client.trigger(ctx.language, None, "make the most of your new coins")
        bottomsheet["connect"] = {
            "astrologer": connect_action["astrologer"],
            "display_mode": connect_action["display_mode"],
        }
        ctx.ui_action = bottomsheet

    return {
        "total_coins": total_coins,
        "reason_code": result.get("reason_code"),
        "tier": result.get("tier"),
        "daily_cap": result.get("daily_cap"),
        "used_today": result.get("used_today"),
    }


def _handle_search_astrologers(safe_input, ctx):
    query = safe_input.get("query", "")
    matches = recommend_flow_client.search(query)
    if matches:
        return {"matches": matches}

    # Nobody by that name — decided HERE instead of trusting the model to
    # remember the "never say not on roster, pivot to someone similar"
    # instruction and make a second tool call on its own. Same reasoning
    # as trigger_recommend_astrologer's own in-code fallback for a named-
    # but-unavailable astrologer (see that function's docstring): live
    # testing showed this reliably doesn't happen in practice — 3/3 runs
    # either leaked "not on the roster", offered to connect without
    # actually triggering the card, or fabricated a specific status for a
    # person who doesn't exist. Triggering the card directly here
    # guarantees it always appears, and the model only needs to state the
    # "busy" framing in text, never a second tool call.
    action = recommend_flow_client.trigger(ctx.language, None, None)
    ctx.ui_action = action
    return {
        "matches": [],
        "requested_name": query,
        "connected_with_someone_else": True,
        "shown_astrologer_availability": action["astrologer"]["availability"],
    }


def _handle_trigger_recommend_astrologer(safe_input, ctx):
    astrologer_id = safe_input.get("astrologer_id")
    concern = safe_input.get("concern")
    action = recommend_flow_client.trigger(ctx.language, astrologer_id, concern)
    ctx.ui_action = action
    astrologer = action["astrologer"]
    # Real availability, not a guess — this is what tells the model whether
    # a named astrologer is actually busy/offline before it decides whether
    # notify_me_subscribe even applies. Never assume someone's unavailable
    # without this. next_available_at is always None for this mock roster
    # (no fabricated ETA — see recommend_flow_client._with_eta).
    requested = action.get("requested_but_unavailable")
    result = {
        "ok": True,
        "shown": astrologer["name"] if action["display_mode"] == "specific" else "best_match",
        "availability": astrologer["availability"],
        "next_available_at": astrologer["next_available_at"],
    }
    if requested:
        # The visitor named someone who turned out to be unavailable —
        # the card ALREADY routes to a different, available astrologer
        # (see recommend_flow_client.trigger's own docstring for why
        # this is decided in code, not left for a second tool call).
        # State both facts together: the named person's own status, and
        # that the shown card connects to someone else instead.
        result["requested_astrologer_name"] = requested["name"]
        result["requested_astrologer_availability"] = requested["availability"]
        result["requested_astrologer_next_available_at"] = requested["next_available_at"]
    return result


def _handle_notify_me_subscribe(safe_input, ctx):
    astrologer_id = safe_input.get("astrologer_id")
    if not astrologer_id:
        return {"error": "astrologer_id is required"}
    astrologer = recommend_flow_client.get_astrologer(astrologer_id)
    if not astrologer:
        return {"error": "unknown astrologer_id"}
    # MOCKED — real version would write a notify-me subscription row.
    return {
        "ok": True,
        "subscribed_to": astrologer["name"],
        "next_available_at": astrologer["next_available_at"],
    }


def _handle_trigger_payment_bottomsheet(safe_input, ctx):
    kind = safe_input.get("kind", "recharge")
    action = payment_bottomsheet_client.trigger(kind)
    ctx.ui_action = action
    return {"ok": True, "kind": kind, "payment_methods": action["payment_methods"]}


#  Confirmed live: despite this tool's own description instructing the
# model to ask the visitor what happened FIRST (see agent/tool_schemas.py's
# CREATE_SUPPORT_TICKET) before ever calling this, the model sometimes
# calls it anyway on a bare topic with no real detail — self-admitted in
# its own description text ("wants to report an astrologer but hasn't
# provided details yet"). Same prompt-reliability gap as
# PREDICTION_KEYWORDS/is_severe_complaint in app.py — reject the call
# here rather than trust prompt compliance alone, so the model gets this
# back as a tool error (it still has iterations left in the same turn,
# see orchestrator.py's loop) and actually asks instead of raising an
# empty-shell ticket. Narrow and self-admission-based on purpose: this
# only catches the model's OWN stated insufficiency, not a general
# "is this description good enough" judgment call, which needs real
# understanding this heuristic can't give. Not exhaustive — same
# documented limitation as app.py's _ESCALATION_CLAIM_WORDS (English has
# too many ways to say "they haven't told me yet" to ever fully enumerate,
# e.g. "hasn't PROVIDED" vs "hasn't YET SHARED" vs "hasn't CLARIFIED" all
# showed up live for the exact same underlying case) — catches the
# reproduced phrasings, not a general paraphrase detector.
# Regexes, not plain substrings — a naive substring list ("hasn't
# provided") missed real phrasings with a word in between ("hasn't YET
# provided") and a different info-giving verb ("hasn't YET SHARED"), so
# each pattern tolerates an optional infix word between the negation and
# a broad set of "told me the details" verbs.
_INFIX = r"(?:yet\s+|actually\s+|even\s+|further\s+)?"
_INFO_VERB = (
    r"(?:provided|specified|shared|told(?:\s+me)?|mentioned|given|"
    r"described|stated|informed|explained|detailed|clarified|elaborated)"
)
_INSUFFICIENT_DESCRIPTION_PATTERNS = [
    rf"has\s*n'?t\s+{_INFIX}{_INFO_VERB}",
    rf"have\s*n'?t\s+{_INFIX}{_INFO_VERB}",
    rf"has\s+not\s+{_INFIX}{_INFO_VERB}",
    r"no\s+(?:further\s+|real\s+|specific\s+|concrete\s+)?(?:details?|information|info)\b",
    r"without\s+(?:providing|giving|sharing)\s+(?:any\s+)?details?",
    r"not\s+(?:sure|certain)\s+what\s+happened",
    r"doesn'?t\s+know\s+what\s+happened",
    r"unclear\s+what\s+(?:happened|went\s+wrong|the\s+issue\s+is)",
    r"yet\s+to\s+(?:provide|specify|share|explain|describe|clarify)",
    r"no\s+description\s+(?:provided|given|shared)",
    r"without\s+(?:any\s+)?specifics",
    r"no\s+specifics",
    r"(?:awaiting|pending)\s+(?:more\s+)?(?:details?|information|info|clarification)",
]
_INSUFFICIENT_DESCRIPTION_RE = re.compile("|".join(_INSUFFICIENT_DESCRIPTION_PATTERNS), re.IGNORECASE)


def _description_admits_insufficient_detail(description: str) -> bool:
    return bool(_INSUFFICIENT_DESCRIPTION_RE.search(description or ""))


def _handle_create_support_ticket(safe_input, ctx):
    category = safe_input.get("category")
    sub_category = safe_input.get("sub_category")
    description = safe_input.get("description")
    if not category or not sub_category or not description:
        return {"error": "category, sub_category, and description are required"}
    if _description_admits_insufficient_detail(description):
        return {
            "error": (
                "Not enough real detail yet to raise this ticket — ask "
                "the visitor directly what happened (one question, say "
                "nothing about the team/flagging/forwarding yet) instead "
                "of calling this tool again until they've answered."
            )
        }
    evidence_url = safe_input.get("evidence_url") or ctx.last_attachment_url
    # ROUTE on the visitor's OWN declared language from the link (see
    # agent/context.py's SessionContext.user_language) only — never the
    # per-message auto-detected script of what they just typed, which is
    # just this message's reply language and can be wrong for routing
    # (e.g. they typed this message in English but actually prefer Hindi
    # support). No declared preference at all (an older link format, or
    # the param not sent) means no language preference for routing,
    # same as before this param existed — _pick_round_robin_assignee
    # already treats None the same as "no match", not "handles nothing".
    routing_language = ctx.user_language
    ticket = ticket_service.create_ticket(
        ctx.user_id, category, sub_category, description, evidence_url,
        session_id=ctx.session_id, language=routing_language, ltv=ctx.ltv,
    )
    ctx.ticket_raised = True
    return {"ok": True, "ticket_id": ticket["ticket_id"], "status": ticket["status"]}


def _handle_reopen_ticket(safe_input, ctx):
    result = ticket_service.reopen_ticket(ctx.user_id, note=safe_input.get("reason"))
    if result["reopened"]:
        ctx.ticket_raised = True
    return result


def _handle_log_feature_request(safe_input, ctx):
    # MOCKED — real version would write to a product-feedback table.
    return {"ok": True, "logged": True}


def _handle_mark_issue_resolved(safe_input, ctx):
    ctx.show_feedback = True
    return {"ok": True}


def _handle_get_tickets(safe_input, ctx):
    return {"tickets": ticket_service.get_tickets(ctx.user_id)}


def _handle_get_wallet_status(safe_input, ctx):
    return redash_client.get_wallet_status(ctx.user_id)


def _handle_get_active_offers(safe_input, ctx):
    return {"offers": offers_client.get_active_offers()}


def _handle_get_queue_position(safe_input, ctx):
    return redash_client.get_queue_position(ctx.user_id)


def _handle_get_app_faq(safe_input, ctx):
    return faq_client.search(safe_input.get("query", ""))


REGISTRY = {
    # "get_payment_status": _handle_get_payment_status,
    # "get_booking_details": _handle_get_booking_details,
    # "get_wallet_status": _handle_get_wallet_status,
    # "get_queue_position": _handle_get_queue_position, -- deliberately
    # not registered right now, see tool_schemas.py's ALL_TOOLS comment.
    # "check_refund_eligibility": _handle_check_refund_eligibility, and
    # "credit_coins": _handle_credit_coins, -- deliberately not
    # registered right now, see tool_schemas.py's ALL_TOOLS comment.
    "get_ltv_tier": _handle_get_ltv_tier,
    "search_astrologers": _handle_search_astrologers,
    "trigger_recommend_astrologer": _handle_trigger_recommend_astrologer,
    "notify_me_subscribe": _handle_notify_me_subscribe,
    "trigger_payment_bottomsheet": _handle_trigger_payment_bottomsheet,
    "create_support_ticket": _handle_create_support_ticket,
    "reopen_ticket": _handle_reopen_ticket,
    "log_feature_request": _handle_log_feature_request,
    "mark_issue_resolved": _handle_mark_issue_resolved,
    "get_tickets": _handle_get_tickets,
    # "get_active_offers": _handle_get_active_offers, -- deliberately not
    # registered right now, see tool_schemas.py's ALL_TOOLS comment.
    "get_app_faq": _handle_get_app_faq,
}
