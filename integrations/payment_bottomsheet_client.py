"""MOCKED — stands in for the app's EXISTING low-balance/recharge bottom
sheet UI. No new payment integration here: the bot only ever surfaces the
entry point with FIXED package options — it can never set or suggest a
specific amount itself. Going live is a one-line change: replace
RECHARGE_PACKAGES with whatever the real bottom sheet actually offers.
"""

RECHARGE_PACKAGES = [
    {"coins": 100, "price": "₹120"},
    {"coins": 300, "price": "₹320"},
    {"coins": 500, "price": "₹500"},
]

# Real supported payment methods — given back to the model so it can
# answer "can I pay with X" factually instead of guessing.
SUPPORTED_PAYMENT_METHODS = ["UPI", "Cards", "Net Banking", "Wallets", "Amazon Pay"]


def trigger(kind: str) -> dict:
    return {
        "type": "payment_bottomsheet",
        "kind": kind,  # 'low_balance' | 'recharge'
        "packages": RECHARGE_PACKAGES,
        "payment_methods": SUPPORTED_PAYMENT_METHODS,
    }
