"""Live testing (real admin conversation) showed the model spontaneously
addressed a nameless visitor as "Guest 6676616" — inventing a placeholder
AND leaking their raw user_id directly into the reply, a backdoor around
the existing "never state user_id" rule (nothing in agent/prompt.py or
agent/context.py ever generates that text; it's the model's own
fallback). Fixed by explicitly forbidding it in the TRUST section.
"""
from agent.prompt import build_system_prompt


def test_prompt_forbids_guest_placeholder_and_id_as_name():
    prompt = build_system_prompt("en", 1, False, user_name=None)
    assert "never 'Guest'" in prompt


def test_prompt_with_no_name_does_not_invite_a_placeholder():
    prompt = build_system_prompt("en", 1, False, user_name=None)
    assert "address them by any name" in prompt
