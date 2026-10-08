"""Pure-data tool definitions — {name, description, input_schema} dicts.

Provider-neutral by design: orchestrator.py is the ONLY place these get
translated into Gemini's FunctionDeclaration shape. A future model swap (or
a second provider) uses this same seam, same as AstroHelp's tool_schemas.py.

This file must never import anything from integrations/ or services/ — the
orchestrator only ever sees this pure data; executor.py is the one place
that resolves a tool name to a real handler (see executor.py's docstring).

Security note baked into the schemas themselves, not just executor.py:
credit_coins has NO `amount` field — the credit amount is always computed
server-side (services/ltv_service.py) from the visitor's real LTV tier and
the booking's real deduction, never supplied by the model. There's no slot
for the model to fill in even if it wanted to, same reasoning as AstroHelp's
tool schemas never carrying an astrologer_id field.
"""

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
GET_PAYMENT_STATUS = {
    "name": "get_payment_status",
    "description": (
        "PAUSED — see create_support_ticket's note on why this and the "
        "other visibility-into-a-specific-payment/booking/wallet tools "
        "are disabled right now."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
GET_BOOKING_DETAILS = {
    "name": "get_booking_details",
    "description": (
        "PAUSED — see create_support_ticket's note on why this and the "
        "other visibility-into-a-specific-payment/booking/wallet tools "
        "are disabled right now."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "booking_id": {
                "type": "string",
                "description": "Specific booking to check. Omit for the most recent one.",
            }
        },
    },
}

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
CHECK_REFUND_ELIGIBILITY = {
    "name": "check_refund_eligibility",
    "description": (
        "Deterministic check for the single dominant refund pattern: "
        "session ended in seconds with the astrologer never actually "
        "responding, coins still deducted. Returns whether this booking "
        "auto-qualifies (booking status + talktime + astrologer message "
        "count — factual, not a judgment call) and, if so, the exact "
        "amount already refunded automatically. This is NOT the same as "
        "credit_coins — a qualifying booking is refunded automatically by "
        "this check itself, no separate credit_coins call needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "booking_id": {"type": "string", "description": "The disputed booking."}
        },
        "required": ["booking_id"],
    },
}

GET_LTV_TIER = {
    "name": "get_ltv_tier",
    "description": (
        "Returns the visitor's real lifetime-spend tier (New/Unpaid, "
        "New-Low, Mid, High). Informational — mainly useful to explain why "
        "a coin credit is or isn't being offered. credit_coins already "
        "checks this itself; you don't need to call this first just to "
        "decide whether to call credit_coins. For your own reasoning "
        "only — never state the tier, a figure, or that you looked this "
        "up to the visitor; it's backend context, not something to "
        "surface in the conversation."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
CREDIT_COINS = {
    "name": "credit_coins",
    "description": (
        "Requests a coin credit for a specific booking the visitor is "
        "unhappy about (v1: their claim is trusted at face value — no "
        "booking facts are checked). You never set or influence the "
        "amount, there is no amount field — it's a flat amount sized by "
        "the visitor's LTV tier, capped at a small number of refunds per "
        "day for that tier. If they've hit today's cap, this tells you "
        "so (say so plainly, don't imply anything is wrong with their "
        "account) — don't retry the same complaint hoping for a "
        "different result. For a visitor saying the app/service is too "
        "expensive and considering leaving with NO specific booking in "
        "dispute, omit booking_id entirely — that's a separate small "
        "retention gesture, unrelated to a refund."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "booking_id": {
                "type": "string",
                "description": "The disputed booking. Omit ONLY for a no-booking-in-dispute retention gesture (e.g. 'this app is too expensive').",
            },
            "reason": {
                "type": "string",
                "description": "One-line factual summary of what the visitor said happened, for the audit trail.",
            },
        },
        "required": ["reason"],
    },
}

# Real names are deliberately NOT spelled out anywhere in these
# descriptions (only in search_astrologers' own results) — otherwise the
# model treats them as a menu it can rattle off on its own ("Do you prefer
# Samrat, Nidhi, or Mahalakshmi?"), which defeats the whole point of the
# recommend flow staying anonymous. The enum values below are just ids for
# schema validation; the model only ever learns a real name if the
# visitor said it first and search_astrologers echoed it back.
SEARCH_ASTROLOGERS = {
    "name": "search_astrologers",
    "description": (
        "Looks up the real roster by name — handles a bare first name, a "
        "nickname, or an 'Astro <name>'-style title. ALWAYS call this the "
        "moment the visitor names someone casually or partially, BEFORE "
        "calling trigger_recommend_astrologer with an id — never guess who "
        "they mean from memory. Returns a list of {id, name}: empty means "
        "nobody by that name — never say you don't see them / they're not "
        "on the roster; frame it lightly as them likely being busy right "
        "now (never a specific fake ETA), then pivot straight to "
        "connecting with someone similar via trigger_recommend_astrologer "
        "(omit astrologer_id) — 'similar' just means warm and natural, "
        "never a generic bolted-on offer. One match means unambiguous "
        "(confirm briefly, then proceed), two or more means genuinely "
        "ambiguous (ask a one-line question naming the options before "
        "proceeding — never pick one silently). Reply in the same "
        "language/script the visitor just used for this message (Hinglish "
        "in, Hinglish out — see the LANGUAGE rules)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The name or partial name the visitor used, as they said it."}
        },
        "required": ["query"],
    },
}

TRIGGER_RECOMMEND_ASTROLOGER = {
    "name": "trigger_recommend_astrologer",
    "description": (
        "CRITICAL: whenever your reply text says anything like 'connect "
        "you', 'tap below', 'here's someone available', or otherwise "
        "references this card or offer, you MUST call this tool in that "
        "exact same turn — never write that language without the matching "
        "call. The visitor must never see text describing a connect card "
        "that wasn't actually triggered.\n\n"
        "ALSO CRITICAL: the moment you'd otherwise lay out multiple "
        "options/paths in text — 'you can choose from here', 'a few "
        "options:', 'you could either X or Y', 'pick one of these' — call "
        "this tool INSTEAD of writing that menu out. The card itself is "
        "the choice you're giving them (chat vs call), so a text list of "
        "options is almost always this tool in disguise. Say the one line "
        "that frames it, then call this tool rather than enumerating "
        "choices yourself.\n\n"
        "Surfaces the Chat/Call connect entry point in the chat UI — always "
        "the same anonymous 'connect with a top astrologer' card, never a "
        "name or photo, regardless of astrologer_id. Astrologer "
        "matching/ranking is NOT your job — a separate system owns that. "
        "Omit astrologer_id to let that system pick the best match (the "
        "normal case, and the ONLY case unless the visitor named someone "
        "first). Only pass astrologer_id once you've resolved exactly who "
        "the visitor means via search_astrologers — never guess, and never "
        "state or list a name yourself unprompted. This is also the "
        "required response to ANY prediction/fortune question — never "
        "answer the prediction itself, always call this instead. Never "
        "share an astrologer's phone number or any private contact detail "
        "under any circumstances — this tool (or notify_me_subscribe, if "
        "they're offline) is always the substitute for direct contact. "
        "If you passed astrologer_id and that person isn't actually "
        "available, the card ALREADY falls back to showing someone who "
        "is (never routes Connect to someone offline) — you don't need "
        "to call this again to get that; the result tells you this "
        "happened via requested_astrologer_name/availability/"
        "next_available_at (present only in this case). State their "
        "status plainly, then that you're connecting with someone "
        "available now instead — never claim the shown card will reach "
        "the person you named if it won't. next_available_at is a real "
        "ETA when known ('Nidhi's offline, back in about 40 minutes') "
        "but is usually null for now — there's no real schedule data "
        "behind this yet, so when it's null just state their status "
        "('Nidhi's offline right now') without inventing a return time. "
        "You can still "
        "offer notify_me_subscribe on top of that for the named person. "
        "ALSO the required response to any offers/discount/promo-code "
        "question ('any offers right now?', 'any discount?') — that data "
        "isn't available right now, so never state a specific offer, "
        "percentage or code. Instead pivot to connecting them with a "
        "quality astrologer at a discounted price, framed as exclusive to "
        "them (e.g. 'a discount just for you'), and call this tool.\n\n"
        "ALSO the pivot for a payment/wallet/coins/queue question you "
        "can't actually check (see create_support_ticket's note — those "
        "tools are paused), or a question asking for a specific "
        "astrologer's own stats/rating/experience/availability detail you "
        "don't have real data for. Don't dwell on what you can't confirm "
        "or answer around it — acknowledge briefly and naturally, then "
        "move the conversation toward connecting them with someone great "
        "right now. Keep it warm and conversational, not a reflexive "
        "'want me to connect you?' bolted onto the end — e.g. 'Can't "
        "pull that up on my side, but let's get you talking to someone "
        "who can actually help' rather than just offering a card after "
        "stating you don't know."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "astrologer_id": {
                "type": "string",
                "enum": ["mahalakshmi", "samrat", "nidhi"],
                "description": "Only set if the visitor explicitly asked for this specific person by name.",
            },
            "mode_hint": {
                "type": "string",
                "enum": ["chat", "call"],
                "description": "If the visitor already said which they prefer; otherwise omit and let the UI ask.",
            },
            "concern": {
                "type": "string",
                "enum": ["career", "love", "finance", "marriage", "general"],
                "description": (
                    "Which concern this connect is about, based on the "
                    "conversation so far — used ONLY to word the card's "
                    "subtitle (e.g. 'understands career pressure' vs "
                    "'understands relationship stuff'), never a business "
                    "decision. Use 'general' if nothing specific fits."
                ),
            },
        },
    },
}

NOTIFY_ME_SUBSCRIBE = {
    "name": "notify_me_subscribe",
    "description": (
        "Subscribes the visitor to be notified the moment a specific "
        "astrologer (who's currently offline/busy) comes online. Only ever "
        "called for someone the visitor themselves already named and "
        "search_astrologers already resolved to an id — use this as the "
        "conversion-recovery path when that person isn't available right "
        "now — ALWAYS also call trigger_recommend_astrologer in the SAME "
        "reply for a live alternate (it automatically shows someone "
        "actually available, whether or not you pass this same "
        "astrologer_id), don't just say 'keep checking the app'. The "
        "result's next_available_at is an estimated return "
        "time when known — say it directly ('back in about 40 minutes, "
        "I'll ping you then too') rather than a vague 'I'll notify you'; "
        "when it's null there's genuinely no known return time, so the "
        "notification framing alone is what you have. Also the correct "
        "response if the visitor asks for their phone number — never "
        "share a phone number or private contact detail, offer this "
        "instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "astrologer_id": {
                "type": "string",
                "enum": ["mahalakshmi", "samrat", "nidhi"],
                "description": "The offline/busy astrologer to notify about.",
            }
        },
        "required": ["astrologer_id"],
    },
}

TRIGGER_PAYMENT_BOTTOMSHEET = {
    "name": "trigger_payment_bottomsheet",
    "description": (
        "Surfaces the existing low-balance or recharge bottom sheet in the "
        "app UI, and its result tells you the real supported payment "
        "methods (UPI, cards, net banking, wallets, etc.) — quote those "
        "back rather than guessing a method the visitor asks about. You "
        "never set or suggest a specific amount — the visitor picks from "
        "the app's own fixed packages once the sheet opens. Use "
        "'low_balance' when a booking/call is blocked on insufficient "
        "coins, 'recharge' for a general top-up request."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["low_balance", "recharge"]}
        },
        "required": ["kind"],
    },
}

CREATE_SUPPORT_TICKET = {
    "name": "create_support_ticket",
    "description": (
        "Raises a ticket to the CS team via the existing Zoho Desk "
        "connection, pre-filled with category, the whole-conversation "
        "summary, any evidence, and the visitor's LTV tier. The CS team "
        "owns ALL triage/priority/resolution from here — you decide "
        "nothing about outcome, you only decide WHEN to escalate: after "
        "troubleshooting/explaining didn't resolve it, or the visitor "
        "explicitly wants a cash refund (never payable in coins), or asks "
        "for a person. NEVER tell the visitor a ticket was raised, or that "
        "a team was notified, unless this tool was actually called in this "
        "exact turn and it succeeded.\n\n"
        "TEMPORARY — coin credits are paused: credit_coins and "
        "check_refund_eligibility are both disabled for now, so no coin "
        "credit or refund goes out automatically for ANYTHING — a "
        "disputed booking, astrologer-didn't-respond, 'too expensive', or "
        "any other complaint that used to get a credit. Never promise "
        "coins, a refund, or a credit amount for any of these. Instead, "
        "every time: apologize once, briefly and genuinely ('sorry you "
        "had that experience' / 'maaf karna, yeh sahi nahi hua'), call "
        "this tool with category='refund', and in that SAME reply ALSO "
        "call trigger_recommend_astrologer to offer connecting them with "
        "a top astrologer right now — that's the actual make-good while "
        "the ticket is with the CS team. Never describe or offer that "
        "connect option in text without also calling trigger_recommend_"
        "astrologer in the same turn; the card has to actually be there "
        "whenever you reference it. EXCEPTION: if the visitor has "
        "explicitly said they don't want to talk to an astrologer / just "
        "want support, skip trigger_recommend_astrologer this turn — "
        "raise the ticket (or just ask what happened, if you don't have "
        "enough yet) without it, and only bring connecting back up if "
        "they ask for it themselves.\n\n"
        "ALSO TEMPORARY — no real-time payment/wallet/booking visibility: "
        "get_payment_status, get_booking_details, get_wallet_status and "
        "get_queue_position are ALL disabled too. You only ever have the "
        "visitor's user_id and, when verified, their name — nothing about "
        "a specific recharge, booking, coin balance or queue position. "
        "NEVER invent or estimate one of these ('your payment is "
        "pending', 'your balance is actually X', 'you're 3rd in queue') — "
        "a number that doesn't match what they see in their own app is "
        "worse than no answer, and is exactly what erodes trust. For a "
        "genuine dispute (coins wrongly deducted, payment failed but "
        "money gone, a booking that went wrong), call this tool with "
        "category='payment' or 'billing_dispute' so the team who can "
        "actually see the real data checks it — same apologize-once, "
        "raise-the-ticket, pivot-to-connect pattern as above. For a "
        "plain informational ask with no real problem behind it ('what's "
        "my balance', 'how long is the wait', 'is my recharge done') — "
        "skip the ticket, just don't state a number, and warmly steer "
        "toward connecting them with a top astrologer instead (see "
        "trigger_recommend_astrologer).\n\n"
        "Use 'account' for a delete-account "
        "request once you've asked why and it isn't something you can fix "
        "in this chat — never claim the account was deleted, only that "
        "it's been sent to the team who handles it. Use 'report' for the "
        "visitor reporting another user or an astrologer (attach the "
        "relevant booking/session as evidence_url or in the description) "
        "— you never resolve these yourself, only route them. Use "
        "'language_change' for a consultation-language-change request — "
        "never claim the language changed until this ticket's own process "
        "confirms it. Use 'escalation' for a visitor asking for a manager/"
        "senior person, or saying this is their second time writing with "
        "nothing resolved. For a legal threat or fraud accusation, use "
        "'escalation' or 'refund' immediately — this ticket is the only "
        "response, never any credit or promise of one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "payment", "refund", "account", "astrologer_queue",
                    "billing_dispute", "quality_complaint", "feature_request",
                    "technical", "language_change", "report", "escalation",
                ],
            },
            "sub_category": {"type": "string"},
            "description": {
                "type": "string",
                "description": "Whole-conversation summary, not just the visitor's last message.",
            },
            "evidence_url": {
                "type": "string",
                "description": "URL of a shared screenshot/recording, if any.",
            },
        },
        "required": ["category", "sub_category", "description"],
    },
}

LOG_FEATURE_REQUEST = {
    "name": "log_feature_request",
    "description": (
        "Logs a feature request or an ask for something not yet built "
        "(chat history export, account deletion via chat, etc.) for the "
        "product team. These are honest product gaps — acknowledge "
        "warmly, log it, and NEVER escalate to a support ticket for this. "
        "For 'delete_account_info' specifically: this is a retention "
        "moment, not a form to fill — do NOT call this tool the first time "
        "someone asks to delete their account. Ask ONE warm question about "
        "what's not working first, and only log it once they've actually "
        "given a reason (or flatly insist without one after being asked). "
        "If the reason is cost/value ('too expensive', 'not worth it'), "
        "coin credits are disabled for now — don't offer a retention "
        "credit. Apologize briefly, call create_support_ticket "
        "(category='refund'), and call trigger_recommend_astrologer in "
        "the same reply to offer connecting them with a top astrologer. "
        "Also use 'astrologer_signup_lead' when someone wants to JOIN the "
        "platform as an astrologer — this logs their interest for the "
        "onboarding team to reach out directly; never redirect them off-app "
        "or promise a specific timeline."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": [
                    "feature_request", "delete_chat_history", "delete_account_info",
                    "astrologer_signup_lead",
                ],
            },
        },
        "required": ["text", "kind"],
    },
}

MARK_ISSUE_RESOLVED = {
    "name": "mark_issue_resolved",
    "description": (
        "Call this whenever the conversation is actually winding down — "
        "either because you helped resolve a real problem and the visitor "
        "confirmed it's fixed, OR because you asked if they need anything "
        "else and they said no/that's all/bye. This is what puts up the "
        "rating card and closes the chat, so don't skip it and just say "
        "bye in text — that leaves the visitor stuck with no rating and "
        "an open chat. Never creates a ticket either way. Do not call "
        "this mid-conversation for a question you just answered "
        "informationally with no ending signal from the visitor."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"category": {"type": "string"}},
        "required": ["category"],
    },
}

GET_TICKETS = {
    "name": "get_tickets",
    "description": "Lists the visitor's own open/recent support tickets — for 'what's the status of my ticket'.",
    "input_schema": {"type": "object", "properties": {}},
}

REOPEN_TICKET = {
    "name": "reopen_ticket",
    "description": (
        "Call this when the visitor says a past issue isn't actually "
        "fixed / is still happening / 'you closed this but it's still "
        "broken' — NOT for a brand-new complaint (use create_support_"
        "ticket for that). Always acts on their own most recent Resolved/"
        "Closed ticket automatically — there's no ticket_id field, never "
        "ask them for one. Real rule, enforced server-side, not "
        "something you decide: only reopens if that ticket was resolved "
        "within the last 72 hours. Check the result's `reopened` field:\n"
        "- true: tell them plainly it's reopened and being looked at "
        "again — treat it exactly like a fresh escalation from here "
        "(e.g. still offer to connect them with an astrologer while it's "
        "looked at, same as create_support_ticket's own pattern).\n"
        "- false with reason='past_reopen_window': it's been too long to "
        "reopen — apologize briefly, then call create_support_ticket "
        "for a NEW ticket instead (never claim the old one reopened).\n"
        "- false with reason='no_resolved_ticket': they don't have a "
        "resolved/closed ticket to reopen at all — this is probably a "
        "new issue, treat it as one (create_support_ticket)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One-line factual summary of what they said is still wrong, for the audit trail.",
            }
        },
    },
}

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
GET_WALLET_STATUS = {
    "name": "get_wallet_status",
    "description": (
        "PAUSED — see create_support_ticket's note on why this and the "
        "other visibility-into-a-specific-payment/booking/wallet tools "
        "are disabled right now."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
GET_ACTIVE_OFFERS = {
    "name": "get_active_offers",
    "description": (
        "Fetches the real active recharge offers/promotions. Use this for "
        "'any offers right now' or pricing questions — never invent a "
        "discount, bonus-coin amount, or promo code that isn't in this "
        "result."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

# Deliberately not in ALL_TOOLS right now — see the comment there.
# Kept defined so re-enabling is a one-line change.
GET_QUEUE_POSITION = {
    "name": "get_queue_position",
    "description": (
        "PAUSED — see create_support_ticket's note on why this and the "
        "other visibility-into-a-specific-payment/booking/wallet tools "
        "are disabled right now."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

GET_APP_FAQ = {
    "name": "get_app_faq",
    "description": (
        "Looks up a real answer for general 'how does the app work' "
        "questions (recharging, consultations, coins, refund policy, "
        "etc.) from AstroLokal's own FAQ. ALWAYS call this for general "
        "app-usage questions instead of answering from your own general "
        "knowledge. If it returns no match, say so honestly rather than "
        "guessing how the app works.\n\n"
        "For a 'how do I use the app' / 'how does this work' question "
        "specifically (as opposed to a narrower question like refund "
        "policy), ALSO call trigger_recommend_astrologer in the same "
        "turn right after giving the real FAQ answer — the best way to "
        "actually see how the app works is to connect with an "
        "astrologer right now, so make that the natural next line "
        "('best way to see it in action — want me to connect you?'), "
        "not a bolted-on offer. For a narrower FAQ question (refund "
        "policy, what coins are, etc.) only suggest connecting if that's "
        "actually relevant to what they asked."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The visitor's question, as they asked it."}
        },
        "required": ["query"],
    },
}

ALL_TOOLS = [
    # GET_PAYMENT_STATUS, GET_BOOKING_DETAILS, GET_WALLET_STATUS and
    # GET_QUEUE_POSITION deliberately NOT registered for now — none of
    # them are real: SessionContext only ever carries user_id and,
    # when verified, user_name (see agent/context.py), so these were
    # always mocked stand-ins presented as if real, which is exactly what
    # VOC testing flagged as an active-distrust risk (a stated balance/
    # status that contradicts what the visitor actually sees in the app).
    # Any payment/wallet/booking/queue question routes through
    # CREATE_SUPPORT_TICKET (genuine dispute) or straight to
    # TRIGGER_RECOMMEND_ASTROLOGER (plain informational ask) instead —
    # see CREATE_SUPPORT_TICKET's own description. Schemas/handlers/mock
    # data stay in place, ready to re-enable once real data exists.
    # CHECK_REFUND_ELIGIBILITY and CREDIT_COINS deliberately NOT
    # registered for now — coin credits of any kind are paused. Any
    # refund/credit-worthy complaint routes through CREATE_SUPPORT_
    # TICKET's category='refund' + TRIGGER_RECOMMEND_ASTROLOGER instead
    # (see CREATE_SUPPORT_TICKET's own description). Schemas/handlers/
    # service code stay in place, ready to re-enable.
    GET_LTV_TIER,
    SEARCH_ASTROLOGERS,
    TRIGGER_RECOMMEND_ASTROLOGER,
    NOTIFY_ME_SUBSCRIBE,
    TRIGGER_PAYMENT_BOTTOMSHEET,
    CREATE_SUPPORT_TICKET,
    LOG_FEATURE_REQUEST,
    MARK_ISSUE_RESOLVED,
    GET_TICKETS,
    REOPEN_TICKET,
    # GET_ACTIVE_OFFERS deliberately NOT registered for now — offers/
    # discount questions are routed through TRIGGER_RECOMMEND_ASTROLOGER's
    # "connect at a discount" framing instead (see its description). The
    # schema/handler/mock data stay in place, ready to re-enable.
    GET_APP_FAQ,
]
