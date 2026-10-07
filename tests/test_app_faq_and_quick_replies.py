"""The 'How do I use the app?' quick-reply chip (app.py's quick_replies)
must actually be grounded in integrations/faq_client.py's real answer,
not left for the model to guess at — get_app_faq's own tool description
says to always call it for general app-usage questions instead of
answering from general knowledge, so the chip's exact wording has to
match a real FAQ entry.
"""
import app as app_module
from integrations import faq_client


def test_how_to_use_quick_reply_chip_exists():
    ids = [q["id"] for q in app_module.quick_replies]
    assert "how_to_use" in ids


def test_how_to_use_chip_text_matches_a_real_faq_entry():
    chip = next(q for q in app_module.quick_replies if q["id"] == "how_to_use")
    result = faq_client.search(chip["text"])
    assert result["answer"] is not None


def test_faq_still_matches_the_original_how_does_this_work_phrasing():
    assert faq_client.search("how does this work?")["answer"] is not None
    assert faq_client.search("What is AstroLokal?")["answer"] is not None
