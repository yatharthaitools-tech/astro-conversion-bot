"""Gemini-backed reply generation.

Gated entirely by GEMINI_API_KEY: when it's unset (or a placeholder), every
function here is a no-op and app.py falls back to the rule-based responder.
Nothing else in the app requires a real key.
"""
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-flash-latest')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_API_URL = (
    'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
)
REQUEST_TIMEOUT_SECONDS = 15


def is_configured() -> bool:
    return bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith('placeholder')


def _build_system_prompt(lang: str, packages, astrologers, quick_replies) -> str:
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
        "consultation website. Your job is to help a visitor with their concern "
        "(love, career, marriage, finance, kundali, general guidance) and guide them "
        "toward booking a paid consultation or the right package.\n\n"
        "Rules:\n"
        "- Reply in whatever language and script the visitor just typed in (Hindi in "
        "Devanagari gets Hindi in Devanagari, Hinglish gets Hinglish, English gets English) "
        "— never ask which language to use, detect it.\n"
        "- Never invent a specific astrological prediction, date, or personal detail about "
        "the visitor. You are not doing the reading yourself — a real astrologer does that "
        "in the paid consultation.\n"
        "- Only reference astrologers and packages from the data below. Never invent prices, "
        "names, or availability that isn't in it.\n"
        "- Keep replies short (2-4 sentences), warm, and end with a clear next step "
        "(book a consultation, pick a package, or ask a clarifying question).\n"
        "- If the visitor's message is unrelated to astrology/consultations/booking, say "
        "you don't have information on that rather than guessing.\n\n"
        f"Known packages and astrologers (JSON, use only this data):\n{json.dumps(grounding, ensure_ascii=False)}"
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


def generate_reply(question: str, history, lang: str, packages, astrologers, quick_replies):
    """Returns a reply string, or None if Gemini is unconfigured/unavailable.

    Never raises — callers fall back to the rule-based responder on None.
    """
    if not is_configured():
        return None

    contents = _history_to_contents(history)
    contents.append({'role': 'user', 'parts': [{'text': question}]})

    payload = {
        'system_instruction': {
            'parts': [{'text': _build_system_prompt(lang, packages, astrologers, quick_replies)}]
        },
        'contents': contents,
        'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 300},
    }

    url = GEMINI_API_URL.format(model=GEMINI_MODEL)
    try:
        response = requests.post(
            url,
            params={'key': GEMINI_API_KEY},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        parts = data['candidates'][0]['content']['parts']
        text = ''.join(part.get('text', '') for part in parts).strip()
        return text or None
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        logger.warning('Gemini reply generation failed, falling back to rule-based: %s', exc)
        return None
