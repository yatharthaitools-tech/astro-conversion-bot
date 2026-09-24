"""Real call to AstroLokal's system-transactions API — confirmed against
a working example (curl, Basic Auth, exact payload shape), and
independently cross-checked field-by-field against a sibling app's
(daily-cast) own integration with this exact same API:

    POST <COIN_CREDIT_API_URL>  (default: api.astrolokal.com, production —
                                  no dev environment/credential available)
    Authorization: Basic <COIN_CREDIT_API_AUTH>
    Body (a JSON ARRAY of one object):
        [{"userId": ..., "amount": "10.00", "purpose": "promo",
          "description": ..., "source": "astro_conversion_bot"}]

Falls back to the old mocked no-op path when COIN_CREDIT_API_AUTH isn't
set — this moves real money/coins, so it only goes live when someone
explicitly sets the credential.

`amount` is sent as a string formatted to 2 decimal places ("10.00"),
matching the confirmed-working example exactly — not the int this
module receives it as internally. `userId` is cast to int when it's
purely numeric (matching the confirmed example's bare-number userId),
falling back to the raw string otherwise (a synthetic/non-numeric id
should never reach a real credit call anyway, since the security
boundary in agent/context.py only trusts a real numeric AstroLokal
user_id — see resolve_session()'s oauth_token requirement — but this
keeps the function itself from ever raising on an unexpected id shape).

purpose="promo" and source="astro_conversion_bot" are this bot's own
fixed values (confirmed with the app owner) — not varied per SOP
category. `reason` (this module's own param) still carries the real
refund/bonus/retention distinction into our own dashboard DB via
services/refund_service.py's redash_client.record_credit() call right
after this.

AT-MOST-ONCE FIRING: ported directly from a sibling app's (daily-cast)
own integration with this exact same API, which documents it has "no
idempotency of its own — it credits on every call." Same fix here: an
attempt row with a UNIQUE idempotency key is inserted (via
dashboard.db.reserve_coin_credit_attempt) BEFORE the HTTP call — a
concurrent/repeated call loses the insert race and never fires twice.
This is purely mechanical (the "how" of firing safely); it carries no
opinion on WHEN or HOW MUCH to credit — that decision is entirely
services/refund_service.py's LTV-tiered SOP, unchanged by any of this.
The idempotency key scope differs from daily-cast's own (which is
"once per user per day" — their product's actual redemption rule):
here it's `{user_id}:{booking_id}` for booking-scoped credits (matching
this app's existing dedupe check's own scope), or
`{user_id}:retention:{today}` for retention gestures (no booking_id) —
a default, not yet confirmed as astro-conversion-bot's real retention
cadence.

UNCONFIRMED, flagged for whoever runs the first live test:
  - success/failure response shape — currently just "2xx status code",
    nothing more specific parsed out of the body yet.
"""
import logging
import os
import uuid
from datetime import date

import requests

from dashboard import db as dashboard_db

logger = logging.getLogger(__name__)

COIN_CREDIT_API_URL = os.environ.get(
    'COIN_CREDIT_API_URL', 'https://api.astrolokal.com/v1/system-transactions/'
)
COIN_CREDIT_API_AUTH = os.environ.get('COIN_CREDIT_API_AUTH', '')


def _as_user_id(user_id: str):
    return int(user_id) if str(user_id).isdigit() else user_id


def _idempotency_key(user_id: str, booking_id: str) -> str:
    if booking_id:
        return f"{user_id}:{booking_id}"
    return f"{user_id}:retention:{date.today().isoformat()}"


def credit(user_id: str, booking_id: str, amount: int, reason: str) -> dict:
    # Matches daily-cast's own guard in fireCoinCredit() — reject before
    # ever touching the gate or the network, not after.
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("coin_credit_client.credit: amount must be a positive integer")

    idempotency_key = _idempotency_key(user_id, booking_id)
    live = bool(COIN_CREDIT_API_AUTH)

    won = dashboard_db.reserve_coin_credit_attempt(
        idempotency_key, user_id, booking_id, amount, "firing" if live else "stub_credited",
        purpose=reason,
    )
    if not won:
        logger.info("coin_credit_client: duplicate suppressed for key=%s", idempotency_key)
        return {"success": False, "credit_id": None, "amount": 0}

    if not live:
        credit_id = uuid.uuid4().hex[:10]
        logger.info(
            "[MOCK coin_credit_client] credited user=%s booking=%s amount=%s reason=%s credit_id=%s",
            user_id, booking_id, amount, reason, credit_id,
        )
        return {"success": True, "credit_id": credit_id, "amount": amount}

    try:
        response = requests.post(
            COIN_CREDIT_API_URL,
            json=[{
                "userId": _as_user_id(user_id),
                "amount": f"{amount:.2f}",
                "purpose": "promo",
                "description": booking_id or reason,
                "source": "astro_conversion_bot",
            }],
            headers={"Authorization": f"Basic {COIN_CREDIT_API_AUTH}"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        dashboard_db.finalize_coin_credit_attempt(idempotency_key, "error", error=str(exc))
        logger.error(
            "coin_credit_client: credit FAILED user=%s booking=%s amount=%s reason=%s: %s",
            user_id, booking_id, amount, reason, exc,
        )
        return {"success": False, "credit_id": None, "amount": 0}

    dashboard_db.finalize_coin_credit_attempt(
        idempotency_key, "success", http_status=response.status_code, response=response.text,
    )
    credit_id = uuid.uuid4().hex[:10]  # API's own transaction-id field, if any, not yet confirmed
    logger.info(
        "coin_credit_client: credited user=%s booking=%s amount=%s reason=%s status=%s",
        user_id, booking_id, amount, reason, response.status_code,
    )
    return {"success": True, "credit_id": credit_id, "amount": amount}
