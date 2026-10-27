"""app.py's fix_mixed_language_opener — confirmed live that, despite
agent/prompt.py's own LANGUAGE rule against it, the model still
occasionally opens an English-language reply with a Hindi apology
("Maaf karna, yeh bilkul theek nahi hai.") then continues in English, for
a visitor who wrote in plain English. Same unreliable-prompt-compliance
precedent as is_prediction_intent/answer_claims_escalation — this strips
the mixed-language opener and swaps in a plain English apology instead.
"""
import app as app_module


def test_strips_hindi_opener_from_an_english_reply():
    answer = app_module.fix_mixed_language_opener(
        "Maaf karna, yeh bilkul theek nahi hai. I've reported Samrat's "
        "behavior to the team right away so they can look into it.",
        "en",
    )
    assert "maaf" not in answer.lower()
    assert "I've reported Samrat's behavior" in answer


def test_leaves_a_plain_english_reply_untouched():
    answer = "I'm really sorry that happened. I've reported this to the team."
    assert app_module.fix_mixed_language_opener(answer, "en") == answer


def test_leaves_genuine_hindi_hinglish_reply_untouched_for_hindi_question():
    answer = "Maaf karna, yeh bilkul theek nahi hai. Main team ko bata rahi hoon."
    assert app_module.fix_mixed_language_opener(answer, "hi") == answer
