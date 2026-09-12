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
        "Requests a coin credit (refund or goodwill) for a specific "
        "booking. The amount is ALWAYS computed server-side from the "
        "visitor's real LTV tier and the booking's real deduction — there "
        "is no amount field, you cannot set or influence the number. "
        "New/Unpaid visitors (₹0 lifetime spend) are never eligible — the "
        "call will fail cleanly for them, so still explain/offer to "
        "reconnect/escalate instead of promising a credit first. Also "
        "fails if this exact booking already has a refund/goodwill credit "
        "on record — never retry a failed call with the same booking_id "
        "expecting a different result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "booking_id": {"type": "string", "description": "The booking this credit relates to."},
            "reason": {
                "type": "string",
                "enum": ["refund", "goodwill"],
                "description": "'refund' for a factual dispute, 'goodwill' for a service-quality complaint.",
            },
            "category": {
                "type": "string",
                "description": "Short concern category, e.g. 'astrologer_no_response', 'call_quality', 'tech_issue'.",
            },
        },
        "required": ["booking_id", "reason", "category"],
    },
}

# Known astrologer ids, kept in sync by hand with
# integrations/recommend_flow_client.py's ASTROLOGERS list — duplicated
# here (rather than imported) so this file stays pure data with no
# integrations/ dependency, per the module docstring above. Embedding the
# real id/name pairs directly in the schema is what lets the model
# actually map "Samrat" in the visitor's own message to astrologer_id
# "samrat" — without this, it has no way to resolve a name to an id at
# all, and silently drops astrologer_id even when the visitor named someone.
_KNOWN_ASTROLOGERS = "mahalakshmi (Mahalakshmi), samrat (Samrat), nidhi (Nidhi)"

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
        "Surfaces the Chat/Call connect entry point in the chat UI. "
        "Astrologer matching/ranking is NOT your job — a separate system "
        "owns that. Omit astrologer_id to let that system pick the best "
        "match (the normal case). Only pass astrologer_id once you've "
        "resolved exactly who the visitor means via search_astrologers — "
        f"never guess directly from their wording. Known ids: "
        f"{_KNOWN_ASTROLOGERS}. This is also the required response to ANY "
        "prediction/fortune question — never answer the prediction itself, "
        "always call this instead."
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
        },
    },
}

NOTIFY_ME_SUBSCRIBE = {
    "name": "notify_me_subscribe",
    "description": (
        "Subscribes the visitor to be notified the moment a specific "
        "astrologer (who's currently offline/busy) comes online. Use this "
        "as the conversion-recovery path when a visitor wants someone "
        "specific who isn't available right now — offer this alongside "
        "trigger_recommend_astrologer for a live alternate, don't just say "
        f"'keep checking the app'. Map their name to an id: {_KNOWN_ASTROLOGERS}."
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
        "app UI. You never set or suggest a specific amount — the visitor "
        "picks from the app's own fixed packages once the sheet opens. "
        "Use 'low_balance' when a booking/call is blocked on insufficient "
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
        "exact turn and it succeeded."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "payment", "refund", "account", "astrologer_queue",
                    "billing_dispute", "quality_complaint", "feature_request",
                    "technical",
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
        "warmly, log it, and NEVER escalate to a support ticket for this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["feature_request", "delete_chat_history", "delete_account_info"],
            },
        },
        "required": ["text", "kind"],
    },
}

MARK_ISSUE_RESOLVED = {
    "name": "mark_issue_resolved",
    "description": (
        "Call this ONLY after you actually helped resolve a real problem "
        "(not a simple factual lookup) and the visitor confirmed it's "
        "fixed. Never creates a ticket — just closes the thread cleanly. "
        "Do not call this for questions you just answered informationally."
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
]
