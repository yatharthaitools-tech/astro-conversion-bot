"""Gemini-backed reply generation, via Vertex AI (service-account auth).

Matches astrohelp's approach: a GCP service-account JSON, not a plain API
key. Gated entirely by GEMINI_VERTEX_CREDENTIALS_JSON: when it's unset or
fails to parse, every function here is a no-op and app.py falls back to the
rule-based responder. Nothing else in the app requires real credentials.

The credentials JSON itself is read only from the environment — never
hardcode it here, and never commit a real value into GEMINI_VERTEX_CREDENTIALS_JSON
in any tracked file (this repo is public).
"""
import json
import logging
import os

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash-001')
GOOGLE_CLOUD_LOCATION = os.environ.get('GOOGLE_CLOUD_LOCATION', 'global')
GEMINI_BILLING_FEATURE = os.environ.get('GEMINI_BILLING_FEATURE', 'astro_conversion_bot')
REQUEST_TIMEOUT_SECONDS = 15

_SCOPES = ['https://www.googleapis.com/auth/cloud-platform']

LANGUAGE_NAMES = {
    'en': 'English',
    'hi': 'Hindi, in Devanagari script',
    'ta': 'Tamil',
    'te': 'Telugu',
    'ml': 'Malayalam',
}

_credentials = None
_project_id = None
_credentials_load_failed = False


def _load_credentials():
    """Parses GEMINI_VERTEX_CREDENTIALS_JSON and builds credentials once.

    Caches failure too, so a bad/missing value doesn't retry JSON parsing
    on every request.
    """
    global _credentials, _project_id, _credentials_load_failed

    if _credentials is not None or _credentials_load_failed:
        return

    raw = os.environ.get('GEMINI_VERTEX_CREDENTIALS_JSON', '')
    if not raw:
        _credentials_load_failed = True
        return

    try:
        info = json.loads(raw)
        _credentials = service_account.Credentials.from_service_account_info(
            info, scopes=_SCOPES
        )
        _project_id = info['project_id']
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning('Failed to parse GEMINI_VERTEX_CREDENTIALS_JSON: %s', exc)
        _credentials_load_failed = True


def is_configured() -> bool:
    _load_credentials()
    return _credentials is not None


def _get_access_token() -> str:
    if not _credentials.valid:
        _credentials.refresh(GoogleAuthRequest())
    return _credentials.token


def _endpoint_url() -> str:
    if GOOGLE_CLOUD_LOCATION == 'global':
        host = 'aiplatform.googleapis.com'
    else:
        host = f'{GOOGLE_CLOUD_LOCATION}-aiplatform.googleapis.com'
    return (
        f'https://{host}/v1/projects/{_project_id}/locations/{GOOGLE_CLOUD_LOCATION}'
        f'/publishers/google/models/{GEMINI_MODEL}:generateContent'
    )


def _build_system_prompt(packages, astrologers, quick_replies, lang: str, turn_number: int, past_warmup: bool) -> str:
    grounding = {
        'packages': packages,
        'astrologers': [
            {
                'name': a['name'],
                'specialty': a['specialty'],
                'price': a['price'],
                'availability': a['availability'],
            }
            for a in astrologers
        ],
        'quick_replies': quick_replies,
    }
    return (
        "You are the AstroHelp conversion assistant, a chat widget on an astrology "
        "consultation website. Your goal is CONVERSION, not conversation — get the "
        "visitor connected to a real astrologer or booked on a package as fast as "
        "possible. You are not here to chat, explore their feelings at length, or be a "
        "free substitute for the paid consultation.\n\n"
        "Rules:\n"
        "- Reply in whatever language and script the visitor just typed in (Hindi in "
        "Devanagari gets Hindi in Devanagari, Hinglish gets Hinglish, English gets English) "
        "— never ask which language to use, detect it.\n"
        "- Never invent a specific astrological prediction, date, or personal detail about "
        "the visitor. You are not doing the reading yourself — a real astrologer does that "
        "in the paid consultation. If the visitor asks a specific fortune/prediction "
        "question (e.g. 'will I get married this year', 'what does my future hold'), don't "
        "answer it at all — just say you'd like to connect them with the right astrologer for "
        "that and ask if they'd like to proceed.\n"
        "- Only reference astrologers and packages from the data below. Never invent prices, "
        "names, or availability that isn't in it.\n"
        "- Use short, plain sentences — no paragraphs. One idea per message, ending with "
        "exactly one clear next step (not a list of options).\n"
        "- Ask AT MOST one clarifying question in the entire conversation before naming a "
        "specific astrologer and inviting them to connect now. Do not keep asking follow-up "
        "questions to understand their situation better — a real astrologer does that during "
        "the paid session, not you for free beforehand.\n"
        "- Matching the visitor to a specific astrologer is NOT your job — a separate system "
        "handles that. So do NOT name a specific astrologer from the data below UNLESS the "
        "visitor explicitly asked for that person by name themselves. Otherwise refer to "
        "'our top-rated astrologer' or 'a trusted, highly experienced specialist' "
        "generically — never invent specific stats (years, ratings) that aren't in the data "
        "below.\n"
        + (
            f"- This is turn {turn_number} of the conversation. Turns 1-2 are a warmup "
            f"period (connecting only starts from turn 3 onward), so turn {turn_number} is "
            "STILL WARMUP: this reply must NOT contain the words 'connect', 'astrologer', "
            "'book', or any invitation at all — no exceptions, even if the conversation "
            "already feels far along. Just respond like a warm, curious friend: acknowledge "
            "what they said, show empathy, optionally ask one short question. Nothing about "
            "next steps belongs in a warmup-turn reply.\n"
            if not past_warmup else
            "- Enough back-and-forth has happened — now end by inviting them to connect with "
            "our top-rated astrologer, don't just explain. Make it sound exclusive and "
            "premium, like a hand-picked match reserved just for them, not a generic queue.\n"
        )
        + "- If the visitor's message is unrelated to astrology/consultations/booking, say "
        "you don't have information on that rather than guessing.\n\n"
        f"Known packages and astrologers (JSON, use only this data):\n{json.dumps(grounding, ensure_ascii=False)}\n\n"
        f"IMPORTANT: the message you are replying to RIGHT NOW is written in "
        f"{LANGUAGE_NAMES.get(lang, 'English')}. Reply in that exact language/script — "
        "match the CURRENT message, not whatever language earlier turns in this "
        "conversation happened to use. A user switching languages mid-conversation is "
        "normal; always follow their latest message, never the conversation's earlier tone."
    )


def _history_to_contents(history):
    contents = []
    for turn in history or []:
        sender = turn.get('sender') or turn.get('role')
        text = (turn.get('text') or '').strip()
        if not text:
            continue
        role = 'model' if sender == 'bot' else 'user'
        contents.append({'role': role, 'parts': [{'text': text}]})
    return contents


def generate_reply(question: str, history, lang: str, packages, astrologers, quick_replies, turn_number: int = 99, past_warmup: bool = True):
    """Returns a reply string, or None if Vertex AI is unconfigured/unavailable.

    Never raises — callers fall back to the rule-based responder on None.
    """
    if not is_configured():
        return None

    contents = _history_to_contents(history)
    contents.append({'role': 'user', 'parts': [{'text': question}]})

    payload = {
        'systemInstruction': {
            'parts': [{'text': _build_system_prompt(packages, astrologers, quick_replies, lang, turn_number, past_warmup)}]
        },
        'contents': contents,
        'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 150},
        'labels': {'feature': GEMINI_BILLING_FEATURE},
    }

    try:
        token = _get_access_token()
        response = requests.post(
            _endpoint_url(),
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        parts = data['candidates'][0]['content']['parts']
        text = ''.join(part.get('text', '') for part in parts).strip()
        return text or None
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        logger.warning('Vertex AI reply generation failed, falling back to rule-based: %s', exc)
        return None
