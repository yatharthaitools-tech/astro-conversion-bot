"""The dominant refund pattern: session ended in seconds, astrologer never
actually responded, coins still deducted. This is auto-approved on FACT
(duration + message count), regardless of LTV tier — unlike decide()
below, a New/Unpaid visitor still gets this refund, because nothing was
actually delivered. Keep this separate from decide(): one is "did the
astrologer even show up" (a fast, narrow factual check with no LTV
involved at all), the other is the full "Refund & Bonus Logic" SOP.
"""
import uuid

from dashboard import db as dashboard_db
from integrations import coin_credit_client, redash_client

AUTO_REFUND_MAX_DURATION_SECONDS = 30


def check_eligibility(user_id: str, booking_id: str) -> dict:
    booking = redash_client.get_booking_details(user_id, booking_id)
    qualifies = (
        booking["duration_seconds"] < AUTO_REFUND_MAX_DURATION_SECONDS
        and booking["astrologer_message_count"] == 0
        and booking["coins_deducted"] > 0
    )
    if not qualifies:
        return {"qualifies": False, "booking": booking}

    existing = redash_client.get_refund_goodwill_history(user_id, booking_id)
    if existing:
        return {"qualifies": True, "already_refunded": True, "booking": booking, "existing": existing}

    result = coin_credit_client.credit(user_id, booking_id, booking["coins_deducted"], "refund")
    if not result["success"]:
        # The real credit call failed — must NOT record this as credited
        # (that would poison the dedupe check above and block a retry)
        # or report an amount/credit_id as if coins actually moved.
        return {"qualifies": True, "already_refunded": False, "credit_failed": True, "booking": booking}
    redash_client.record_credit(user_id, booking_id, "refund", booking["coins_deducted"])
    return {
        "qualifies": True,
        "already_refunded": False,
        "amount": booking["coins_deducted"],
        "credit_id": result["credit_id"],
        "booking": booking,
    }


# --- v1 refund: LTV tier + daily cap, no booking validation -------------
#
# Replaces the 4-step SOP below for now: no get_booking_details call, no
# issue_tag classification, no Redash query of any kind — LTV arrives
# with the session itself (agent/context.py's SessionContext.ltv, set
# from the app's own user_name/ltv session params). A flat amount per
# tier, capped at N refunds/day per tier, tracked in our own
# coin_credit_attempts table (see dashboard.db.count_successful_credits_today)
# rather than a Redash-backed dedupe/monthly-count check.

_V1_REFUND_TIERS = (
    # (ltv_lower_inclusive, ltv_upper_exclusive, daily_cap, flat_amount)
    (0, 200, 1, 50),
    (200, 1000, 2, 100),
    (1000, float("inf"), 3, 150),
)


def _v1_tier(ltv: float) -> tuple:
    for lower, upper, cap, amount in _V1_REFUND_TIERS:
        if lower <= ltv < upper:
            return cap, amount
    # ltv < 0 (shouldn't happen) falls through to the lowest tier rather
    # than raising — never let a weird input value block a refund path.
    return _V1_REFUND_TIERS[0][2], _V1_REFUND_TIERS[0][3]


def decide_v1_refund(user_id: str, ltv, reason: str) -> dict:
    """The only refund decision credit_coins makes right now when
    booking_id is set. No fact-checking against what actually happened —
    trusts the visitor's claim entirely, gated only by how many they've
    already gotten today for their LTV tier."""
    if ltv is None:
        return {"total_coins": 0, "credit_id": None, "reason_code": "ltv_unknown"}

    cap, amount = _v1_tier(float(ltv))
    used_today = dashboard_db.count_successful_credits_today(user_id, "refund_v1")
    if used_today >= cap:
        return {"total_coins": 0, "credit_id": None, "reason_code": "daily_cap_reached",
                "daily_cap": cap, "used_today": used_today}

    # A synthetic per-attempt reference (not a real booking_id) so the
    # idempotency gate in coin_credit_client treats each refund as its
    # own attempt — this cap, not that gate, is what limits how many a
    # visitor can get in a day.
    attempt_ref = f"v1refund-{uuid.uuid4().hex[:10]}"
    result = coin_credit_client.credit(user_id, attempt_ref, amount, "refund_v1")
    if not result["success"]:
        return {"total_coins": 0, "credit_id": None, "reason_code": "credit_failed",
                "daily_cap": cap, "used_today": used_today}

    return {"total_coins": amount, "credit_id": result["credit_id"], "reason_code": "refund_v1_credited",
            "daily_cap": cap, "used_today": used_today + 1}


# --- "Refund & Bonus Logic — One Page" SOP -----------------------------
#
# NOT currently wired into credit_coins — decide_v1_refund() above is
# what actually runs for now. Kept here for a possible v2 (real booking
# validation via Redash), not deleted.
#
# Facts decide the refund. LTV only decides the size of the "thank you
# for your patience" on top. Steps are evaluated in order, first match
# wins — decide() below is the ONE place this whole SOP lives; nothing
# else should reimplement a slice of it.
#
# Step 1 — factual, astrologer/system at fault -> full refund, every tier.
# Step 2 — factual, visitor at fault -> no refund, every tier.
# Step 3 — partial fault -> refund unused coins + an LTV-tiered bonus.
# Step 4 — no clear factual signal -> escalate to CS, optional small
#          goodwill (half of Step 3's %) while it's pending.
#
# credit_coins is the only tool that reaches this — its issue_tag enum
# (agent/tool_schemas.py) is exactly the four sets below, so the model's
# own classification IS the step selection. The model never sees or sets
# an amount; every number below is computed here.

STEP1_ASTROLOGER_FAULT = {
    "astro_did_not_reply",
    "no_one_responded",
    "consultation_not_done_coins_deducted",
    "astrologer_answered_and_disconnected",
}
STEP2_USER_FAULT = {
    "user_did_not_reply",
    "user_replied_late",
}
STEP3_PARTIAL_FAULT = {
    "astrologer_took_more_time",
    "audio_video_not_clear",
    "chat_glitch_mid_session",
    "session_disconnected_mid_way",
}
STEP4_NO_SIGNAL = {
    "astrologer_not_helpful",
    "poor_prediction_quality",
    "scam_or_trust_complaint",
    "blank_screen_unconfirmed",
}

# Step 3: refund unused coins (always) + this tier's bonus on top.
_STEP3_BONUS = {
    "new_unpaid": {"pct": 0.00, "cap_per_incident": 0, "cap_per_month": 0},
    "new_low": {"pct": 0.10, "cap_per_incident": 20, "cap_per_month": 2},
    "mid": {"pct": 0.20, "cap_per_incident": 50, "cap_per_month": 3},
    "high": {"pct": 0.30, "cap_per_incident": 100, "cap_per_month": None},  # unlimited, but 2nd+/month -> ticket
}

# Step 4: goodwill while a ticket is pending — exactly half Step 3's %.
_STEP4_GOODWILL_PCT = {"new_unpaid": 0.00, "new_low": 0.05, "mid": 0.10, "high": 0.15}

_SEVERE_KEYWORDS = ("legal", "lawyer", "police", "court", "sue", "consumer forum", "fraud", "cheat")


def _is_severe(reason: str) -> bool:
    lowered = (reason or "").lower()
    return any(kw in lowered for kw in _SEVERE_KEYWORDS)


def _credit(user_id: str, booking_id: str, coins: int, purpose: str) -> tuple:
    """Returns (credit_id, succeeded). "Nothing owed" (coins <= 0) and "the
    real credit call failed" both come back as credit_id=None, but callers
    MUST check `succeeded` — a failed credit must never be reported to the
    visitor as coins landing, and record_credit() (which feeds the dedupe
    check above) must never fire for a credit that didn't actually happen,
    or a legitimate retry would be silently blocked forever."""
    if coins <= 0:
        return None, True
    result = coin_credit_client.credit(user_id, booking_id, coins, purpose)
    if not result["success"]:
        return None, False
    redash_client.record_credit(user_id, booking_id, purpose, coins)
    return result["credit_id"], True


def decide(user_id: str, booking_id: str, issue_tag: str, reason: str = "") -> dict:
    """The one place a Step 1-4 refund/bonus decision gets made. Never
    raises — an unrecognized issue_tag falls through to Step 4, the
    safest default (escalate rather than silently refund or deny).
    """
    booking = redash_client.get_booking_details(user_id, booking_id)
    tier = redash_client.get_ltv_tier(user_id)
    base = {"tier": tier, "booking": booking, "route_to_ticket": False,
            "refund_coins": 0, "bonus_coins": 0, "total_coins": 0, "credit_id": None}

    # Cross-cutting: already credited for this exact booking -> never
    # double-credit, just say so.
    existing = redash_client.get_refund_goodwill_history(user_id, booking_id)
    if existing:
        return {**base, "step": None, "reason_code": "already_credited", "existing": existing}

    # Cross-cutting: severe/legal-threat language skips any bonus
    # entirely regardless of tier or step, straight to a ticket.
    if _is_severe(reason):
        return {**base, "step": 4, "route_to_ticket": True, "reason_code": "severe_language_no_bonus"}

    coins_deducted = booking.get("coins_deducted", 0)

    if issue_tag in STEP1_ASTROLOGER_FAULT:
        credit_id, ok = _credit(user_id, booking_id, coins_deducted, "refund")
        if not ok:
            return {**base, "step": 1, "route_to_ticket": True, "reason_code": "step1_credit_failed"}
        return {**base, "step": 1, "refund_coins": coins_deducted, "total_coins": coins_deducted,
                "credit_id": credit_id, "reason_code": "step1_full_refund"}

    if issue_tag in STEP2_USER_FAULT:
        return {**base, "step": 2, "reason_code": "step2_user_fault_no_refund"}

    if issue_tag in STEP3_PARTIAL_FAULT:
        rule = _STEP3_BONUS[tier]
        bonus = min(round(coins_deducted * rule["pct"]), rule["cap_per_incident"]) if rule["pct"] else 0
        monthly_count = redash_client.get_monthly_bonus_count(user_id)
        route_to_ticket = False
        if rule["cap_per_month"] is not None and monthly_count >= rule["cap_per_month"]:
            bonus = 0  # over this tier's monthly bonus cap — still gets the unused-coins refund below
        elif tier == "high" and monthly_count >= 1:
            # High tier's bonus cap is "unlimited", but the 2nd+ in a
            # month is routed to a human rather than kept fully automatic.
            route_to_ticket = True
        total = coins_deducted + bonus
        credit_id, ok = _credit(user_id, booking_id, total, "refund_plus_bonus")
        if not ok:
            return {**base, "step": 3, "route_to_ticket": True, "reason_code": "step3_credit_failed"}
        return {**base, "step": 3, "route_to_ticket": route_to_ticket, "refund_coins": coins_deducted,
                "bonus_coins": bonus, "total_coins": total, "credit_id": credit_id,
                "reason_code": "step3_partial_fault"}

    # Step 4 (STEP4_NO_SIGNAL, or anything unrecognized): escalate, with
    # an optional small goodwill sized at half Step 3's percentage.
    goodwill_pct = _STEP4_GOODWILL_PCT[tier]
    goodwill = round(coins_deducted * goodwill_pct) if goodwill_pct else 0
    credit_id, ok = _credit(user_id, booking_id, goodwill, "goodwill_pending_ticket")
    if not ok:
        return {**base, "step": 4, "route_to_ticket": True, "reason_code": "step4_credit_failed",
                "mandatory_human_followup": True}
    return {**base, "step": 4, "route_to_ticket": True, "bonus_coins": goodwill, "total_coins": goodwill,
            "credit_id": credit_id, "reason_code": "step4_escalate",
            "mandatory_human_followup": tier == "high"}


# Pure retention gesture — no disputed booking at all (credit_coins with
# booking_id omitted). Deliberately NOT part of the SOP above: there's no
# session to compute "unused coins" from, so this is a flat, small,
# clearly-separate goodwill amount, not a percentage of anything.
_RETENTION_FLAT_AMOUNT = {"new_unpaid": 0, "new_low": 20, "mid": 35, "high": 50}


def decide_retention(user_id: str) -> dict:
    tier = redash_client.get_ltv_tier(user_id)
    amount = _RETENTION_FLAT_AMOUNT[tier]
    if amount <= 0:
        return {"tier": tier, "total_coins": 0, "credit_id": None, "reason_code": "new_unpaid_ineligible"}
    result = coin_credit_client.credit(user_id, None, amount, "retention")
    if not result["success"]:
        return {"tier": tier, "total_coins": 0, "credit_id": None, "reason_code": "retention_credit_failed"}
    redash_client.record_credit(user_id, None, "retention", amount)
    return {"tier": tier, "total_coins": amount, "credit_id": result["credit_id"], "reason_code": "retention_gesture"}
