import os
import re
import uuid
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, url_for
from werkzeug.utils import secure_filename

load_dotenv()

from integrations import gemini_client, s3_client

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB cap on uploaded photos

UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads')
ALLOWED_UPLOAD_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
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
        'keywords': ['career', 'job', 'business', 'work', 'professional', 'career concern', 'job stability'],
        'answers': {
            'en': 'I can help with career-related guidance. A career consultation may be the best fit. Would you like to speak with an astrologer or explore available packages?',
            'hi': 'मैं कैरियर से जुड़े मार्गदर्शन में मदद कर सकता हूँ। कैरियर परामर्श सबसे उपयुक्त हो सकता है। क्या आप ज्योतिषी से बात करना चाहेंगे या उपलब्ध पैकेज देखें?'
        }
    },
    'love': {
        'keywords': ['love', 'relationship', 'partner', 'dating', 'romantic', 'love life', 'relationship problem'],
        'answers': {
            'en': 'I can help with relationship guidance. I can connect you with a relationship specialist or suggest a suitable consultation package.',
            'hi': 'मैं रिश्ते और प्रेम से जुड़े मार्गदर्शन में मदद कर सकता हूँ। मैं आपको रिलेशनशिप स्पेशलिस्ट से जोड़ सकता हूँ या उचित परामर्श पैकेज सुझा सकता हूँ।'
        }
    },
    'finance': {
        'keywords': ['finance', 'money', 'financial', 'wealth', 'income', 'business growth', 'financial future'],
        'answers': {
            'en': 'I can help with finance-related guidance. A financial astrology consultation may be suitable. Would you like to book a session?',
            'hi': 'मैं वित्त से जुडे़ मार्गदर्शन में मदद कर सकता हूँ। वित्तीय ज्योतिष परामर्श उपयुक्त हो सकता है। क्या आप बैठक बुक करना चाहेंगे?'
        }
    },
    'marriage': {
        'keywords': ['marriage', 'wedding', 'husband', 'wife', 'marriage prospects', 'shaadi'],
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

# Prediction/fortune questions are never answered directly — always deflected
# into a connect-with-an-astrologer pitch (see is_prediction_intent() and the
# /ask route). ta/te/ml keyword lists and messages below are a v1 heuristic
# baseline, not reviewed by a native speaker — flag for review before launch.
PREDICTION_KEYWORDS = {
    'en': [
        'will i', 'will my', 'when will i', 'when will my', 'what will happen',
        'my future', 'prediction', "today's horoscope", 'horoscope for today',
        'rashifal', 'lucky number', 'lucky colour', 'lucky color',
        'auspicious time', 'shubh muhurat', 'shubh mahurat', 'fortune',
        'when am i getting married', 'when will i get a job', 'my fate',
    ],
    'hi': [
        'क्या होगा', 'भविष्य', 'भाग्य', 'कब होगी', 'कब मिलेगी', 'कब मिलेगा',
        'राशिफल', 'मुहूर्त', 'कब शादी होगी', 'भविष्यफल',
    ],
    'ta': [
        'எதிர்காலம்', 'ராசி பலன்', 'பலன் என்ன', 'எப்போது திருமணம்',
    ],
    'te': [
        'భవిష్యత్తు', 'జాతకం', 'రాశిఫలం', 'ఎప్పుడు పెళ్ళి',
    ],
    'ml': [
        'ഭാവി', 'ജാതകം', 'രാശിഫലം', 'എപ്പോൾ വിവാഹം',
    ],
}

CONNECT_MESSAGES = {
    'en': "I know just the right astrologer for this — want me to connect you?",
    'hi': "मैं जानता हूँ इसके लिए सही ज्योतिषी कौन है — क्या आपको जोड़ूँ?",
    'ta': "இதற்கு சரியான ஜோதிடரை எனக்குத் தெரியும் — உங்களை இணைக்கவா?",
    'te': "దీనికి సరైన జ్యోతిష్కుడు నాకు తెలుసు — మిమ్మల్ని కనెక్ట్ చేయనా?",
    'ml': "ഇതിന് ശരിയായ ജ്യോതിഷിയെ എനിക്കറിയാം — നിങ്ങളെ ബന്ധിപ്പിക്കട്ടെ?",
}

CONNECT_LABELS = {
    'en': "Connect now",
    'hi': "अभी जोड़ें",
    'ta': "இப்போது இணைக்க",
    'te': "ఇప్పుడు కనెక్ట్ చేయండి",
    'ml': "ഇപ്പോൾ ബന്ധിപ്പിക്കുക",
}

# The default connect card never names a specific astrologer — matching is
# owned by the real app's own matching system, not this bot (see
# build_connect_action()). Only revealed when the visitor names someone
# themselves. ta/te/ml text is a v1 heuristic, not native-reviewed.
GENERIC_CARD_TEXT = {
    'en': {
        'title': "Our Top-Rated Astrologer",
        'subtitle': "Hand-picked for you • Trusted by thousands • Years of real experience",
    },
    'hi': {
        'title': "हमारे सर्वश्रेष्ठ ज्योतिषी",
        'subtitle': "आपके लिए चुने गए • हज़ारों का भरोसा • वर्षों का अनुभव",
    },
    'ta': {
        'title': "எங்கள் சிறந்த ஜோதிடர்",
        'subtitle': "உங்களுக்காக தேர்ந்தெடுக்கப்பட்டவர் • ஆயிரக்கணக்கானோர் நம்பிக்கை",
    },
    'te': {
        'title': "మా అత్యుత్తమ జ్యోతిష్కుడు",
        'subtitle': "మీ కోసం ఎంపిక చేయబడ్డారు • వేలమంది నమ్మకం",
    },
    'ml': {
        'title': "ഞങ്ങളുടെ മികച്ച ജ്യോതിഷി",
        'subtitle': "നിങ്ങൾക്കായി തിരഞ്ഞെടുത്തത് • ആയിരങ്ങളുടെ വിശ്വാസം",
    },
}


quick_replies = [
    "I'm anxious about my future",
    "Marriage isn't happening",
    "Facing money problems",
    "Relationship isn't working out",
    "Career feels stuck",
    "Just want to talk to someone",
]

messages = []

# Matches the real AstroLokal app's astrologer roster shape (name, specialty
# tags, languages, per-minute coin pricing, live availability) rather than
# the earlier made-up per-session-₹ demo data.
astrologers = [
    {
        'id': 'mahalakshmi',
        'name': 'Mahalakshmi',
        'specialty': 'Face reading, Palm reading, Numerology',
        'languages': 'Hindi, English, Telugu',
        'experience': '10 years',
        'rating': 4.4,
        'price': '10/min',
        'price_original': '56/min',
        'availability': 'Available now',
        'image': 'https://ui-avatars.com/api/?name=Mahalakshmi&background=ff8a5c&color=fff&size=128',
    },
    {
        'id': 'samrat',
        'name': 'Samrat',
        'specialty': 'Face reading, Tarot, Vedic',
        'languages': 'Hindi, English, Telugu, Marathi',
        'experience': '7 years',
        'rating': 4.6,
        'price': '12/min',
        'price_original': None,
        'availability': 'Busy, wait ~15 min',
        'image': 'https://ui-avatars.com/api/?name=Samrat&background=e8623d&color=fff&size=128',
    },
    {
        'id': 'nidhi',
        'name': 'Nidhi',
        'specialty': 'Face reading, Palm reading, Numerology',
        'languages': 'Hindi, English, Telugu',
        'experience': '3 years',
        'rating': 4.4,
        'price': '15/min',
        'price_original': '25/min',
        'availability': 'Available now',
        'image': 'https://ui-avatars.com/api/?name=Nidhi&background=ff6f47&color=fff&size=128',
    },
]

packages = [
    {
        'name': 'Starter Guidance',
        'price': '₹499',
        'description': 'Quick insights for a single life concern.'
    },
    {
        'name': 'Priority Consultation',
        'price': '₹1499',
        'description': 'In-depth guidance with follow-up questions.'
    },
    {
        'name': 'Premium Relationship Report',
        'price': '₹2999',
        'description': 'Detailed compatibility and relationship analysis.'
    },
]


def normalize_text(text: str) -> str:
    # \w doesn't match Indic combining vowel signs/virama (Unicode category
    # Mc/Mn, e.g. Tamil \u0bbf/\u0bcd, Devanagari \u093f/\u094d) \u2014 without whitelisting each
    # script's full block explicitly, words in these scripts get shredded
    # into fragments and keyword matching silently breaks.
    value = text.lower().strip()
    value = re.sub(r'[^\w\s\u0900-\u097f\u0b80-\u0bff\u0c00-\u0c7f\u0d00-\u0d7f]', ' ', value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def detect_language(text: str) -> str:
    """Detected per-message (not cached per session) so mid-conversation
    language switches are followed naturally, per the launch language set
    (hi/en/ta/te/ml). Anything else falls through to 'en' for rule-based
    strings, but Gemini's own prompt still replies natively in whatever
    script it actually sees.
    """
    if re.search(r'[\u0900-\u097F]', text):
        return 'hi'
    if re.search(r'[\u0B80-\u0BFF]', text):
        return 'ta'
    if re.search(r'[\u0C00-\u0C7F]', text):
        return 'te'
    if re.search(r'[\u0D00-\u0D7F]', text):
        return 'ml'
    return 'en'


def is_prediction_intent(question: str, lang: str) -> bool:
    normalized = normalize_text(question)
    keywords = PREDICTION_KEYWORDS.get(lang, PREDICTION_KEYWORDS['en'])
    return any(keyword in normalized for keyword in keywords)


def pick_available_astrologer():
    for astrologer in astrologers:
        if not astrologer['availability'].lower().startswith('busy'):
            return astrologer
    return astrologers[0]


def find_named_astrologer(text: str):
    """Looks for one of our real astrologers' names in the given text.

    Only meaningful on the user's OWN message — that's an explicit request
    for a specific person, the one case where we reveal a name at all.
    """
    normalized = normalize_text(text)
    for astrologer in astrologers:
        if astrologer['name'].lower() in normalized:
            return astrologer
    return None


def build_connect_action(lang: str, named_astrologer) -> dict:
    """Astrologer matching isn't this bot's job — it's the real app's
    existing matching system. So by default the card stays generic (no
    name/photo), and only reveals a specific astrologer when the visitor
    asked for one by name themselves.
    """
    if named_astrologer:
        return {
            'type': 'connect_popup',
            'display_mode': 'specific',
            'label': CONNECT_LABELS.get(lang, CONNECT_LABELS['en']),
            'astrologer': named_astrologer,
        }
    return {
        'type': 'connect_popup',
        'display_mode': 'general',
        'label': CONNECT_LABELS.get(lang, CONNECT_LABELS['en']),
        'astrologer': pick_available_astrologer(),
        'generic': GENERIC_CARD_TEXT.get(lang, GENERIC_CARD_TEXT['en']),
    }


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


LEAD_INTENTS = {'consultation_request', 'package_selection', 'booking_flow'}


def rule_based_answer(question: str, lang: str, intent) -> str:
    if not intent:
        return NO_INFO[lang]
    return PRD_INTENTS[intent]['answers'][lang]


def generate_answer(question: str) -> str:
    lang = detect_language(question)
    intent = map_intent(question)
    return rule_based_answer(question, lang, intent)


@app.route('/')
def home():
    return render_template(
        'index.html',
        quick_replies=quick_replies,
        messages=messages,
    )


@app.route('/upload', methods=['POST'])
def upload():
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({'error': 'Unsupported file type'}), 400

    safe_name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, secure_filename(safe_name)))

    return jsonify({'url': url_for('static', filename=f'uploads/{safe_name}')})


@app.route('/ask', methods=['POST'])
def ask():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get('question', '')).strip()
    session_id = str(payload.get('session_id') or uuid.uuid4())
    history = payload.get('history') or []

    if not question:
        return jsonify({'answer': NO_INFO['en'], 'session_id': session_id}), 400

    lang = detect_language(question)
    intent = map_intent(question)
    action = None
    named_astrologer = find_named_astrologer(question)

    if is_prediction_intent(question, lang):
        # Never let a prediction/fortune question reach Gemini or the
        # rule-based answers at all — deflect before either runs, same
        # code-level gate pattern astrohelp uses for its hard rules.
        answer = CONNECT_MESSAGES.get(lang, CONNECT_MESSAGES['en'])
        source = 'prediction_deflect'
        action = build_connect_action(lang, named_astrologer)
    else:
        answer = gemini_client.generate_reply(
            question, history, lang, packages, astrologers, quick_replies
        )
        source = 'gemini'
        if not answer:
            answer = rule_based_answer(question, lang, intent)
            source = 'rule_based'

        # Conversion-first: the goal is to get the visitor connected, not to
        # keep chatting. Attach a connect CTA to almost every substantive
        # reply about a real concern — but matching is the real app's job,
        # not this bot's, so the card stays generic (no name/photo) unless
        # the visitor asked for someone specific themselves. A genuinely
        # off-topic NO_INFO reply (no recognized intent) skips the CTA
        # entirely — there's nothing to convert on there.
        if named_astrologer or intent is not None:
            action = build_connect_action(lang, named_astrologer)

    s3_client.log_event({
        'session_id': session_id,
        'question': question,
        'answer': answer,
        'language': lang,
        'intent': intent,
        'source': source,
        'lead': intent in LEAD_INTENTS,
        'prediction_deflected': source == 'prediction_deflect',
    })

    return jsonify({
        'answer': answer,
        'session_id': session_id,
        'source': source,
        'language': lang,
        'action': action,
    })


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
