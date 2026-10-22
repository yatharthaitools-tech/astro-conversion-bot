import base64
import hmac
import io
import logging
import mimetypes
import os
import re
import urllib.parse
import uuid
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, url_for
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

load_dotenv()

from agent import context as agent_context
from agent import orchestrator as agent_orchestrator
from integrations import faq_client, link_token, photo_storage, recommend_flow_client, s3_client
from services import ticket_service
from dashboard import auth as dashboard_auth
from dashboard import db as dashboard_db
from dashboard.routes import bp as dashboard_bp

app = Flask(__name__)
#  QA: "the uploaded image often doesn't come through" — script.js now
# compresses a photo client-side before upload (resize + re-encode to
# JPEG), which brings almost every real phone photo in well under this,
# but a WebView that can't decode the source (so compression falls back
# to the original file untouched) still needs real headroom: a modern
# phone camera's raw JPEG commonly runs 8-15MB. 5MB was silently
# rejecting a large share of uploads outright.
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB cap on uploaded photos
app.register_blueprint(dashboard_bp)
dashboard_db.init_db()
# admin_users/dashboard_config must exist first (init_db above), and the
# secret needs the table too — hence this order. Only needed for the
# admin dashboard's login session cookie — the main chat widget itself
# has no session/cookie state. A per-process random key broke logins
# under >1 worker/pod: whichever process signed the login cookie was the
# only one that could verify it, so a request landing on a different
# worker looked like an instant logout. get_or_create_session_secret
# fixes that at the root — a real random value generated once and shared
# via Postgres, not derived from anything else — rather than refusing to
# start when ADMIN_SESSION_SECRET isn't set.
app.secret_key = os.environ.get('ADMIN_SESSION_SECRET') or dashboard_db.get_or_create_session_secret()
# Seeds the first admin_users row from ADMIN_EMAIL/ADMIN_PASSWORD — a
# no-op once any account exists, see dashboard/auth.py's bootstrap().
dashboard_auth.bootstrap()

UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads')
# heic/heif: iPhone's native camera format (Settings > Camera > Formats
# > High Efficiency, the default) — script.js's client-side compression
# re-encodes to JPEG before upload when the browser can decode the
# source, but a WebView that can't decode HEIC passes the original
# through unchanged, so the server has to accept it too rather than
# reject a huge share of iPhone photos outright.
ALLOWED_UPLOAD_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'heif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

PRD_INTENTS = {
    'general_guidance': {
        'keywords': ['astrology guidance', 'guidance', 'help', 'need help', 'astrology', 'kundali', 'horoscope'],
        'answers': {
            'en': 'I can help with astrology guidance. Please tell me what you need help with, and I’ll suggest the right consultation.',
            'hi': 'मैं ज्योतिष मार्गदर्शन में मदद कर सकती हूँ। कृपया बताइए कि आपको किस चीज़ में मदद चाहिए, और मैं सही परामर्श सुझाऊँगी।'
        }
    },
    'career': {
        # Keywords below are English + the 4 regional scripts (hi/ta/te/ml)
        # in ONE flat list, same as is_prediction_intent's own multi-
        # language PREDICTION_KEYWORDS — map_intent() doesn't need to know
        # which language it's matching against, substring containment
        # alone is enough (normalize_text already whitelists these script
        # ranges, see its own comment). This is what lets concern_for_
        # intent identify a concern for a regional-language question too,
        # not just English.
        'keywords': [
            'career', 'job', 'business', 'work', 'professional', 'career concern', 'job stability',
            'करियर', 'नौकरी', 'व्यापार', 'व्यवसाय', 'काम', 'जॉब',
            'தொழில்', 'வேலை', 'வியாபாரம்', 'உத்தியோகம்',
            'కెరీర్', 'ఉద్యోగం', 'వ్యాపారం', 'పని',
            'കരിയർ', 'ജോലി', 'ബിസിനസ്സ്',
        ],
        'answers': {
            'en': 'I can help with career-related guidance. A career consultation may be the best fit. Would you like to speak with an astrologer or explore available packages?',
            'hi': 'मैं कैरियर से जुड़े मार्गदर्शन में मदद कर सकती हूँ। कैरियर परामर्श सबसे उपयुक्त हो सकता है। क्या आप ज्योतिषी से बात करना चाहेंगे या उपलब्ध पैकेज देखें?'
        }
    },
    'love': {
        'keywords': [
            'love', 'relationship', 'partner', 'dating', 'romantic', 'love life', 'relationship problem',
            'प्यार', 'रिश्ता', 'रिलेशनशिप', 'पार्टनर', 'प्रेम',
            'காதல்', 'உறவு', 'துணை',
            'ప్రేమ', 'సంబంధం', 'భాగస్వామి',
            'പ്രണയം', 'ബന്ധം', 'പങ്കാളി',
        ],
        'answers': {
            'en': 'I can help with relationship guidance. I can connect you with a relationship specialist or suggest a suitable consultation package.',
            'hi': 'मैं रिश्ते और प्रेम से जुड़े मार्गदर्शन में मदद कर सकती हूँ। मैं आपको रिलेशनशिप स्पेशलिस्ट से जोड़ सकती हूँ या उचित परामर्श पैकेज सुझा सकती हूँ।'
        }
    },
    'finance': {
        'keywords': [
            'finance', 'money', 'financial', 'wealth', 'income', 'business growth', 'financial future',
            'पैसा', 'पैसों', 'वित्त', 'धन', 'आमदनी', 'फाइनेंस',
            'பணம்', 'நிதி', 'வருமானம்',
            'డబ్బు', 'ఆర్థిక', 'ఆదాయం',
            'പണം', 'സാമ്പത്തികം', 'വരുമാനം',
        ],
        'answers': {
            'en': 'I can help with finance-related guidance. A financial astrology consultation may be suitable. Would you like to book a session?',
            'hi': 'मैं वित्त से जुडे़ मार्गदर्शन में मदद कर सकती हूँ। वित्तीय ज्योतिष परामर्श उपयुक्त हो सकता है। क्या आप बैठक बुक करना चाहेंगे?'
        }
    },
    'marriage': {
        # 'married'/'get married' added — 'married' isn't a substring of
        # 'marriage', so "when am i getting married" (a literal
        # PREDICTION_KEYWORDS phrase, hits is_prediction_intent fine) was
        # silently missing this bucket entirely, falling through to
        # concern_for_intent's 'general' default and losing the specific
        # concern the deflection's own connect-card and text both need.
        'keywords': [
            'marriage', 'wedding', 'husband', 'wife', 'marriage prospects', 'shaadi', 'married', 'get married',
            'शादी', 'विवाह', 'पति', 'पत्नी',
            'திருமணம்', 'கல்யாணம்', 'கணவர்', 'மனைவி',
            'పెళ్లి', 'వివాహం', 'భర్త', 'భార్య',
            'വിവാഹം', 'കല്യാണം', 'ഭർത്താവ്', 'ഭാര്യ',
        ],
        'answers': {
            'en': 'I can help with marriage-related guidance. I can suggest the right consultation based on your concern and preferred service.',
            'hi': 'मैं शादी से जुड़े मार्गदर्शन में मदद कर सकती हूँ। आपकी चिंता और पसंद के अनुसार सही परामर्श सुझा सकती हूँ।'
        }
    },
    'consultation_request': {
        # 'want to talk' deliberately removed — it's a clean phrase match
        # even inside "I DON'T want to talk to anyone", so it was firing
        # this intent (and the CTA safety net below) on an explicit
        # decline, confirmed live: "no I don't want to talk to anyone
        # right now, just venting" force-showed the connect card right
        # after the visitor declined it. Too fragile a phrase to
        # keyword-match without real negation handling.
        'keywords': ['talk to astrologer', 'consultation', 'book consultation', 'book a consultation', 'astrologer'],
        'answers': {
            'en': 'Sure. I can help you book a consultation. Please choose the type of consultation you need and your preferred time.',
            'hi': 'बिलकुल। मैं आपको परामर्श बुक करने में मदद कर सकती हूँ। कृपया बताइए आपको किस प्रकार का परामर्श चाहिए और आप किस समय को पसंद करते हैं।'
        }
    },
    'package_selection': {
        'keywords': ['what package', 'which package', 'package', 'best package', 'suggest package'],
        'answers': {
            'en': 'Based on your concern, I recommend starting with a consultation that matches your issue. I can suggest the best package based on your needs.',
            'hi': 'आपकी समस्या के आधार पर, मैं ऐसे परामर्श की सलाह दूँगी जो आपकी चिंता से मेल खाता हो। मैं आपके needs के आधार पर सबसे सही पैकेज सुझा सकती हूँ।'
        }
    },
    'booking_flow': {
        'keywords': ['book', 'booking', 'want to book', 'schedule', 'appointment'],
        'answers': {
            'en': 'Please share your name, preferred consultation type, and a suitable time. Once confirmed, I can help you complete the booking.',
            'hi': 'कृपया अपना नाम, पसंदीदा परामर्श प्रकार और सही समय बताइए। पुष्टि होने के बाद, मैं बुकिंग पूरी करने में मदद करूँगी।'
        }
    },
    'support_question': {
        'keywords': ['how does this work', 'how it works', 'what is this', 'consultation work', 'refund', 'policy', 'support'],
        'answers': {
            'en': 'The consultation helps you speak with an astrologer about your concern. After booking, you can proceed with the selected service and follow-up support.',
            'hi': 'यह परामर्श आपको अपने मुद्दे पर ज्योतिषी से बात करने में मदद करता है। बुकिंग के बाद आप चुने गए सेवा और फॉलो-अप सहायता के साथ आगे बढ़ सकते हैं।'
        }
    },
    'support_escalation': {
        'keywords': ['need help', 'support', 'issue', 'problem', 'unresolved', 'need assistance'],
        'answers': {
            'en': 'I can help with your issue. If your concern needs specialist support, I can route you to the right next step or connect you with a support team.',
            'hi': 'मैं आपकी समस्या में मदद कर सकती हूँ। अगर आपके मामले के लिए विशेषज्ञ सहायता चाहिए, तो मैं आपको सही अगला कदम या सपोर्ट टीम से जोड़ सकती हूँ।'
        }
    }
}

# ta/te/ml are a v1 heuristic, not native-reviewed — same caveat as the
# other regional-language strings elsewhere in this file.
NO_INFO = {
    'en': "Sorry, I didn't quite get that.",
    'hi': "माफ़ करना, मैं समझी नहीं।",
    'ta': "மன்னிக்கவும், அது எனக்குப் புரியவில்லை.",
    'te': "క్షమించండి, అది నాకు అర్థం కాలేదు.",
    'ml': "ക്ഷമിക്കണം, അത് എനിക്ക് മനസ്സിലായില്ല.",
}

# Code-level gate for the ONE hard, non-negotiable rule ("never answer a
# prediction question") -- added after live testing showed the model
# doesn't reliably call trigger_recommend_astrologer for this on its own
# (verified via the raw Vertex AI response: finishReason STOP, no
# functionCall part, despite the prompt saying it's mandatory). Same
# precedent as astrohelp's own code-enforced rules: prompt-only isn't
# trustworthy enough for something this deterministic. ta/te/ml keyword
# lists are a v1 heuristic, not native-reviewed.
# Real account/booking/support terms that mean a message is about an
# actual issue, not a fortune question, even when it also contains a
# PREDICTION_KEYWORDS phrase like "will i"/"what will happen" (e.g. "will I
# get my refund" or "what will happen to my booking") — checked FIRST in
# is_prediction_intent, before any prediction-keyword match, so a real
# support question never gets deflected with the canned fortune-question
# non-answer. ta/te/ml lists are a v1 heuristic, not native-reviewed, same
# caveat as PREDICTION_KEYWORDS below.
ISSUE_OVERRIDE_KEYWORDS = {
    'en': [
        'refund', 'booking', 'payment', 'order', 'cancel', 'ticket',
        'charged', 'deducted', 'coins', 'wallet', 'money back',
        'complaint', 'support', 'account', 'session', 'recharge',
    ],
    'hi': [
        'रिफंड', 'बुकिंग', 'भुगतान', 'पैसे वापस', 'शिकायत', 'सपोर्ट',
        'अकाउंट', 'वॉलेट', 'रिचार्ज', 'टिकट',
    ],
    'ta': ['பணம் திரும்ப', 'முன்பதிவு', 'புகார்', 'கணக்கு', 'ரீசார்ஜ்'],
    'te': ['రీఫండ్', 'బుకింగ్', 'ఫిర్యాదు', 'ఖాతా', 'రీచార్జ్'],
    'ml': ['റീഫണ്ട്', 'ബുക്കിംഗ്', 'പരാതി', 'അക്കൗണ്ട്', 'റീചാർജ്'],
}

PREDICTION_KEYWORDS = {
    'en': [
        'will i', 'will my', 'when will i', 'when will my', 'what will happen',
        'what does my future hold', 'predict my', 'prediction', "today's horoscope",
        'horoscope for today', 'rashifal', 'lucky number', 'lucky colour',
        'lucky color', 'auspicious time', 'shubh muhurat', 'shubh mahurat',
        'when am i getting married', 'when will i get a job', 'my fate',
        'what does my chart say', 'what my chart',
    ],
    'hi': [
        'क्या होगा', 'भविष्य', 'भाग्य', 'कब होगी', 'कब मिलेगी', 'कब मिलेगा',
        'राशिफल', 'मुहूर्त', 'कब शादी होगी', 'भविष्यफल',
    ],
    'ta': ['எதிர்காலம்', 'ராசி பலன்', 'பலன் என்ன', 'எப்போது திருமணம்'],
    'te': ['భవిష్యత్తు', 'జాతకం', 'రాశిఫలం', 'ఎప్పుడు పెళ్ళి'],
    'ml': ['ഭാവി', 'ജാതകം', 'രാശിഫലം', 'എപ്പോൾ വിവാഹം'],
}

# Generic fallback — only used for 'general' (no specific concern bucket
# matched, see _CONCERN_BY_INTENT below), since "best people who've helped
# others with their general" isn't a real phrase.
CONNECT_MESSAGES = {
    'en': "I know a few people who can help with this. Want me to connect you?",
    'hi': "इसमें मदद कर सकने वाले कुछ लोगों को मैं जानती हूँ। जोड़ दूँ?",
    'ta': "இதற்கு உதவக்கூடிய சிலரை எனக்குத் தெரியும். இணைக்கட்டுமா?",
    'te': "దీనికి సహాయపడగల కొందరు నాకు తెలుసు. కనెక్ట్ చేయమంటారా?",
    'ml': "ഇതിന് സഹായിക്കാൻ കഴിയുന്ന ചിലരെ എനിക്കറിയാം. ബന്ധിപ്പിക്കട്ടെയോ?",
}

# Whenever a specific concern bucket IS known (career/love/finance/
# marriage), name it explicitly instead of the generic "this" above —
# "the best people who've helped others with their X" reads as a real
# recommendation, not a vague offer.
CONNECT_MESSAGE_WITH_CONCERN = {
    'en': "I know the best people who've helped others with their {concern}. Want me to connect you?",
    'hi': "{concern} में औरों की मदद कर चुके सबसे अच्छे लोगों को मैं जानती हूँ। जोड़ दूँ?",
    'ta': "{concern} விஷயத்தில் மற்றவர்களுக்கு உதவிய சிறந்தவர்களை எனக்குத் தெரியும். இணைக்கட்டுமா?",
    'te': "{concern} విషయంలో ఇతరులకు సహాయపడిన అత్యుత్తమ వ్యక్తులు నాకు తెలుసు. కనెక్ట్ చేయమంటారా?",
    'ml': "{concern} കാര്യത്തിൽ മറ്റുള്ളവരെ സഹായിച്ച മികച്ചവരെ എനിക്കറിയാം. ബന്ധിപ്പിക്കട്ടെയോ?",
}

# The concern noun itself, per language — plugged into
# CONNECT_MESSAGE_WITH_CONCERN's {concern} placeholder above.
CONCERN_NOUNS = {
    'en': {'career': 'career', 'love': 'love life', 'finance': 'finances', 'marriage': 'marriage'},
    'hi': {'career': 'करियर', 'love': 'प्यार', 'finance': 'पैसों', 'marriage': 'शादी'},
    'ta': {'career': 'தொழில்', 'love': 'காதல்', 'finance': 'பணம்', 'marriage': 'திருமணம்'},
    'te': {'career': 'కెరీర్', 'love': 'ప్రేమ', 'finance': 'డబ్బు', 'marriage': 'పెళ్లి'},
    'ml': {'career': 'കരിയർ', 'love': 'പ്രണയം', 'finance': 'പണം', 'marriage': 'വിവാഹം'},
}


def connect_message(lang: str, concern: str) -> str:
    """The code-level (non-model) connect offer text — only used by the
    two hardcoded trigger() call sites below (prediction deflect + the
    general-concern safety net), same ones that already compute `concern`
    for the card itself but never threaded it into this text until now."""
    noun = CONCERN_NOUNS.get(lang, CONCERN_NOUNS['en']).get(concern)
    if not noun:
        return CONNECT_MESSAGES.get(lang, CONNECT_MESSAGES['en'])
    template = CONNECT_MESSAGE_WITH_CONCERN.get(lang, CONNECT_MESSAGE_WITH_CONCERN['en'])
    return template.format(concern=noun)


# Shared by every keyword-list detector below — `normalized` is whatever
# the caller already normalized the text to (normalize_text() or .lower()).
def _contains_any(normalized: str, keywords) -> bool:
    return any(keyword in normalized for keyword in keywords)


# Shared {lang: [...]} lookup with an 'en' fallback.
def _lang_list(keyword_dict: dict, lang: str) -> list:
    return keyword_dict.get(lang, keyword_dict['en'])


def is_prediction_intent(question, lang):
    normalized = normalize_text(question)
    if _contains_any(normalized, _lang_list(ISSUE_OVERRIDE_KEYWORDS, lang)):
        return False
    return _contains_any(normalized, _lang_list(PREDICTION_KEYWORDS, lang))


# Code-level gate for another hard, non-negotiable rule ("fraud/cheat/
# 'waste of time'/legal-threat language always gets a real ticket") —
# added after live testing showed the model sometimes CLAIMS it raised a
# ticket for this kind of language ('I've told the team') without
# actually calling create_support_ticket. Same precedent as
# PREDICTION_KEYWORDS above: prompt-only isn't trustworthy enough for a
# must-never-fail rule. ta/te/ml lists are a v1 heuristic, not
# native-reviewed.
LEGAL_THREAT_KEYWORDS = {
    'en': ['legal', 'lawyer', 'police', 'court', 'sue', 'consumer forum', 'consumer court'],
    'hi': ['कानूनी', 'वकील', 'पुलिस', 'कोर्ट', 'अदालत'],
    'ta': ['சட்ட நடவடிக்கை', 'வழக்கறிஞர்', 'காவல்துறை', 'நீதிமன்றம்'],
    'te': ['చట్టపరమైన', 'న్యాయవాది', 'పోలీసు', 'కోర్టు'],
    'ml': ['നിയമപരമായ', 'അഭിഭാഷകൻ', 'പോലീസ്', 'കോടതി'],
}
SEVERE_COMPLAINT_KEYWORDS = {
    'en': ['fraud', 'cheat', 'scam', 'waste of time', 'waste of money'],
    'hi': ['धोखा', 'फ्रॉड', 'समय की बर्बादी', 'पैसे की बर्बादी'],
    'ta': ['மோசடி', 'ஏமாற்று', 'நேரம் வீண்'],
    'te': ['మోసం', 'స్కామ్', 'సమయం వృథా'],
    'ml': ['വഞ്ചന', 'തട്ടിപ്പ്', 'സമയം പാഴായി'],
}


def is_severe_complaint(question, lang):
    """(is_severe, is_legal_threat) — is_legal_threat decides the ticket
    category (escalation vs refund) when the safety net below fires."""
    normalized = normalize_text(question)
    is_legal = _contains_any(normalized, _lang_list(LEGAL_THREAT_KEYWORDS, lang))
    is_severe = is_legal or _contains_any(normalized, _lang_list(SEVERE_COMPLAINT_KEYWORDS, lang))
    return is_severe, is_legal


# General backstop, broader than is_severe_complaint above: rather than
# enumerate every possible complaint phrasing (refund, no-reply, billing,
# ...) that SHOULD result in a real ticket, catch the actual failure mode
# directly — live testing showed the model's own reply sometimes claims
# it escalated/flagged something ('maine team ko flag kar diya hai') while
# never actually calling create_support_ticket. A plain "astrologer never
# replied, coins deducted, want a refund" complaint hit this on 7/7 tries
# in testing despite explicit tool-schema instructions to always raise a
# ticket for exactly this case. Checked against the AGENT's reply text,
# not the visitor's question — this is about catching a broken promise,
# not classifying the complaint. ta/te/ml phrases are a v1 heuristic, not
# native-reviewed, same caveat as this file's other regional-language lists.
# Tried matching full phrases, then verb-tense root-word co-occurrence —
# both kept missing new real phrasings live ("flag kar deti hoon", "team
# ko bolkar", "let me get the team on this", ...): English/Hindi have too
# many ways to say "I'm telling the team" to ever fully enumerate. The
# robust anchor instead: agent/prompt.py's REPAIR section explicitly
# reserves "the team"/"our team" as the ONLY approved phrasing for
# anything escalation-related, and forbids every other internal-sounding
# word — so "team" appearing in a reply AT ALL is already a reliable
# signal on its own, regardless of which verb surrounds it. 'ticket'/
# 'escalat' kept as direct triggers too, for the rare case that word
# leaks despite the REPAIR rule against it (itself worth catching).
_ESCALATION_CLAIM_WORDS = ['team', 'टीम', 'ticket', 'escalat']


def answer_claims_escalation(answer: str) -> bool:
    """Checked regardless of the question's detected language — the
    model's own reply slips into Hindi/Hinglish wording even when replying
    to an English question (seen repeatedly in testing), so gating this by
    the question's language would miss an escalation claim phrased in a
    different register than the question."""
    return _contains_any((answer or '').lower(), _ESCALATION_CLAIM_WORDS)


# Same claim-mismatch pattern as _ESCALATION_CLAIM_PHRASES above, but for
# the connect card instead of a ticket — live testing (a real multi-turn
# conversation) showed the model's own reply twice said something like
# "want me to connect you?" while never actually calling
# trigger_recommend_astrologer, leaving the text promising a card that
# never showed up. Also covers the reverse case: the visitor says "yes"
# to a connect offer the BOT made last turn, and the card still has to
# show up even if this turn's reply doesn't repeat the word "connect".
_CONNECT_OFFER_PHRASES = [
    'connect you', 'connect now', 'want to connect', 'tap below',
    'connect right now', 'talking to someone', 'talk to someone',
    'connecting you', 'connect karu', 'connect kar doon', 'connect kardu',
    'connect karoon', 'jodu', 'jod doon', 'jod du', 'jod sakti',
]

_AFFIRMATIVE_REPLIES = [
    'yes', 'yeah', 'yep', 'yup', 'sure', 'please connect', 'connect me',
    'go ahead', 'haan', 'ha', 'theek hai', 'zaroor', 'bilkul', 'connect karo',
    'connect kar do', 'kar do', 'okay connect', 'ok connect',
]


def answer_offers_connect(text: str) -> bool:
    return _contains_any((text or '').lower(), _CONNECT_OFFER_PHRASES)


def is_affirmative_reply(question: str) -> bool:
    normalized = f' {normalize_text(question)} '
    return any(f' {phrase} ' in normalized for phrase in _AFFIRMATIVE_REPLIES)


def last_bot_message(history: list) -> str:
    for turn in reversed(history or []):
        if (turn.get('sender') or turn.get('role')) == 'bot':
            return turn.get('text') or ''
    return ''


# Shared by /ask's two code-level ticket backstops (severe-complaint and
# claim-mismatch) — same create_ticket call and ctx bookkeeping either way.
def _raise_safety_net_ticket(ctx, session_id, question, category, sub_category, description_prefix, trace_source):
    ticket_service.create_ticket(
        ctx.user_id, category, sub_category, f"{description_prefix}: {question}",
        session_id=session_id, language=ctx.user_language, ltv=ctx.ltv,
    )
    ctx.ticket_raised = True
    ctx.trace.append({'tool': 'create_support_ticket', 'ok': True, 'source': trace_source})


# Small inline icons (not emoji) for the opening quick-reply chips, one per
# concern — kept here rather than in the template since they travel with
# the same 3 entries the backend already curates. `| safe` in the template
# is fine: this is our own fixed markup, never user input.
_ICON_CLOCK_HEART = (
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.6V8l2.3 1.3"/></svg>'
)
_ICON_RINGS = (
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="5.6" cy="9.4" r="3"/><circle cx="10.4" cy="9.4" r="3"/>'
    '<path d="M6.4 3.2l1.6 2.6 1.6-2.6"/></svg>'
)
_ICON_INFO = (
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="8" cy="8" r="5.8"/>'
    '<path d="M8 7.2v3.6" stroke-linecap="round"/><circle cx="8" cy="5.1" r="0.15" fill="currentColor" '
    'stroke-width="0.9"/></svg>'
)

quick_replies = [
    {"id": "ex_back", "text": "When will my ex come back?", "icon": _ICON_CLOCK_HEART},
    {"id": "marriage_timing", "text": "When will I get married?", "icon": _ICON_RINGS},
    {"id": "how_to_use", "text": "How do I use the app?", "icon": _ICON_INFO},
]


def normalize_text(text: str) -> str:
    # \w doesn't match Indic combining vowel signs/virama (Unicode category
    # Mc/Mn, e.g. Tamil ி/், Devanagari ि/्) — without whitelisting each
    # script's full block explicitly, words in these scripts get shredded
    # into fragments and keyword matching silently breaks.
    value = text.lower().strip()
    value = re.sub(r'[^\w\sऀ-ॿ஀-௿ఀ-౿ഀ-ൿ]', ' ', value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def detect_language(text: str) -> str:
    """Detected per-message (not cached per session) so mid-conversation
    language switches are followed naturally, per the launch language set
    (hi/en/ta/te/ml). Anything else falls through to 'en' for rule-based
    strings, but Gemini's own prompt still replies natively in whatever
    script it actually sees.
    """
    if re.search(r'[ऀ-ॿ]', text):
        return 'hi'
    if re.search(r'[஀-௿]', text):
        return 'ta'
    if re.search(r'[ఀ-౿]', text):
        return 'te'
    if re.search(r'[ഀ-ൿ]', text):
        return 'ml'
    return 'en'


def map_intent(question: str):
    normalized = normalize_text(question)
    best_intent = None
    best_score = 0

    for intent, config in PRD_INTENTS.items():
        score = 0
        for keyword in config['keywords']:
            if keyword in normalized:
                score += 1
        if score > best_score:
            best_score = score
            best_intent = intent

    if best_score > 0:
        return best_intent
    return None


def rule_based_answer(question: str, lang: str, intent) -> str:
    if not intent:
        return NO_INFO[lang]
    return PRD_INTENTS[intent]['answers'][lang]


# Maps map_intent()'s keyword-based PRD_INTENTS keys down to the small set
# of concern buckets recommend_flow_client.trigger() knows how to word a
# card subtitle for. Only used by the two CODE-LEVEL trigger() calls below
# (prediction deflect + safety net) — the agent's own tool call gets its
# concern straight from the model instead, which has fuller context.
_CONCERN_BY_INTENT = {'career': 'career', 'love': 'love', 'finance': 'finance', 'marriage': 'marriage'}


def concern_for_intent(intent) -> str:
    return _CONCERN_BY_INTENT.get(intent, 'general')


# Intents the CTA safety net (in /ask below) must NEVER force the connect
# card for, even past warmup — a refund/billing complaint or a "need help"
# support ask has nothing to do with astrology, so the card showing up
# there reads as a non-sequitur rather than a genuine next step. Every
# other recognized intent (including the 4 concern buckets AND the more
# generic ones like general_guidance/consultation_request/package_selection/
# booking_flow) still gets the safety net as before — this is a narrow
# carve-out for the two intents that are explicitly about a problem/support
# ask, not scoping it down to only the concern buckets.
_SAFETY_NET_EXCLUDED_INTENTS = {'support_question', 'support_escalation'}


@app.route('/')
def home():
    # Two real link formats open this page, both confirmed against
    # production: the native app's own WebView uses ?user_id=...&
    # oauth_token=...&user_name=...&ltv=..., while the "Chat with us"
    # support/CRM link uses ?user_id=...&name=...&ltv=... with no
    # oauth_token at all. `name` wins when both happen to be present.
    #
    # A `token` param (integrations/link_token.py) takes over from BOTH
    # of those when present — an encrypted blob carrying user_id/name/
    # ltv instead of plaintext query params, so those values never sit
    # readable in a URL (browser history, server access logs, a
    # forwarded link). A present-but-broken token (expired/tampered/
    # wrong key) deliberately does NOT fall back to reading legacy
    # plaintext params in the same request — that fallback would let
    # `?token=garbage&user_id=...&name=...` bypass encryption entirely.
    # See agent/context.py's resolve_session for how user_id/name/ltv
    # get trusted (or not) once they reach /ask. Embedded into the page
    # below so script.js can carry them on every request without
    # re-reading location.search each time.
    token_param = request.args.get('token')
    if token_param:
        identity = link_token.decrypt_identity(token_param) or {'user_id': '', 'name': '', 'ltv': ''}
        user_id = identity['user_id']
        user_name = identity['name']
        ltv = identity['ltv']
    else:
        user_id = request.args.get('user_id', '')
        user_name = request.args.get('name') or request.args.get('user_name', '')
        ltv = request.args.get('ltv', '')

    return render_template(
        'index.html',
        quick_replies=quick_replies,
        user_id=user_id,
        oauth_token=request.args.get('oauth_token', ''),
        user_name=user_name,
        ltv=ltv,
        # The user's own declared language preference (one of
        # dashboard.db.LANGUAGE_CODES, e.g. 'hi') — a new, independent
        # query param, not part of the encrypted token: it isn't
        # sensitive, so whoever generates the link can add it whenever
        # ready without touching the encryption flow at all. Only
        # actually used for ticket round-robin routing (see
        # agent/context.py's resolve_session and tool_registry.py's
        # create_support_ticket handler) — never overrides the bot's own
        # per-message reply-language detection.
        user_language=request.args.get('language', ''),
    )


@app.route('/upload', methods=['POST'])
def upload():
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({'error': 'Unsupported file type'}), 400

    # Read once into memory rather than handing boto3 the request's own
    # stream directly — s3transfer's managed upload closes the stream it's
    # given on failure as part of its own cleanup (confirmed: a seek()
    # after a failed upload_fileobj raises "seek of closed file"), so a
    # local-disk fallback reading from that same stream would either
    # crash or write a truncated file. Bytes in memory are also what let
    # a PutObject failure at ANY point mid-stream still fall back cleanly.
    file_bytes = file.read()

    # S3 when configured — local disk doesn't survive a pod restart/
    # redeploy, and is invisible across replicas behind a load balancer,
    # which is exactly what "photo not displayed / broken image" looks
    # like in a multi-pod production deployment (see photo_storage.py's
    # own docstring).
    #
    # QA: "the uploaded image often doesn't come through" traced to a
    # real, 100%-reproducible S3 PutObject AccessDenied (an IAM policy
    # gap on this bucket/prefix — needs fixing on the AWS side, this
    # code can't grant itself permissions). Previously any S3 failure
    # hard-failed the whole upload with no fallback, so every single
    # photo share was silently broken for as long as that gap existed.
    # Falling back to local disk here means a photo still displays and
    # still reaches Gemini vision for the CURRENT turn even while S3 is
    # down/misconfigured — it just won't survive a pod restart until the
    # underlying permission is fixed, which beats not working at all.
    if photo_storage.is_configured():
        mime_type, _ = mimetypes.guess_type(file.filename)
        try:
            url = photo_storage.save(io.BytesIO(file_bytes), ext, mime_type or 'application/octet-stream')
            return jsonify({'url': url})
        except Exception:
            logger.exception('Photo upload to S3 failed — falling back to local disk')

    safe_name = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_FOLDER, secure_filename(safe_name)), 'wb') as f:
        f.write(file_bytes)
    return jsonify({'url': url_for('static', filename=f'uploads/{safe_name}')})


_ATTACHMENT_MARKER_RE = re.compile(r'\[Shared a photo: (\S+)\]')


def find_last_attachment_url(question: str, history: list):
    """Most recent `[Shared a photo: <url>]` marker — checked on the
    current question first (script.js embeds it directly there for the
    turn that just uploaded), then scanning history newest-first for a
    photo shared earlier in the conversation."""
    match = _ATTACHMENT_MARKER_RE.search(question)
    if match:
        return match.group(1)
    for turn in reversed(history or []):
        match = _ATTACHMENT_MARKER_RE.search(turn.get('text') or '')
        if match:
            return match.group(1)
    return None


def load_current_photo(question: str):
    """Only for a photo shared in THIS exact turn (marker on `question`
    itself, not history) — the model should actually look at a photo once,
    when it's shared, not re-analyze the same bytes on every later turn
    just because find_last_attachment_url() can still find the marker in
    old history for evidence_url purposes. Returns (base64_data, mime_type)
    or (None, None) — never raises; a missing/unreadable file just means
    no image reaches the model this turn, same as if none was shared."""
    match = _ATTACHMENT_MARKER_RE.search(question)
    if not match:
        return None, None
    url = match.group(1)

    static_prefix = app.static_url_path + '/'
    if url.startswith(static_prefix):
        relative_path = url[len(static_prefix):]
        file_path = os.path.join(app.static_folder, relative_path)
        # Never let a crafted marker path escape static/ (e.g. '../../etc/passwd').
        if not os.path.abspath(file_path).startswith(os.path.abspath(app.static_folder) + os.sep):
            return None, None
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type or not mime_type.startswith('image/'):
            return None, None
        try:
            with open(file_path, 'rb') as f:
                data = f.read()
        except OSError:
            return None, None
        return base64.b64encode(data).decode('ascii'), mime_type

    # photo_storage.py's S3 path (see /upload and /media/<key> below) —
    # the marker holds our own /media/<key> app path in this mode (or, for
    # history saved before the proxy existed, a real https:// presigned
    # URL — media_path_for_url normalizes either to a key). Read straight
    # from S3 with our own credentials rather than issuing an HTTP GET
    # against whatever URL shows up in the marker — `question` ultimately
    # comes from the client, so blindly fetching it would be a textbook SSRF.
    media_path = photo_storage.media_path_for_url(url)
    if media_path.startswith('/media/'):
        result = photo_storage.get_object(media_path[len('/media/'):])
        if result:
            data, mime_type = result
            if mime_type.startswith('image/'):
                return base64.b64encode(data).decode('ascii'), mime_type

    return None, None


@app.route('/media/<path:key>')
def serve_photo(key):
    """Serves an uploaded chat photo by proxying it through S3 with our
    own long-lived credentials — see photo_storage.save()'s docstring for
    why this exists instead of handing the browser a presigned URL (AWS
    hard-caps those at 7 days). The key itself (a random uuid4 hex) is the
    only access control, same trust model a presigned URL had: anyone
    with the link can view it, nobody without it can guess one."""
    result = photo_storage.get_object(key)
    if not result:
        return '', 404
    data, mime_type = result
    return Response(data, mimetype=mime_type, headers={'Cache-Control': 'private, max-age=86400'})


# At least this many user turns happen before the agent's own
# trigger_recommend_astrologer CTA shows for a general concern — see
# agent/prompt.py's warmup clause. Prediction questions and explicit
# astrologer requests bypass this inside the agent itself.
MIN_TURNS_BEFORE_CTA = 3


@app.route('/ask', methods=['POST'])
def ask():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get('question', '')).strip()
    session_id = str(payload.get('session_id') or uuid.uuid4())
    history = payload.get('history') or []

    if not question:
        return jsonify({'answer': NO_INFO['en'], 'session_id': session_id}), 400

    lang = detect_language(question)
    turn_number = 1 + sum(1 for turn in history if (turn.get('sender') or turn.get('role')) == 'user')
    past_warmup = turn_number >= MIN_TURNS_BEFORE_CTA

    ctx = agent_context.resolve_session(payload, session_id, lang, history)
    ctx.last_attachment_url = find_last_attachment_url(question, history)
    image_data, image_mime = load_current_photo(question)
    # A photo was shared this turn but its bytes never made it to the model
    # (bad path, unknown mime, unreadable file) — say so explicitly rather than
    # letting the model guess what a photo it can't see contains.
    image_unavailable = bool(_ATTACHMENT_MARKER_RE.search(question)) and not image_data
    if image_unavailable:
        app.logger.warning('Photo marker in question but image could not be loaded: %r', question[:200])
    dashboard_db.ensure_conversation(session_id, ctx.user_id, ltv=ctx.ltv)

    if is_prediction_intent(question, lang):
        # Never let a prediction/fortune question reach the model at all —
        # deflect before it runs. Added after live testing showed the
        # agent's own trigger_recommend_astrologer call for this case
        # isn't reliable enough on prompt instruction alone (see
        # PREDICTION_KEYWORDS' comment above).
        concern = concern_for_intent(map_intent(question))
        ctx.ui_action = recommend_flow_client.trigger(lang, None, concern)
        answer = connect_message(lang, concern)
        source = 'prediction_deflect'
    elif faq_client.critical_fact(question, lang):
        # Code-level bypass for a curated set of fact-sensitive questions
        # (see faq_client.critical_fact's own docstring) — live testing
        # showed the model sometimes skips calling get_app_faq entirely
        # for a question that FEELS like a simple yes/no and just
        # guesses, confirmed wrong for both a feature-existence question
        # (search bar, English) and a privacy question (Tamil). Answering
        # straight from the real fact here trades some of the model's
        # natural phrasing for a guarantee of correctness, same tradeoff
        # already accepted for the prediction-deflection above.
        answer = faq_client.critical_fact(question, lang)
        source = 'critical_faq_deflect'
    else:
        answer = agent_orchestrator.run_chat_turn(
            question, history, ctx, turn_number, past_warmup,
            image_data=image_data, image_mime=image_mime,
            image_unavailable=image_unavailable,
        )
        source = 'agent'
        intent = map_intent(question)
        if not answer:
            # Gemini unconfigured or the whole tool loop failed — everything
            # else in the app still works, same posture as astrohelp.
            answer = rule_based_answer(question, lang, intent)
            source = 'rule_based'
        elif (
            not ctx.ui_action and past_warmup and intent is not None
            and intent not in _SAFETY_NET_EXCLUDED_INTENTS
            and not _contains_any(normalize_text(question), _lang_list(ISSUE_OVERRIDE_KEYWORDS, lang))
        ):
            # Safety net, not a substitute for the agent's own tool call:
            # live testing showed the model doesn't reliably call
            # trigger_recommend_astrologer for a general recognized concern
            # even with an explicit prompt rule (unlike the prediction case
            # above, this is too varied/conversational to hard-gate on
            # content — but the core "every reply moves toward action"
            # principle still needs the button to actually exist whenever
            # the reply implies one). Only fires when the agent didn't
            # already set an action itself. Excludes support_question/
            # support_escalation (see _SAFETY_NET_EXCLUDED_INTENTS) — a
            # refund/support complaint has nothing to do with astrology,
            # so forcing the card there just contradicts whatever the
            # agent's own tool calls (e.g. create_support_ticket) already
            # decided was the right response. ALSO excludes any message
            # containing an ISSUE_OVERRIDE_KEYWORDS term (payment,
            # deducted, refund, recharge, ...) regardless of which intent
            # matched — confirmed live: "money got deducted" classifies as
            # the 'finance' concern bucket (via the generic word "money"),
            # which isn't support_question/support_escalation, so without
            # this check the card still got force-shown for a payment
            # failure even after create_support_ticket's own instructions
            # were fixed to never pivot there. A real finance CONCERN
            # ("worried about my finances") doesn't contain any of these
            # override terms, so it's unaffected.
            ctx.ui_action = recommend_flow_client.trigger(lang, None, concern_for_intent(intent))

        if not ctx.ui_action and past_warmup and (
            answer_offers_connect(answer)
            or (answer_offers_connect(last_bot_message(history)) and is_affirmative_reply(question))
        ):
            # Backstop for two confirmed-live failure modes: (1) this
            # turn's own reply says "want me to connect you?" but never
            # actually called trigger_recommend_astrologer, leaving that
            # promise empty; (2) the BOT offered to connect last turn and
            # the visitor just said "yes"/"haan" — the card must show up
            # this turn even if the new reply doesn't repeat the word
            # "connect". Gated on past_warmup so a stray pre-warmup offer
            # doesn't get compounded by also forcing the card early (see
            # the no-backstop warmup-timing note elsewhere).
            ctx.ui_action = recommend_flow_client.trigger(lang, None, concern_for_intent(intent))

    is_severe, is_legal_threat = is_severe_complaint(question, lang)
    if is_severe and not ctx.ticket_raised:
        # Code-level backstop for the ONE must-never-fail rule here: live
        # testing showed the model sometimes CLAIMS it raised a ticket for
        # fraud/cheat/"waste of time"/legal-threat language ('I've told
        # the team') without actually calling create_support_ticket. Raise
        # a real one ourselves rather than trust prompt compliance for
        # this — same reasoning as PREDICTION_KEYWORDS' own gate. Checked
        # regardless of which path produced `answer` above (prediction
        # deflect or agent), and regardless of whether the agent's own
        # tool call already set ctx.ticket_raised (if it did, this is a
        # no-op). Never overrides `answer` itself — only guarantees the
        # ticket genuinely exists so whatever the reply already said
        # ("I've flagged this") is actually true.
        _raise_safety_net_ticket(
            ctx, session_id, question,
            'escalation' if is_legal_threat else 'refund', 'auto_escalated_severe_language',
            'Auto-escalated by safety net (fraud/cheat/waste-of-time/legal-threat language detected)',
            'severe_complaint_safety_net',
        )

    if source == 'agent' and not ctx.ticket_raised and answer_claims_escalation(answer):
        # Broader backstop than the severe-complaint one above: catches
        # ANY complaint (not just fraud/cheat) where the agent's own
        # reply claims it escalated/flagged something but never actually
        # called create_support_ticket — e.g. a plain "astrologer never
        # replied, coins deducted, want a refund" complaint, which hit
        # this at 0/7 in testing despite tool-schema instructions to
        # always raise a ticket for it. Makes the already-stated claim
        # true rather than leave it false.
        _raise_safety_net_ticket(
            ctx, session_id, question, 'refund', 'auto_escalated_claim_mismatch',
            'Auto-escalated by safety net (reply claimed an escalation that never happened)',
            'claim_mismatch_safety_net',
        )

    s3_client.log_event({
        'session_id': session_id,
        'user_id': ctx.user_id,
        'question': question,
        'answer': answer,
        'language': lang,
        'source': source,
        'tool_trace': ctx.trace,
    })
    dashboard_db.record_turn(
        session_id, ctx.user_id, question, answer, lang, source, ctx.trace,
        card_shown=bool(ctx.ui_action), ltv=ctx.ltv,
    )

    return jsonify({
        'answer': answer,
        'session_id': session_id,
        'source': source,
        'language': lang,
        'action': ctx.ui_action,
        'show_feedback': ctx.show_feedback,
        # Tells the client a ticket now exists for this session — that's
        # its cue to start polling agent-messages below for a live reply
        # once a real CS agent picks it up in Zoho (see #3's webhook sync,
        # /webhooks/zoho). Client-side this only ever turns polling ON,
        # never off mid-session — see static/script.js's hasOpenTicket.
        'ticket_raised': ctx.ticket_raised,
    })


@app.route('/history/<session_id>')
def get_history(session_id):
    """#3 on the QA list — 'chat history is not getting saved' — it
    actually always was (record_turn persists every turn), it just never
    got read back: the page always rendered a blank chatBody and showed
    the welcome message fresh, even on a reload with the exact same
    session_id still in sessionStorage. Polled once by script.js on load
    (only when a session_id already exists, i.e. this isn't a brand-new
    visit) to restore the real conversation instead of starting over.
    Same visitor-facing trust model as /conversations/<id>/agent-messages
    above — whoever has this session_id can read it."""
    conv = dashboard_db.get_conversation(session_id)
    if not conv:
        return jsonify({'messages': [], 'has_ticket': False})
    return jsonify({
        'messages': [
            {'role': m['role'], 'text': m['text'], 'created_at': m['created_at']}
            for m in conv['messages']
        ],
        'has_ticket': dashboard_db.session_has_ticket(session_id),
    })


@app.route('/conversations/<session_id>/agent-messages')
def get_agent_messages(session_id):
    """Polled by the visitor's own chat widget (static/script.js) while a
    ticket is open, so a real Zoho agent's reply shows up in the SAME
    chat window instead of the visitor needing a separate channel — see
    dashboard/db.py's get_new_agent_messages docstring for the trust
    model (same as every other visitor-facing route here: whoever has
    this session_id can read it, there's no stronger per-visitor auth to
    check against)."""
    since = request.args.get('since') or None
    messages = dashboard_db.get_new_agent_messages(session_id, since)
    return jsonify({'messages': messages})


@app.route('/close', methods=['POST'])
def close_session():
    """Records that the visitor's chat session was closed, so the widget
    (and /history on a later reload) can show 'Session closed on <date,
    time>'. Same trust model as /feedback and /history: session_id only."""
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'ok': False, 'error': 'session_id is required'}), 400
    closed_at = dashboard_db.record_session_closed(session_id)
    return jsonify({'ok': closed_at is not None, 'closed_at': closed_at})


@app.route('/feedback', methods=['POST'])
def feedback():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get('session_id') or '').strip()
    rating = payload.get('rating')
    if not session_id or not isinstance(rating, int) or not (1 <= rating <= 5):
        return jsonify({'ok': False, 'error': 'session_id and an integer rating 1-5 are required'}), 400
    ok = dashboard_db.record_rating(session_id, rating)
    return jsonify({'ok': ok})


@app.route('/event', methods=['POST'])
def track_event():
    """UI interaction analytics — quick-reply taps, send/upload/close/
    connect-card taps, ratings given (script.js's trackEvent()). Not a
    security boundary like /ask (session_id/user_id here are only ever
    used as analytics labels, never to gate a privileged action), so
    unlike agent_context.resolve_session, the client-sent values are
    trusted as-is — same posture as s3_client.log_event's fire-and-forget
    logging elsewhere in this app."""
    payload = request.get_json(silent=True) or {}
    event_type = str(payload.get('event_type') or '').strip()
    if not event_type:
        return jsonify({'ok': False, 'error': 'event_type is required'}), 400
    session_id = str(payload.get('session_id') or '').strip() or None
    user_id = str(payload.get('user_id') or '').strip() or None
    event_data = payload.get('event_data')
    if not isinstance(event_data, dict):
        event_data = {}
    dashboard_db.record_event(session_id, user_id, event_type, event_data)
    return jsonify({'ok': True})


ZOHO_WEBHOOK_SECRET = os.environ.get('ZOHO_WEBHOOK_SECRET', '')


@app.route('/webhooks/zoho', methods=['POST'])
def zoho_webhook():
    """#3's inbound half — an agent's status/category/reply change in Zoho
    Desk reflects back here instead of needing this dashboard AND Zoho
    open side by side. Zoho Desk's Webhooks feature (Setup > Automation >
    Webhooks) lets YOU define the outgoing JSON body as a merge-field
    template, so this endpoint's contract is whatever you configure there
    to point at this URL — set the body to exactly this shape (adjust the
    ${...} merge fields to match your actual Zoho Desk field picker, same
    "verify against your real portal" caveat as zoho_client.py's category
    map and customFields):
        {
          "zoho_ticket_id": "${Ticket.id}",
          "status": "${Ticket.status}",          // optional
          "category": "${Ticket.category}",       // optional
          "sub_category": "${Ticket.subCategory}",// optional
          "comment": "${Comment.content}",        // optional — a new reply to sync into the visitor's chat
          "agent_name": "${Comment.commentedBy}"  // optional
        }
    Add a custom header (or query param) carrying ZOHO_WEBHOOK_SECRET's
    value and this route rejects anything that doesn't match it — Zoho's
    webhook setup lets you add custom headers to the outgoing call.
    Every field but zoho_ticket_id is optional and independently applied;
    an unknown zoho_ticket_id or a status that isn't one of
    dashboard.db.TICKET_STATUSES is just ignored rather than erroring —
    Zoho's own retry-on-non-2xx behavior isn't something a malformed
    payload should trigger repeatedly."""
    provided_secret = request.headers.get('X-Webhook-Secret') or request.args.get('secret') or ''
    if not ZOHO_WEBHOOK_SECRET or not hmac.compare_digest(provided_secret, ZOHO_WEBHOOK_SECRET):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    payload = request.get_json(silent=True) or {}
    zoho_ticket_id = str(payload.get('zoho_ticket_id') or '').strip()
    if not zoho_ticket_id:
        return jsonify({'ok': False, 'error': 'zoho_ticket_id is required'}), 400

    ticket = dashboard_db.get_ticket_by_zoho_id(zoho_ticket_id)
    if not ticket:
        # Not necessarily an error — could be a webhook firing for a ticket
        # this bot never raised (e.g. a manually-created Zoho ticket if
        # the same webhook is reused department-wide). Ack anyway so Zoho
        # doesn't retry.
        return jsonify({'ok': True, 'matched': False})

    status = str(payload.get('status') or '').strip()
    if status in dashboard_db.TICKET_STATUSES:
        dashboard_db.update_ticket_status(ticket['id'], status, note='Synced from Zoho')

    category = payload.get('category')
    sub_category = payload.get('sub_category')
    if category or sub_category:
        dashboard_db.update_ticket_fields(ticket['id'], category=category, sub_category=sub_category)

    comment = str(payload.get('comment') or '').strip()
    if comment and ticket.get('session_id'):
        dashboard_db.record_agent_message(ticket['session_id'], comment, payload.get('agent_name'))

    return jsonify({'ok': True, 'matched': True})


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
