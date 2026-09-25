"""Shapes a coin credit into the free-coins payload shown in-chat.

The real native app's WebView bridge only implements two actions —
start_random_flow and close_webview — there's no native free-coins
bottomsheet to hand this to. A credit happens via
services/ltv_service.py -> integrations/coin_credit_client.py exactly
like any other credit; this module's only job is shaping the RESULT of
that credit into the `freeCoins` shape below for script.js's
showCoinsCreditedCard to render entirely in-chat, followed straight into
the connect card (see agent/tool_registry.py's _handle_credit_coins).
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
