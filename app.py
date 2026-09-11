import os
import re
import uuid
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, url_for
from werkzeug.utils import secure_filename

load_dotenv()

from agent import context as agent_context
from agent import orchestrator as agent_orchestrator
from integrations import s3_client

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

quick_replies = [
    "I'm anxious about my future",
    "Marriage isn't happening",
    "Facing money problems",
    "Relationship isn't working out",
    "Career feels stuck",
    "Just want to talk to someone",
]

messages = []


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

    answer = agent_orchestrator.run_chat_turn(question, history, ctx, turn_number, past_warmup)
    source = 'agent'
    if not answer:
        # Gemini unconfigured or the whole tool loop failed — everything
        # else in the app still works, same posture as astrohelp.
        intent = map_intent(question)
        answer = rule_based_answer(question, lang, intent)
        source = 'rule_based'

    s3_client.log_event({
        'session_id': session_id,
        'user_id': ctx.user_id,
        'question': question,
        'answer': answer,
        'language': lang,
        'source': source,
        'tool_trace': ctx.trace,
    })

    return jsonify({
        'answer': answer,
        'session_id': session_id,
        'source': source,
        'language': lang,
        'action': ctx.ui_action,
        'show_feedback': ctx.show_feedback,
    })


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
