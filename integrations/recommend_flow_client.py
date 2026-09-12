"""MOCKED — stands in for the app's EXISTING astrologer recommend/matching
system. This bot never runs its own ranking/matching query — trigger()
only ever picks the first available astrologer as a placeholder for "the
real system decided this." Going live is a one-line change: replace
pick_best_match()'s body with a real call into that system.

Astrologer roster shape/data matches the real AstroLokal app (see the
Figma reference): name, specialty tags, languages, per-minute coin
pricing, live availability.

The rendered card is ALWAYS the anonymous "top astrologers on the
platform" showcase card — no specific name, no one person's photo, ever.
The 3-avatar collage (static/avatars/collage-*.svg) is decorative brand
imagery representing the roster as a category, not a claim that these are
the specific people the visitor will be connected to — same reasoning as
before, just styled as a group shot instead of a blank line of text. Even
when the visitor named someone specific and search_astrologers resolved a
real record (astrologer_id set below), that record is only used
internally (real availability for notify_me_subscribe, and the id still
reaches the native bridge so tapping Connect routes to the right person)
— it never determines what the visitor sees on the card itself.
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
    "ml": "ഇപ്പോൾ ബന്ധിപ്പിക്കుக",
}

# Decorative showcase collage — real portrait photos representing
# "astrologers on the platform" as a category. Always these same 3 images
# regardless of who's actually matched; never implies a specific identity
# (the front/center photo isn't "your astrologer", just the visual lead).
COLLAGE_IMAGES = [
    "/static/avatars/avatar_female.png",
    "/static/avatars/avatar_male.png",
    "/static/avatars/avatar_senior_male.png",
]

# Headline stays constant; only the subtitle is personalized per concern.
CARD_TEXT = {
    "en": {"title": "Get guidance from our best astrologers", "badge": "Top astrologers for you"},
    "hi": {"title": "हमारे सबसे अच्छे ज्योतिषियों से सलाह लें", "badge": "आपके लिए टॉप ज्योतिषी"},
    "ta": {"title": "எங்கள் சிறந்த ஜோதிடர்களிடமிருந்து வழிகாட்டுதல் பெறுங்கள்", "badge": "உங்களுக்கான சிறந்த ஜோதிடர்கள்"},
    "te": {"title": "మా అత్యుత్తమ జ్యోతిష్కుల నుండి మార్గదర్శనం పొందండి", "badge": "మీ కోసం టాప్ జ్యోతిష్కులు"},
    "ml": {"title": "ഞങ്ങളുടെ മികച്ച ജ്യോതിഷികളിൽ നിന്ന് മാർഗ്ഗനിർദ്ദേശം നേടൂ", "badge": "നിങ്ങൾക്കായി മികച്ച ജ്യോതിഷികൾ"},
}

# {concern} is substituted from CONCERN_WORDS below — never left as a raw
# placeholder; falls back to the "general" entry when the concern is
# unset or not recognized.
SUBTITLE_TEMPLATE = {
    "en": "We'll connect you with an astrologer who understands {concern}.",
    "hi": "हम आपको ऐसे ज्योतिषी से जोड़ेंगे जो {concern} को अच्छे से समझते हैं।",
    "ta": "{concern} புரிந்துகொள்ளும் ஜோதிடருடன் உங்களை இணைப்போம்.",
    "te": "{concern} అర్థం చేసుకునే జ్యోతిష్కుడితో మిమ్మల్ని కనెక్ట్ చేస్తాము.",
    "ml": "{concern} മനസ്സിലാക്കുന്ന ഒരു ജ്യോതിഷിയുമായി ഞങ്ങൾ നിങ്ങളെ ബന്ധിപ്പിക്കും.",
}

CONCERN_WORDS = {
    "career": {
        "en": "career pressure", "hi": "करियर की उलझन", "ta": "தொழில் கவலைகளை",
        "te": "కెరీర్ ఒత్తిడిని", "ml": "കരിയർ സമ്മർദ്ദം",
    },
    "love": {
        "en": "relationship stuff", "hi": "रिश्तों की उलझन", "ta": "உறவு பிரச்சினைகளை",
        "te": "సంబంధాల సమస్యలను", "ml": "ബന്ധങ്ങളിലെ പ്രശ്‌നങ്ങൾ",
    },
    "finance": {
        "en": "money worries", "hi": "पैसों की चिंता", "ta": "பண கவலைகளை",
        "te": "డబ్బు ఆందోళనలను", "ml": "സാമ്പത്തിക ആശങ്കകൾ",
    },
    "marriage": {
        "en": "marriage concerns", "hi": "शादी से जुड़ी बातें", "ta": "திருமண கவலைகளை",
        "te": "వివాహ సమస్యలను", "ml": "വിവാഹ പ്രശ്‌നങ്ങൾ",
    },
    "general": {
        "en": "what you're going through", "hi": "आपकी बात", "ta": "நீங்கள் சொல்வதை",
        "te": "మీరు చెప్పింది", "ml": "നിങ്ങൾ പറയുന്നത്",
    },
}

# 3 platform-level trust signals, ever — never a specific astrologer's own
# stats, since no individual is ever named or pictured on the card.
PLATFORM_TRUST = {
    "en": [
        {"icon": "years", "value": "10+ years", "label": "average experience"},
        {"icon": "users", "value": "1,000+", "label": "users helped"},
        {"icon": "rating", "value": "4.8 ★", "label": "average rating"},
    ],
    "hi": [
        {"icon": "years", "value": "10+ साल", "label": "औसत अनुभव"},
        {"icon": "users", "value": "1,000+", "label": "उपयोगकर्ता"},
        {"icon": "rating", "value": "4.8 ★", "label": "औसत रेटिंग"},
    ],
    "ta": [
        {"icon": "years", "value": "10+ ஆண்டுகள்", "label": "சராசரி அனுபவம்"},
        {"icon": "users", "value": "1,000+", "label": "பயனர்கள்"},
        {"icon": "rating", "value": "4.8 ★", "label": "சராசரி மதிப்பீடு"},
    ],
    "te": [
        {"icon": "years", "value": "10+ సంవత్సరాలు", "label": "సగటు అనుభవం"},
        {"icon": "users", "value": "1,000+", "label": "వినియోగదారులు"},
        {"icon": "rating", "value": "4.8 ★", "label": "సగటు రేటింగ్"},
    ],
    "ml": [
        {"icon": "years", "value": "10+ വർഷം", "label": "ശരാശരി പരിചയം"},
        {"icon": "users", "value": "1,000+", "label": "ഉപയോക്താക്കൾ"},
        {"icon": "rating", "value": "4.8 ★", "label": "ശരാശരി റേറ്റിംഗ്"},
    ],
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


def trigger(lang: str, astrologer_id: str = None, concern: str = None) -> dict:
    """Builds the connect_popup UI action. The card the visitor sees is
    always the same anonymous showcase card with platform trust stats —
    astrologer_id (when the visitor named someone and search_astrologers
    resolved them) only affects internal fields: real availability, and
    the id passed to the native bridge so Connect still routes to that
    person. It's never shown on the card. `concern` (career/love/finance/
    marriage/general) only personalizes the subtitle wording — cosmetic,
    never a business decision."""
    label = CONNECT_LABELS.get(lang, CONNECT_LABELS["en"])
    named = get_astrologer(astrologer_id) if astrologer_id else None
    astrologer = named or pick_best_match()

    card_text = CARD_TEXT.get(lang, CARD_TEXT["en"])
    subtitle_template = SUBTITLE_TEMPLATE.get(lang, SUBTITLE_TEMPLATE["en"])
    concern_words = CONCERN_WORDS.get(concern or "general", CONCERN_WORDS["general"])
    concern_word = concern_words.get(lang, concern_words["en"])

    return {
        "type": "connect_popup",
        "display_mode": "specific" if named else "general",
        "label": label,
        "astrologer": astrologer,
        "card": {
            "badge": card_text["badge"],
            "title": card_text["title"],
            "subtitle": subtitle_template.format(concern=concern_word),
            "images": COLLAGE_IMAGES,
            "trust": PLATFORM_TRUST.get(lang, PLATFORM_TRUST["en"]),
        },
    }
