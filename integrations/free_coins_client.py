"""Shapes a coin credit into the free-coins bottomsheet payload the
native app already expects — matching the real Startup Config API's
Approach 3 (a separate `POST /v1/bottomsheet/` call, chosen over folding
this into the startup-config response, which needs to stay lightweight
with no UI elements, and over parallel-polling two APIs at app open
where the higher-priority bottomsheet can get blocked by whichever
response lands first).

This bot never calls that real endpoint — a credit happens via
services/ltv_service.py -> integrations/coin_credit_client.py exactly
like any other credit; this module's only job is shaping the RESULT of
that credit into the `freeCoins` JSON shape below so it can be handed
straight to the native bridge (script.js's SHOW_FREE_COINS_BOTTOMSHEET),
and from there straight into the native app's existing bottomsheet UI —
no new rendering needed on either side.

`POST /v1/free-coins/seen-ack/` (acknowledging the visitor saw/dismissed
it, keyed by lastSeenTransactionId) is entirely the native app's own
responsibility once it has that id — this bot has no part in that call.
"""

# Placeholder — swap for the real Lottie asset URL when this goes live;
# has no bearing on the actual coin amount or credit logic, purely visual.
_ANIMATION_URL = "https://cdn.example.com/free-coins/bottomsheet-animation.lottie"


def build_bottomsheet(coins: int, credit_id: str) -> dict:
    coins_text = f"{coins:g}"  # matches the API's numeric coins field without trailing .0 in copy
    return {
        "type": "free_coins_bottomsheet",
        "freeCoins": {
            "coins": float(coins),
            "cta": "Claim now",
            "animationUrl": _ANIMATION_URL,
            "lastSeenTransactionId": credit_id,
            "lottieTextConfigurations": [
                {"text": f"Congrats! You got {coins_text} free coins", "keypath": "key-1"},
                {"text": coins_text, "keypath": "key-2"},
            ],
        },
    }
