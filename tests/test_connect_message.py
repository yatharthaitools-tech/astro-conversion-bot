"""app.py's connect_message() — the code-level (non-model) connect offer
text used by the prediction-deflection path. Previously a flat generic
line ("I know a few people who can help with this") regardless of what
the visitor was actually asking about, even though the card itself
already computed a specific concern (career/love/finance/marriage) via
the same concern_for_intent/map_intent call. Now names that concern
explicitly when known, falling back to the generic line only for
'general' (no specific bucket matched).
"""
import app as app_module


def test_known_concern_names_it_explicitly():
    msg = app_module.connect_message('en', 'career')
    assert msg == "I know the best people who've helped others with their career. Want me to connect you?"


def test_each_concern_bucket_has_english_wording():
    for concern in ('career', 'love', 'finance', 'marriage'):
        msg = app_module.connect_message('en', concern)
        assert 'best people' in msg
        assert "I know a few people who can help with this" not in msg


def test_general_concern_falls_back_to_generic_line():
    assert app_module.connect_message('en', 'general') == app_module.CONNECT_MESSAGES['en']


def test_unknown_concern_falls_back_to_generic_line():
    assert app_module.connect_message('en', None) == app_module.CONNECT_MESSAGES['en']
    assert app_module.connect_message('en', 'not_a_real_concern') == app_module.CONNECT_MESSAGES['en']


def test_non_english_language_still_templates_the_concern():
    msg = app_module.connect_message('hi', 'love')
    assert 'प्यार' in msg


def test_unknown_language_falls_back_to_english_template():
    msg = app_module.connect_message('fr', 'finance')
    assert msg == "I know the best people who've helped others with their finances. Want me to connect you?"
