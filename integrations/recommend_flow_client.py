"""MOCKED — stands in for the app's EXISTING astrologer recommend/matching
system. This bot never runs its own ranking/matching query — trigger()
only ever picks the first available astrologer as a placeholder for "the
real system decided this." Going live is a one-line change: replace
pick_best_match()'s body with a real call into that system.

Astrologer roster shape/data matches the real AstroLokal app (see the
Figma reference): name, specialty tags, languages, per-minute coin
pricing, live availability.

The rendered card is a real UI component (script.js's renderConnectCard —
markup + CSS, not an image), and it's ALWAYS the same fixed design — a
showcase for the roster as a category, not a claim that these are the
specific people the visitor will be connected to. Even when the visitor
named someone specific and search_astrologers resolved a real record
(astrologer_id set below), that record is only used internally (real
availability for notify_me_subscribe, and the id still reaches the
native bridge so tapping Connect routes to the right person) — it never
changes what the visitor sees on the card itself, which is why `concern`
no longer affects the card's copy — it's still accepted and still shapes
the bot's own chat text, just not this component.
"""
import re

from integrations import redash_client

_TITLE_WORDS = ("astro", "astrologer", "pandit", "acharya", "guru", "guruji", "dr", "tarot", "vedic")

ASTROLOGERS = [
    {
        "id": "mahalakshmi",
        "expert_id": None,  # real Redash expert_id, once known — see get_astrologer_availability() below
        "name": "Mahalakshmi",
        "specialty": "Face reading, Palm reading, Numerology",
        "languages": "Hindi, English, Telugu",
        "experience": "10+ years",
        "consultations": "2,400+ consultations",
        "rating": 4.4,
        "price": "10/min",
        "price_original": "56/min",
        "availability": "Available now",
        "next_available_at": None,
    },
    {
        "id": "samrat",
        "expert_id": None,
        "name": "Samrat",
        "specialty": "Face reading, Tarot, Vedic",
        "languages": "Hindi, English, Telugu, Marathi",
        "experience": "7+ years",
        "consultations": "1,000+ consultations",
        "rating": 4.6,
        "price": "12/min",
        "price_original": None,
        "availability": "Busy, wait ~15 min",
        "next_available_at": None,  # short wait — the availability text IS the ETA
    },
    {
        "id": "nidhi",
        "expert_id": None,
        "name": "Nidhi",
        "specialty": "Face reading, Palm reading, Numerology",
        "languages": "Hindi, English, Telugu",
        "experience": "3+ years",
        "consultations": "600+ consultations",
        "rating": 4.4,
        "price": "15/min",
        "price_original": "25/min",
        "availability": "Offline right now",
        "next_available_at": "6:00 PM today",  # a real scheduled-return case, distinct from "busy, back in ~N min"
    },
]

CONNECT_LABELS = {
    "en": "Connect now", "hi": "अभी जोड़ें",
    "ta": "இப்போது இணைக்க", "te": "ఇప్పుడు కనెక్ట్ చేయండి",
    "ml": "ഇപ്പോൾ ബന്ധിപ്പിക്കుக",
}

def _with_live_availability(astrologer: dict) -> dict:
    """Overlays real Redash availability (get_astrologer_availability) when
    this astrologer's expert_id is known and the query succeeds; otherwise
    returns the mocked entry unchanged. Real data only knows online/offline
    + a predicted ETA, not this mock's "busy, wait ~N min" nuance — once
    expert_id is set, that field's mocked "Busy..." text stops applying."""
    expert_id = astrologer.get("expert_id")
    if not expert_id:
        return astrologer
    live = redash_client.get_astrologer_availability(expert_id)
    if live is None:
        return astrologer
    merged = dict(astrologer)
    merged["availability"] = "Available now" if live["is_online_now"] else "Offline right now"
    merged["next_available_at"] = live["next_available_at"]
    return merged


def get_astrologer(astrologer_id: str):
    for a in ASTROLOGERS:
        if a["id"] == astrologer_id:
            return _with_live_availability(a)
    return None


def search(query: str) -> list:
    """Fuzzy, partial-name lookup over the real roster — handles a bare
    first name, a nickname, or an 'Astro <name>'-style title prefix.
    Returns 0 matches (nobody by that name), 1 (unambiguous), or 2+
    (genuinely ambiguous — caller must ask which one). Only 3 people
    today so ambiguity is unlikely in practice, but the mechanism is real:
    this is meant to scale to the actual roster, not this mock's size.
    Still useful even though the card never shows a name: it's what lets
    the bot correctly resolve an id for availability checks and for the
    native bridge to route to the right person on Connect.
    """
    normalized = (query or "").lower().strip()
    for title in _TITLE_WORDS:
        normalized = re.sub(rf"\b{title}\b", "", normalized).strip()
    if not normalized:
        return []

    matches = []
    for a in ASTROLOGERS:
        name_lower = a["name"].lower()
        tokens = name_lower.split()
        if normalized == name_lower or normalized in name_lower or any(
            normalized == t or normalized in t for t in tokens
        ):
            matches.append({"id": a["id"], "name": a["name"]})
    return matches


def pick_best_match():
    """Placeholder for the real matching system — first genuinely
    available astrologer (not busy, not offline)."""
    for a in ASTROLOGERS:
        live = _with_live_availability(a)
        if live["next_available_at"] is None and not live["availability"].lower().startswith("busy"):
            return live
    return _with_live_availability(ASTROLOGERS[0])


def trigger(lang: str, astrologer_id: str = None, concern: str = None) -> dict:
    """Builds the connect_popup UI action. The card the visitor sees is
    always the same fixed design (rendered client-side as a component,
    not an image) — astrologer_id (when the visitor named someone and
    search_astrologers resolved them) only
    affects internal fields: real availability, and the id passed to the
    native bridge so Connect still routes to that person. It's never
    shown on the card. `concern` is accepted for the agent's own chat
    text but no longer changes the card itself — there's no text left on
    it to template, it's one fixed image."""
    label = CONNECT_LABELS.get(lang, CONNECT_LABELS["en"])
    named = get_astrologer(astrologer_id) if astrologer_id else None
    astrologer = named or pick_best_match()

    return {
        "type": "connect_popup",
        "display_mode": "specific" if named else "general",
        "label": label,
        "astrologer": astrologer,
    }
