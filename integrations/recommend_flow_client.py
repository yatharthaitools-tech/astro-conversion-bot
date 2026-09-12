"""MOCKED — stands in for the app's EXISTING astrologer recommend/matching
system. This bot never runs its own ranking/matching query — trigger()
only ever picks the first available astrologer as a placeholder for "the
real system decided this." Going live is a one-line change: replace
pick_best_match()'s body with a real call into that system.

Astrologer roster shape/data matches the real AstroLokal app (see the
Figma reference): name, specialty tags, languages, per-minute coin
pricing, live availability.
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
        "image": "https://ui-avatars.com/api/?name=Mahalakshmi&background=ff8a5c&color=fff&size=128",
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
        "image": "https://ui-avatars.com/api/?name=Samrat&background=e8623d&color=fff&size=128",
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
        "image": "https://ui-avatars.com/api/?name=Nidhi&background=ff6f47&color=fff&size=128",
    },
]

CONNECT_LABELS = {
    "en": "Connect now", "hi": "अभी जोड़ें",
    "ta": "இப்போது இணைக்க", "te": "ఇప్పుడు కనెక్ట్ చేయండి",
    "ml": "ഇപ്പോൾ ബന്ധിപ്പിക്കുക",
}

# Kept modest on purpose — there's no real astrologer named here, so no
# invented stat/trust-claim is attached to this variant. Just a plain,
# honest line that reads as a continuation of the conversation.
GENERIC_CARD_TEXT = {
    "en": {"title": "A good match is online right now", "subtitle": "Picked based on what you've shared"},
    "hi": {"title": "अभी एक अच्छा मैच उपलब्ध है", "subtitle": "आपकी बात के आधार पर चुना गया"},
    "ta": {"title": "பொருத்தமான ஒருவர் இப்போது இருக்கிறார்", "subtitle": "நீங்கள் பகிர்ந்ததன் அடிப்படையில் தேர்ந்தெடுக்கப்பட்டது"},
    "te": {"title": "సరైన వ్యక్తి ఇప్పుడు అందుబాటులో ఉన్నారు", "subtitle": "మీరు చెప్పిన దాని ఆధారంగా ఎంచుకోబడింది"},
    "ml": {"title": "അനുയോജ്യമായ ഒരാൾ ഇപ്പോൾ ഓൺലൈനിലുണ്ട്", "subtitle": "നിങ്ങൾ പറഞ്ഞതിന്റെ അടിസ്ഥാനത്തിൽ തിരഞ്ഞെടുത്തത്"},
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
    """Builds the connect_popup UI action. astrologer_id set (visitor asked
    for someone by name) -> 'specific' display, real name/photo. Omitted
    -> 'general' display, no name/photo revealed (matching isn't this
    bot's job)."""
    label = CONNECT_LABELS.get(lang, CONNECT_LABELS["en"])
    named = get_astrologer(astrologer_id) if astrologer_id else None
    if named:
        return {
            "type": "connect_popup",
            "display_mode": "specific",
            "label": label,
            "astrologer": named,
        }
    return {
        "type": "connect_popup",
        "display_mode": "general",
        "label": label,
        "astrologer": pick_best_match(),
        "generic": GENERIC_CARD_TEXT.get(lang, GENERIC_CARD_TEXT["en"]),
    }
