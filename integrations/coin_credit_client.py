"""Real call to the "Astro Bot - Credit Coins" n8n webhook — a small
webhook-triggered workflow (Webhook -> HTTP Request) built specifically
for this bot, separate from the existing schedule-triggered "Astro
Refund Workflow". It POSTs to the real system-transactions API
(purpose="promo") on our behalf, using an n8n-stored credential — this
app never needs to hold the astrolokal Basic Auth secret itself.

    POST <COIN_CREDIT_WEBHOOK_URL>
    Body: {"userId": ..., "amount": ..., "description": ...}

Falls back to the old mocked no-op path when COIN_CREDIT_WEBHOOK_URL
isn't set — same posture as every other real-vs-mock integration in
this app, and deliberately NOT defaulted to a guessed URL: this moves
real money, so it only goes live once someone confirms the exact
Production Webhook URL from n8n and sets it explicitly.

UNCONFIRMED, flagged for whoever runs the first live test:
  - success/failure response shape — the webhook's "Last Node" response
    mode proxies back whatever the system-transactions call itself
    returns, currently just treated as "2xx status code = success".
  - whether `amount` needs to be a string or a number — sent as the int
    it already is.
"""
import logging
import os
import uuid

import requests

logger = logging.getLogger(__name__)

COIN_CREDIT_WEBHOOK_URL = os.environ.get('COIN_CREDIT_WEBHOOK_URL', '')


def credit(user_id: str, booking_id: str, amount: int, reason: str) -> dict:
    if not COIN_CREDIT_WEBHOOK_URL:
        credit_id = uuid.uuid4().hex[:10]
        logger.info(
            "[MOCK coin_credit_client] credited user=%s booking=%s amount=%s reason=%s credit_id=%s",
            user_id, booking_id, amount, reason, credit_id,
        )
        return {"success": True, "credit_id": credit_id, "amount": amount}

    try:
        response = requests.post(
            COIN_CREDIT_WEBHOOK_URL,
            json={
                "userId": user_id,
                "amount": amount,
                "description": booking_id or reason,
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error(
            "coin_credit_client: credit FAILED user=%s booking=%s amount=%s reason=%s: %s",
            user_id, booking_id, amount, reason, exc,
        )
        return {"success": False, "credit_id": None, "amount": 0}

    credit_id = uuid.uuid4().hex[:10]  # webhook's own transaction-id field, if any, not yet confirmed
    logger.info(
        "coin_credit_client: credited user=%s booking=%s amount=%s reason=%s status=%s",
        user_id, booking_id, amount, reason, response.status_code,
    )
    return {"success": True, "credit_id": credit_id, "amount": amount}
