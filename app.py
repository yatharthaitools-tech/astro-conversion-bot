import base64
import hmac
import io
import logging
import mimetypes
import os
import re
import urllib.parse
import uuid
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, url_for
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

load_dotenv()

from agent import context as agent_context
from agent import orchestrator as agent_orchestrator
from integrations import link_token, photo_storage, recommend_flow_client, s3_client
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
            'hi': 'मैं ज्योतिष मार्गदर्शन में मदद कर सकता हूँ। कृपया बताइए कि आपको किस चीज़ में मदद चाहिए, और मैं सही परामर्श सुझाऊँगा।'
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
            'hi': 'मैं कैरियर से जुड़े मार्गदर्शन में मदद कर सकता हूँ। कैरियर परामर्श सबसे उपयुक्त हो सकता है। क्या आप ज्योतिषी से बात करना चाहेंगे या उपलब्ध पैकेज देखें?'
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
            'hi': 'मैं रिश्ते और प्रेम से जुड़े मार्गदर्शन में मदद कर सकता हूँ। मैं आपको रिलेशनशिप स्पेशलिस्ट से जोड़ सकता हूँ या उचित परामर्श पैकेज सुझा सकता हूँ।'
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
            'hi': 'मैं वित्त से जुडे़ मार्गदर्शन में मदद कर सकता हूँ। वित्तीय ज्योतिष परामर्श उपयुक्त हो सकता है। क्या आप बैठक बुक करना चाहेंगे?'
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
            'hi': 'मैं शादी से जुड़े मार्गदर्शन में मदद कर सकता हूँ। आपकी चिंता और पसंद के अनुसार सही परामर्श सुझा सकता हूँ।'
        }
    },
    'consultation_request': {
        'keywords': ['talk to astrologer', 'consultation', 'book consultation', 'book a consultation', 'want to talk', 'astrologer'],
        'answers': {
            'en': 'Sure. I can help you book a consultation. Please choose the type of consultation you need and your preferred time.',
            'hi': 'बिलकुल। मैं आपको परामर्श बुक करने में मदद कर सकता हूँ। कृपया बताइए आपको किस प्रकार का परामर्श चाहिए और आप किस समय को पसंद करते हैं।'
        }
    },
    'package_selection': {
        'keywords': ['what package', 'which package', 'package', 'best package', 'suggest package'],
        'answers': {
            'en': 'Based on your concern, I recommend starting with a consultation that matches your issue. I can suggest the best package based on your needs.',
            'hi': 'आपकी समस्या के आधार पर, मैं ऐसे परामर्श की सलाह दूँगा जो आपकी चिंता से मेल खाता हो। मैं आपके needs के आधार पर सबसे सही पैकेज सुझा सकता हूँ।'
        }
    },
    'booking_flow': {
        'keywords': ['book', 'booking', 'want to book', 'schedule', 'appointment'],
        'answers': {
            'en': 'Please share your name, preferred consultation type, and a suitable time. Once confirmed, I can help you complete the booking.',
            'hi': 'कृपया अपना नाम, पसंदीदा परामर्श प्रकार और सही समय बताइए। पुष्टि होने के बाद, मैं बुकिंग पूरी करने में मदद करूँगा।'
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
            'hi': 'मैं आपकी समस्या में मदद कर सकता हूँ। अगर आपके मामले के लिए विशेषज्ञ सहायता चाहिए, तो मैं आपको सही अगला कदम या सपोर्ट टीम से जोड़ सकता हूँ।'
        }
    }
}

NO_INFO = {
    'en': "I don't have information regarding that.",
    'hi': "मुझे इसके बारे में जानकारी नहीं है।",
    'ta': "எனக்கு அதைப் பற்றி தகவல் இல்லை.",
    'te': "దాని గురించి నాకు సమాచారం లేదు.",
    'ml': "അതിനെക്കുറിച്ച് എനിക്ക് വിവരമില്ല.",
}

# Code-level gate for the ONE hard, non-negotiable rule ("never answer a
# prediction question") -- added after live testing showed the model
# doesn't reliably call trigger_recommend_astrologer for this on its own
# (verified via the raw Vertex AI response: finishReason STOP, no
# functionCall part, despite the prompt saying it's mandatory). Same
# precedent as astrohelp's own code-enforced rules: prompt-only isn't
# trustworthy enough for something this deterministic. ta/te/ml keyword
# lists are a v1 heuristic, not native-reviewed.
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


def is_prediction_intent(question, lang):
    normalized = normalize_text(question)
    keywords = PREDICTION_KEYWORDS.get(lang, PREDICTION_KEYWORDS['en'])
    return any(keyword in normalized for keyword in keywords)


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


_S3_UPLOADS_HOST = photo_storage.uploads_host() if photo_storage.is_configured() else None


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

    # photo_storage.py's S3 path (see /upload) — the marker holds a real
    # https:// presigned URL in this mode, not a /static/... one. Only
    # ever fetched when its host is OUR OWN configured upload bucket —
    # `question` ultimately comes from the client, so blindly GETing
    # whatever URL shows up in the marker would be a textbook SSRF
    # (a crafted marker pointing at an internal/metadata endpoint).
    if _S3_UPLOADS_HOST and urllib.parse.urlparse(url).hostname == _S3_UPLOADS_HOST:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except requests.RequestException:
            return None, None
        mime_type = response.headers.get('Content-Type', '')
        if not mime_type.startswith('image/'):
            return None, None
        return base64.b64encode(response.content).decode('ascii'), mime_type

    return None, None


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
    else:
        answer = agent_orchestrator.run_chat_turn(
            question, history, ctx, turn_number, past_warmup,
            image_data=image_data, image_mime=image_mime,
            image_unavailable=image_unavailable,
        )
        source = 'agent'
        if not answer:
            # Gemini unconfigured or the whole tool loop failed — everything
            # else in the app still works, same posture as astrohelp.
            intent = map_intent(question)
            answer = rule_based_answer(question, lang, intent)
            source = 'rule_based'
        elif not ctx.ui_action and past_warmup and map_intent(question) is not None:
            # Safety net, not a substitute for the agent's own tool call:
            # live testing showed the model doesn't reliably call
            # trigger_recommend_astrologer for a general recognized concern
            # even with an explicit prompt rule (unlike the prediction case
            # above, this is too varied/conversational to hard-gate on
            # content — but the core "every reply moves toward action"
            # principle still needs the button to actually exist whenever
            # the reply implies one). Only fires when the agent didn't
            # already set an action itself.
            ctx.ui_action = recommend_flow_client.trigger(lang, None, concern_for_intent(map_intent(question)))

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
