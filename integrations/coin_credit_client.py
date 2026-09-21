"""Real call to AstroLokal's system-transactions API — the same endpoint
the "Refund Processing" step of the "Astro Refund Workflow" n8n pipeline
calls, confirmed directly against that node's own config:

    POST <COIN_CREDIT_API_URL>
    Authorization: Basic <COIN_CREDIT_API_AUTH>
    Body (a JSON ARRAY of one object — confirmed shape, not guessed):
        [{"userId": ..., "amount": ..., "purpose": "goodwill",
          "description": ..., "source": "n8n"}]

Falls back to the old mocked no-op path when COIN_CREDIT_API_AUTH isn't
set — same posture as every other real-vs-mock integration in this app,
and deliberately NOT defaulted to anything: this moves real money, so it
only goes live when someone explicitly sets the credential.

CONFIRMED: the n8n node hardcodes purpose="goodwill" for every
transaction — it does not vary this per refund/bonus/retention. Whether
system-transactions accepts OTHER purpose values (to carry our SOP's
finer-grained refund/bonus/retention distinction into the real ledger)
is UNCONFIRMED — until that's confirmed with whoever owns this API, this
always sends "goodwill" too, matching the one proven-working case,
rather than guessing at values that might be silently misclassified.
`reason` (this module's own param) still carries the real distinction
into our own dashboard DB via services/refund_service.py's
redash_client.record_credit() call right after this.

UNCONFIRMED, flagged for whoever runs the first live test:
  - success/failure response shape — currently just "2xx status code",
    nothing more specific parsed out of the body yet.
  - whether `amount` needs to be a string (n8n's templating always
    produces strings) or a number — sent here as the int it already is.
"""
import logging
import os
import uuid

import requests

logger = logging.getLogger(__name__)

COIN_CREDIT_API_URL = os.environ.get(
    'COIN_CREDIT_API_URL', 'https://api.astrolokal.com/v1/system-transactions/'
)
COIN_CREDIT_API_AUTH = os.environ.get('COIN_CREDIT_API_AUTH', '')


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
                "userId": user_id,
                "amount": amount,
                "purpose": "goodwill",
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
