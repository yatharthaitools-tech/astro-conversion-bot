"""Real call to AstroLokal's system-transactions API — confirmed against
a working example (curl, Basic Auth, exact payload shape) for the DEV
environment specifically:

    POST <COIN_CREDIT_API_URL>  (default: dev-api.astrolokal.com)
    Authorization: Basic <COIN_CREDIT_API_AUTH>
    Body (a JSON ARRAY of one object):
        [{"userId": ..., "amount": "10.00", "purpose": "promo",
          "description": ..., "source": "astro_conversion_bot"}]

Whatever value COIN_CREDIT_API_AUTH holds is explicitly a DEV-ONLY
credential — a separate production credential will be issued once dev
is confirmed working. Don't assume a dev credential works against
api.astrolokal.com (production); one was confirmed to NOT (real 401,
"Invalid username/password") before dev-api.astrolokal.com turned out
to be the actual intended host for it.

Falls back to the old mocked no-op path when COIN_CREDIT_API_AUTH isn't
set — this moves real money/coins even on dev, so it only goes live
when someone explicitly sets the credential.

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

UNCONFIRMED, flagged for whoever runs the first live test:
  - success/failure response shape — currently just "2xx status code",
    nothing more specific parsed out of the body yet.
"""
import logging
import os
import uuid

import requests

logger = logging.getLogger(__name__)

COIN_CREDIT_API_URL = os.environ.get(
    'COIN_CREDIT_API_URL', 'https://dev-api.astrolokal.com/v1/system-transactions/'
)
COIN_CREDIT_API_AUTH = os.environ.get('COIN_CREDIT_API_AUTH', '')


def _as_user_id(user_id: str):
    return int(user_id) if str(user_id).isdigit() else user_id


def credit(user_id: str, booking_id: str, amount: int, reason: str) -> dict:
    if not COIN_CREDIT_API_AUTH:
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
        logger.error(
            "coin_credit_client: credit FAILED user=%s booking=%s amount=%s reason=%s: %s",
            user_id, booking_id, amount, reason, exc,
        )
        return {"success": False, "credit_id": None, "amount": 0}

    credit_id = uuid.uuid4().hex[:10]  # API's own transaction-id field, if any, not yet confirmed
    logger.info(
        "coin_credit_client: credited user=%s booking=%s amount=%s reason=%s status=%s",
        user_id, booking_id, amount, reason, response.status_code,
    )
    return {"success": True, "credit_id": credit_id, "amount": amount}
