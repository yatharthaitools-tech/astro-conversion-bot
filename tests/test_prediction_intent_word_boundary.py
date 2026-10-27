"""app.py's is_prediction_intent — plain substring containment wrongly
matched the PREDICTION_KEYWORDS phrase 'will i' inside "will it lag",
deflecting a real product-confidence question ("will this video call
actually work or will it lag") as a fortune question before it ever
reached the model — the exact case agent/prompt.py's ASTROLOGY section
carves out ("don't confuse a product-confidence question with a fate
question"). Confirmed live. Fixed with a word-boundary match
(_contains_any_word_boundary) for this keyword list only.
"""
import app as app_module


def test_will_it_lag_is_not_a_prediction_question():
    assert app_module.is_prediction_intent(
        "will this video call feature actually work properly or will it lag", "en"
    ) is False


def test_will_it_rain_is_not_a_prediction_question():
    assert app_module.is_prediction_intent("will it rain tomorrow", "en") is False


def test_genuine_fate_question_still_deflected():
    assert app_module.is_prediction_intent("will i get married", "en") is True
    assert app_module.is_prediction_intent("will my marriage work out", "en") is True
