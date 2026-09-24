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

GET_PAYMENT_STATUS = {
    "name": "get_payment_status",
    "description": (
        "Checks the visitor's most recent recharge/payment attempt — status "
        "(success/pending/failed), coins added, and order timestamp. Always "
        "call this before answering 'I recharged but coins weren't added' "
        "rather than guessing. If the payment failed, real provider refunds "
        "take 5-7 days — state that plainly rather than promising a faster "
        "fix."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

GET_BOOKING_DETAILS = {
    "name": "get_booking_details",
    "description": (
        "Fetches a specific booking's real status: duration, coins "
        "deducted, astrologer response count, and end reason. Omit "
        "booking_id to get the visitor's most recent booking. Always call "
        "this before discussing a refund/duration dispute or 'astrologer "
        "took too long' — never estimate what happened in a session."
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
        "decide whether to call credit_coins."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

CREDIT_COINS = {
    "name": "credit_coins",
    "description": (
        "Requests a coin credit for a specific booking, following the "
        "real Refund & Bonus Logic SOP: facts decide WHETHER/HOW MUCH "
        "gets refunded (issue_tag below), LTV only sizes the small bonus "
        "on top — you never set or influence the amount, there is no "
        "amount field. Pick the issue_tag that matches what actually "
        "happened (fetch get_booking_details first if you're not sure) — "
        "astrologer-fault tags always refund in full regardless of tier; "
        "user-fault tags never refund; partial-fault tags refund unused "
        "coins plus a tier-sized bonus; the no-clear-signal tags "
        "automatically escalate to a support ticket (this tool creates it "
        "for you in that case — don't also call create_support_ticket "
        "yourself for the same complaint). Also use a no-clear-signal tag "
        "for a legal threat or fraud accusation — that path always "
        "skips any bonus and goes straight to a ticket, offering coins "
        "there reads as admitting fault. Fails cleanly if this exact "
        "booking already has a credit on record — never retry with the "
        "same booking_id expecting a different result. For a visitor "
        "saying the app/service is too expensive and considering leaving "
        "with NO specific booking in dispute, omit booking_id entirely — "
        "that's a separate small retention gesture, not this SOP."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "booking_id": {
                "type": "string",
                "description": "The disputed booking. Omit ONLY for a no-booking-in-dispute retention gesture (e.g. 'this app is too expensive').",
            },
            "issue_tag": {
                "type": "string",
                "enum": [
                    # Step 1 — astrologer/system at fault: always refund in full.
                    "astro_did_not_reply", "no_one_responded",
                    "consultation_not_done_coins_deducted", "astrologer_answered_and_disconnected",
                    # Step 2 — visitor's own fault: never refund.
                    "user_did_not_reply", "user_replied_late",
                    # Step 3 — partial fault: refund unused coins + tier bonus.
                    "astrologer_took_more_time", "audio_video_not_clear",
                    "chat_glitch_mid_session", "session_disconnected_mid_way",
                    # Step 4 — no clear factual signal: escalate to a ticket automatically.
                    "astrologer_not_helpful", "poor_prediction_quality",
                    "scam_or_trust_complaint", "blank_screen_unconfirmed",
                ],
                "description": "Required when booking_id is set — omit only for the retention case above.",
            },
            "reason": {
                "type": "string",
                "description": "One-line factual summary for the ticket/audit trail if this escalates or gets reviewed later.",
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
        "nobody by that name (fall back to a generic match instead — don't "
        "say you don't have information, just pivot), one means unambiguous "
        "(confirm briefly, then proceed), two or more means genuinely "
        "ambiguous (ask a one-line question naming the options before "
        "proceeding — never pick one silently)."
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
        "The result's next_available_at is a real scheduled return time — "
        "when it's set, say that time directly ('back at 6 PM') rather "
        "than a vague 'I'll let you know when they're back'; you can "
        "still offer notify_me_subscribe on top of stating the time. When "
        "next_available_at is null, there's no known return time at all — "
        "notify_me_subscribe's push-notification framing is what you have. "
        "ALSO the required response to any offers/discount/promo-code "
        "question ('any offers right now?', 'any discount?') — that data "
        "isn't available right now, so never state a specific offer, "
        "percentage or code. Instead pivot to connecting them with a "
        "quality astrologer at a discounted price, framed as exclusive to "
        "them (e.g. 'a discount just for you'), and call this tool."
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
        "now, offered alongside trigger_recommend_astrologer for a live "
        "alternate, don't just say 'keep checking the app'. The result's "
        "next_available_at is a real scheduled return time when known — "
        "say it directly ('back at 6 PM, I'll ping you then too') rather "
        "than a vague 'I'll notify you'; when it's null there's genuinely "
        "no known return time, so the notification framing alone is what "
        "you have. Also the correct response if the visitor asks for "
        "their phone number — never share a phone number or private "
        "contact detail, offer this instead."
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
        "exact turn and it succeeded. Use 'account' for a delete-account "
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
        "'escalation' or 'refund' immediately — do NOT call credit_coins "
        "as a response to a threat, that reads as admitting fault; this "
        "ticket is the only response."
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
        "prefer credit_coins as a retention gesture over logging this. "
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
        "else and they said no/that's all/bye. This is what ends the chat "
        "on the astrologer connect card plus the rating card, so don't "
        "skip it and just say "
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

GET_WALLET_STATUS = {
    "name": "get_wallet_status",
    "description": (
        "Checks the visitor's real current coin balance and whether "
        "anything was actually deducted recently. Use this for a 'my "
        "coins keep going down' / 'coins missing' complaint that ISN'T "
        "about one specific booking — if nothing was really deducted, "
        "explain the balance is safe rather than offering a credit. "
        "Never call credit_coins just because a balance looks lower than "
        "expected without checking this first."
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

GET_QUEUE_POSITION = {
    "name": "get_queue_position",
    "description": (
        "Checks the visitor's real position and estimated wait in an "
        "astrologer's live queue. Use this for 'how long is the wait' "
        "questions — never estimate a wait time yourself. A long queue is "
        "also a good natural moment to offer a live available alternative "
        "via trigger_recommend_astrologer."
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
        "guessing how the app works — and only suggest connecting with an "
        "astrologer if that's actually relevant to what they asked."
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
    GET_PAYMENT_STATUS,
    GET_BOOKING_DETAILS,
    CHECK_REFUND_ELIGIBILITY,
    GET_LTV_TIER,
    CREDIT_COINS,
    SEARCH_ASTROLOGERS,
    TRIGGER_RECOMMEND_ASTROLOGER,
    NOTIFY_ME_SUBSCRIBE,
    TRIGGER_PAYMENT_BOTTOMSHEET,
    CREATE_SUPPORT_TICKET,
    LOG_FEATURE_REQUEST,
    MARK_ISSUE_RESOLVED,
    GET_TICKETS,
    GET_WALLET_STATUS,
    # GET_ACTIVE_OFFERS deliberately NOT registered for now — offers/
    # discount questions are routed through TRIGGER_RECOMMEND_ASTROLOGER's
    # "connect at a discount" framing instead (see its description). The
    # schema/handler/mock data stay in place, ready to re-enable.
    GET_QUEUE_POSITION,
    GET_APP_FAQ,
]
