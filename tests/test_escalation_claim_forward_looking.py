"""app.py's answer_claims_escalation — a bare complaint (e.g. "I want to
report an astrologer") correctly makes the model ask for a description
before raising a ticket (see agent/tool_schemas.py's CREATE_SUPPORT_TICKET),
but the model's own "so I can flag it for the team" phrasing while asking
that question was wrongly read as a completed-action claim, force-raising a
ticket via the claim_mismatch safety net before any real description was
ever given. Confirmed live. Fixed by excluding forward-looking purpose
clauses ("so I can ...") from the per-sentence check, while still catching
a genuine completed-action claim ("maine team ko flag kar diya hai") that
never actually called create_support_ticket.
"""
import app as app_module


def test_asking_for_description_is_not_an_escalation_claim():
    assert app_module.answer_claims_escalation(
        "Ah, I'm really sorry to hear that. What happened? Tell me a bit "
        "about what went wrong so I can flag it for the team right away."
    ) is False


def test_genuine_completed_action_claim_still_detected():
    assert app_module.answer_claims_escalation(
        "Amount deduct ho gaya aur coins nahi aaye? Maine team ko flag kar diya hai."
    ) is True
    assert app_module.answer_claims_escalation(
        "I've already raised a ticket with our team for this."
    ) is True


def test_unrelated_so_i_can_elsewhere_does_not_hide_a_real_claim():
    assert app_module.answer_claims_escalation(
        "I've flagged this with the team. Let me know if you need anything "
        "else so I can help further."
    ) is True
