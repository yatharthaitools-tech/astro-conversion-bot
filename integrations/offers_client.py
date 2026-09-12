"""MOCKED — stands in for the app's real active-offers/promotions feed.

Static rather than per-user-random since a given offer is the same for
everyone at a point in time. Replace ACTIVE_OFFERS with a real fetch when
this goes live — same mocked-then-real pattern as the rest of integrations/.
"""

ACTIVE_OFFERS = [
    {"title": "20% extra coins", "detail": "On recharges of ₹300 or more", "code": None},
    {"title": "First recharge bonus", "detail": "50 bonus coins on your first-ever recharge", "code": "FIRST50"},
]


def get_active_offers() -> list:
    return ACTIVE_OFFERS
