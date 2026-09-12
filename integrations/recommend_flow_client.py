"""MOCKED — stands in for the app's EXISTING astrologer recommend/matching
system. This bot never runs its own ranking/matching query — trigger()
only ever picks the first available astrologer as a placeholder for "the
real system decided this." Going live is a one-line change: replace
pick_best_match()'s body with a real call into that system.

Astrologer roster shape/data matches the real AstroLokal app (see the
Figma reference): name, specialty tags, languages, per-minute coin
pricing, live availability.

The rendered card is ALWAYS the anonymous "connect with a top astrologer
on the platform" card — no name, no photo, ever. Even when the visitor
named someone specific and search_astrologers resolved a real record
(astrologer_id set below), that record is only used internally (real
availability for notify_me_subscribe, and the id still reaches the native
bridge so tapping Connect routes to the right person) — it never
determines what the visitor sees on the card itself.
"""
import re

_TITLE_WORDS = ("astro", "astrologer", "pandit", "acharya", "guru", "guruji", "dr", "tarot", "vedic")

ASTROLOGERS = [
    {
        "id": "mahalakshmi",
        "name": "Mahalakshmi",
        "specialty": "Face reading, Palm reading, Numerology",
        "languages": "Hindi, English, Telugu",
        "experience": "10+ years",
        "consultations": "2,400+ consultations",
        "rating": 4.4,
        "price": "10/min",
        "price_original": "56/min",
        "availability": "Available now",
    },
    {
        "id": "samrat",
        "name": "Samrat",
        "specialty": "Face reading, Tarot, Vedic",
        "languages": "Hindi, English, Telugu, Marathi",
        "experience": "7+ years",
        "consultations": "1,000+ consultations",
        "rating": 4.6,
        "price": "12/min",
        "price_original": None,
        "availability": "Busy, wait ~15 min",
    },
    {
        "id": "nidhi",
        "name": "Nidhi",
        "specialty": "Face reading, Palm reading, Numerology",
        "languages": "Hindi, English, Telugu",
        "experience": "3+ years",
        "consultations": "600+ consultations",
        "rating": 4.4,
        "price": "15/min",
        "price_original": "25/min",
        "availability": "Available now",
    },
]

CONNECT_LABELS = {
    "en": "Connect now", "hi": "अभी जोड़ें",
    "ta": "இப்போது இணைக்க", "te": "ఇప్పుడు కనెక్ట్ చేయండి",
    "ml": "ഇപ്പോൾ ബന്ധിപ്പിക്കുക",
}

# The one and only card copy — platform-level, identical whether or not a
# specific astrologer was resolved internally, since no individual is ever
# named or pictured on the card itself.
CARD_TEXT = {
    "en": {"title": "Connect with a top astrologer", "subtitle": "Available on AstroLokal right now"},
    "hi": {"title": "एक टॉप ज्योतिषी से जुड़ें", "subtitle": "अभी AstroLokal पर उपलब्ध"},
    "ta": {"title": "சிறந்த ஜோதிடருடன் இணையுங்கள்", "subtitle": "இப்போது AstroLokal-இல் கிடைக்கிறார்கள்"},
    "te": {"title": "అత్యుత్తమ జ్యోతిష్కుడితో కనెక్ట్ అవ్వండి", "subtitle": "ఇప్పుడు AstroLokal‌లో అందుబాటులో ఉన్నారు"},
    "ml": {"title": "മികച്ച ജ്യോതിഷിയുമായി ബന്ധപ്പെടുക", "subtitle": "ഇപ്പോൾ AstroLokal-ൽ ലഭ്യമാണ്"},
}

# Only two trust signals, ever — platform-level (years the roster's been
# running, users served), never a specific astrologer's own stats.
PLATFORM_TRUST = {
    "en": ["10+ years experience", "1,000+ users helped"],
    "hi": ["10+ साल का अनुभव", "1,000+ उपयोगकर्ता"],
    "ta": ["10+ ஆண்டுகள் அனுபவம்", "1,000+ பயனர்கள்"],
    "te": ["10+ సంవత్సరాల అనుభవం", "1,000+ మంది వినియోగదారులు"],
    "ml": ["10+ വർഷത്തെ പരിചയം", "1,000+ ഉപയോക്താക്കൾ"],
}


def get_astrologer(astrologer_id: str):
    for a in ASTROLOGERS:
        if a["id"] == astrologer_id:
            return a
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
    """Placeholder for the real matching system — first available astrologer."""
    for a in ASTROLOGERS:
        if not a["availability"].lower().startswith("busy"):
            return a
    return ASTROLOGERS[0]


def trigger(lang: str, astrologer_id: str = None) -> dict:
    """Builds the connect_popup UI action. The card the visitor sees is
    always the same anonymous "connect with a top astrologer" card with
    platform trust stats — astrologer_id (when the visitor named someone
    and search_astrologers resolved them) only affects internal fields:
    real availability, and the id passed to the native bridge so Connect
    still routes to that person. It's never shown on the card."""
    label = CONNECT_LABELS.get(lang, CONNECT_LABELS["en"])
    named = get_astrologer(astrologer_id) if astrologer_id else None
    astrologer = named or pick_best_match()
    return {
        "type": "connect_popup",
        "display_mode": "specific" if named else "general",
        "label": label,
        "astrologer": astrologer,
        "card": {
            **CARD_TEXT.get(lang, CARD_TEXT["en"]),
            "trust": PLATFORM_TRUST.get(lang, PLATFORM_TRUST["en"]),
        },
    }
