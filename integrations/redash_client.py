"""MOCKED — replace with real Redash query calls.

Real shape this stands in for (per the architecture doc): booking history
(bookings_booking + wallet deductions join), payment/order status
(orders_order + orders_payment + razorpay_status), refund/goodwill history
(wallets_systemtransactionlog filtered on purpose IN ('refund','goodwill')),
and LTV/lifetime-spend. Each real query would replace one function body
below — callers only ever see the function signature, same pattern as
AstroHelp's integrations/.

Deterministic per-user (hash of user_id), not random — same user always
gets the same mock data, stable across a demo/test session.
"""
import hashlib


def _seed(user_id: str, salt: str) -> int:
    return int(hashlib.sha256(f"{user_id}:{salt}".encode()).hexdigest(), 16)


def get_lifetime_spend(user_id: str) -> int:
    """Total ₹ ever spent by this user. ~20% land at 0 (New/Unpaid) by design."""
    bucket = _seed(user_id, "spend") % 100
    if bucket < 20:
        return 0
    if bucket < 55:
        return 50 + (_seed(user_id, "spend2") % 150)  # New-Low: 50-200
    if bucket < 85:
        return 200 + (_seed(user_id, "spend3") % 800)  # Mid: 200-1000
    return 1000 + (_seed(user_id, "spend4") % 4000)  # High: 1000-5000


def get_ltv_tier(user_id: str) -> str:
    spend = get_lifetime_spend(user_id)
    if spend == 0:
        return "new_unpaid"
    if spend <= 200:
        return "new_low"
    if spend <= 1000:
        return "mid"
    return "high"


def get_payment_status(user_id: str) -> dict:
    """Most recent recharge/payment attempt."""
    outcome = _seed(user_id, "payment") % 10
    if outcome < 7:
        return {
            "status": "success",
            "coins_added": 100 + (_seed(user_id, "coins") % 400),
            "order_time": "2 hours ago",
        }
    if outcome < 9:
        return {
            "status": "pending",
            "coins_added": 0,
            "order_time": "20 minutes ago",
            "provider_refund_eta_days": None,
        }
    return {
        "status": "failed",
        "coins_added": 0,
        "order_time": "1 hour ago",
        "provider_refund_eta_days": 7,
    }


def get_booking_details(user_id: str, booking_id: str = None) -> dict:
    """A specific booking, or the most recent one if booking_id is omitted."""
    bid = booking_id or f"bk_{_seed(user_id, 'latest_booking') % 100000}"
    duration = _seed(f"{user_id}:{bid}", "duration") % 900  # 0-900 seconds
    astrologer_messages = _seed(f"{user_id}:{bid}", "msgs") % 5
    return {
        "booking_id": bid,
        "astrologer_id": ["mahalakshmi", "samrat", "nidhi"][_seed(f"{user_id}:{bid}", "astro") % 3],
        "duration_seconds": duration,
        "coins_deducted": max(10, duration // 10),
        "astrologer_message_count": astrologer_messages,
        "status": "completed" if duration > 0 else "ended_early",
    }


# In-memory ledger of credits issued THIS process, keyed by (user_id,
# booking_id). Needed because the hash-based mock below is a pure function
# with no memory of its own — without this, a credit issued moments ago in
# the same request (e.g. by refund_service) would be invisible to a
# get_refund_goodwill_history() call made right after, defeating the whole
# point of the dedupe check. A real Redash query wouldn't have this gap
# (the write would already be visible), so this is a mock-specific fix, not
# something that carries over when this goes real.
_credited_this_process: dict = {}


def record_credit(user_id: str, booking_id: str, purpose: str, amount: int) -> None:
    """Called by coin_credit_client.credit()'s callers right after a
    successful credit, so subsequent dedupe checks in this same run see it."""
    _credited_this_process[(user_id, booking_id)] = {
        "booking_id": booking_id,
        "purpose": purpose,
        "amount": amount,
        "credited_at": "just now",
    }


def get_wallet_status(user_id: str) -> dict:
    """Current coin balance + whether anything was actually deducted
    recently — for a 'coins keep going down / going missing' complaint
    that isn't about one specific booking (see get_booking_details for
    that case)."""
    balance = 50 + (_seed(user_id, "wallet_balance") % 950)
    recent_deduction = _seed(user_id, "wallet_recent") % 5 == 0
    return {
        "balance": balance,
        "recent_deduction": recent_deduction,
        "recent_deduction_reason": "a completed consultation" if recent_deduction else None,
    }


def get_queue_position(user_id: str) -> dict:
    """Real position + estimated wait in an astrologer's live queue, for
    a visitor already waiting to connect."""
    position = 1 + (_seed(user_id, "queue_position") % 6)
    return {"position": position, "estimated_wait_minutes": position * 3}


def get_refund_goodwill_history(user_id: str, booking_id: str) -> list:
    """Prior refund/goodwill credits already issued for this booking — used
    to prevent double-crediting the same booking_id."""
    recent = _credited_this_process.get((user_id, booking_id))
    if recent:
        return [recent]

    already_credited = _seed(f"{user_id}:{booking_id}", "already_credited") % 10 == 0
    if not already_credited:
        return []
    return [{
        "booking_id": booking_id,
        "purpose": "refund",
        "amount": 50,
        "credited_at": "yesterday",
    }]
