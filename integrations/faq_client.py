"""MOCKED — small static FAQ standing in for a real RAG/knowledge-base
lookup over AstroLokal's actual help-center content. Keyword-matched, not
semantic — good enough to keep the model from inventing app-usage answers
on a handful of common questions. Replace search() with a real retrieval
call (embeddings + a real doc corpus) when one exists; each entry below is
meant to be real product copy kept in sync by hand until then, not
placeholder text.
"""

FAQ_ENTRIES = [
    {
        "keywords": ["recharge", "add coins", "top up", "topup"],
        "answer": "Tap the wallet icon, pick a package, pay — coins land instantly on success.",
    },
    {
        "keywords": ["how does this work", "what is this app", "how does astrolokal work", "what is astrolokal"],
        "answer": "You chat or call a real astrologer here, paying per minute in coins.",
    },
    {
        "keywords": ["how does consultation work", "how does chat work", "how does call work"],
        "answer": "You connect live with an astrologer — coins deduct per minute you're talking.",
    },
    {
        "keywords": ["what are coins", "coins work"],
        "answer": "Coins are AstroLokal's in-app currency — recharge once, spend per minute.",
    },
    {
        "keywords": ["refund policy", "refund rules", "how do refunds work"],
        "answer": "If a session didn't really happen, it's auto-refunded — I can check a specific one too.",
    },
]


def search(query: str) -> dict:
    """Simple keyword match over FAQ_ENTRIES. Returns {"answer": str} on a
    hit, {"answer": None} on a miss — the model is instructed to say so
    honestly rather than guessing how the app works when this misses."""
    normalized = (query or "").lower()
    for entry in FAQ_ENTRIES:
        if any(keyword in normalized for keyword in entry["keywords"]):
            return {"answer": entry["answer"]}
    return {"answer": None}
