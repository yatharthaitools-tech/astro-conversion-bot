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
from integrations import (
    coin_credit_client,
    faq_client,
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
    return {"tier": ltv_service.get_tier(ctx.user_id)}


def _handle_credit_coins(safe_input, ctx):
    booking_id = safe_input.get("booking_id")
    reason = safe_input.get("reason")
    category = safe_input.get("category", "unspecified")
    if not booking_id or not reason:
        return {"error": "booking_id and reason are required"}
    return ltv_service.request_credit(ctx.user_id, booking_id, reason, category)


def _handle_search_astrologers(safe_input, ctx):
    query = safe_input.get("query", "")
    return {"matches": recommend_flow_client.search(query)}


def _handle_trigger_recommend_astrologer(safe_input, ctx):
    astrologer_id = safe_input.get("astrologer_id")
    concern = safe_input.get("concern")
    action = recommend_flow_client.trigger(ctx.language, astrologer_id, concern)
    ctx.ui_action = action
    astrologer = action["astrologer"]
    # Real availability, not a guess — this is what tells the model whether
    # a named astrologer is actually busy/offline before it decides whether
    # notify_me_subscribe even applies. Never assume someone's unavailable
    # without this.
    return {
        "ok": True,
        "shown": astrologer["name"] if action["display_mode"] == "specific" else "best_match",
        "availability": astrologer["availability"],
    }


def _handle_notify_me_subscribe(safe_input, ctx):
    astrologer_id = safe_input.get("astrologer_id")
    if not astrologer_id:
        return {"error": "astrologer_id is required"}
    astrologer = recommend_flow_client.get_astrologer(astrologer_id)
    if not astrologer:
        return {"error": "unknown astrologer_id"}
    # MOCKED — real version would write a notify-me subscription row.
    return {"ok": True, "subscribed_to": astrologer["name"]}


def _handle_trigger_payment_bottomsheet(safe_input, ctx):
    kind = safe_input.get("kind", "recharge")
    action = payment_bottomsheet_client.trigger(kind)
    ctx.ui_action = action
    return {"ok": True, "kind": kind}


def _handle_create_support_ticket(safe_input, ctx):
    category = safe_input.get("category")
    sub_category = safe_input.get("sub_category")
    description = safe_input.get("description")
    if not category or not sub_category or not description:
        return {"error": "category, sub_category, and description are required"}
    evidence_url = safe_input.get("evidence_url") or ctx.last_attachment_url
    ticket = ticket_service.create_ticket(
        ctx.user_id, category, sub_category, description, evidence_url
    )
    return {"ok": True, "ticket_id": ticket["ticket_id"], "status": ticket["status"]}


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
    "get_payment_status": _handle_get_payment_status,
    "get_booking_details": _handle_get_booking_details,
    "check_refund_eligibility": _handle_check_refund_eligibility,
    "get_ltv_tier": _handle_get_ltv_tier,
    "credit_coins": _handle_credit_coins,
    "search_astrologers": _handle_search_astrologers,
    "trigger_recommend_astrologer": _handle_trigger_recommend_astrologer,
    "notify_me_subscribe": _handle_notify_me_subscribe,
    "trigger_payment_bottomsheet": _handle_trigger_payment_bottomsheet,
    "create_support_ticket": _handle_create_support_ticket,
    "log_feature_request": _handle_log_feature_request,
    "mark_issue_resolved": _handle_mark_issue_resolved,
    "get_tickets": _handle_get_tickets,
    "get_wallet_status": _handle_get_wallet_status,
    "get_active_offers": _handle_get_active_offers,
    "get_queue_position": _handle_get_queue_position,
    "get_app_faq": _handle_get_app_faq,
}
