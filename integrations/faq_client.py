"""MOCKED — small static FAQ standing in for a real RAG/knowledge-base
lookup over AstroLokal's actual help-center content. Keyword-matched, not
semantic — good enough to keep the model from inventing app-usage answers
on a handful of common questions. Replace search() with a real retrieval
call (embeddings + a real doc corpus) when one exists; each entry below is
meant to be real product copy kept in sync by hand until then, not
placeholder text.
"""

FAQ_ENTRIES = [
    # The specific entries below are checked BEFORE the generic
    # "recharge"/"how does this work" entries further down — search()
    # returns the FIRST keyword match, and several of these specific
    # questions (e.g. "how much to recharge for 2 hours") would otherwise
    # get swallowed by the generic "recharge" keyword on the catch-all
    # entry. Order matters here, not just content.
    # hi/ta/te/ml keyword variants below are a v1 heuristic, not
    # native-reviewed — same caveat as every other regional-language
    # keyword list in this app (see app.py's PREDICTION_KEYWORDS comment).
    {
        "keywords": [
            "message not sending", "message not going", "not sending",
            "message failed", "not able to send message", "chat not working",
            "messages not being sent",
            "मैसेज नहीं जा रहा", "मैसेज नहीं भेज पा रहा", "मैसेज फेल हो रहा",
            "மெசேஜ் அனுப்ப முடியவில்லை", "செய்தி அனுப்பப்படவில்லை",
            "మెసేజ్ పంపడం లేదు", "సందేశం పంపబడటం లేదు",
            "സന്ദേശം അയയ്ക്കാൻ കഴിയുന്നില്ല",
        ],
        "answer": (
            "Check your internet connection, close and reopen the app, make sure "
            "it's updated to the latest version, then try sending again — that "
            "clears it most of the time."
        ),
    },
    {
        # accuracy_critical: live testing showed the model sometimes skips
        # calling get_app_faq entirely for a question like this and just
        # guesses — confirmed wrong both in English (search bar) and
        # Tamil (privacy). app.py checks this flag BEFORE the model runs
        # and answers straight from here when it matches, same as
        # PREDICTION_KEYWORDS' own bypass — trades some of the model's
        # natural phrasing for a guarantee of correctness on these
        # specific, fact-sensitive questions.
        "accuracy_critical": True,
        "keywords": [
            "privacy", "is my data safe", "can astrologer see my name",
            "chat history visible", "does astrologer see my details",
            "my information private", "astro see my name",
            "प्राइवेसी", "मेरा डेटा सुरक्षित", "नाम दिखेगा", "चैट हिस्ट्री दिखेगी",
            "தனியுரிமை", "என் தகவல் பாதுகாப்பானதா",
            "గోప్యత", "నా డేటా సురక్షితమా",
            "സ്വകാര്യത", "എന്റെ ഡാറ്റ സുരക്ഷിതമാണോ",
        ],
        # accuracy_critical entries carry per-language answers (dict, not
        # a plain string) since these are served DIRECTLY to the visitor
        # via app.py's bypass (see critical_fact) with no model rephrasing
        # in between — unlike the other entries here, which only ever
        # reach the model as raw tool data for it to translate/rephrase
        # itself. hi/ta/te/ml text is a v1 heuristic, not native-reviewed.
        "answer": {
            "en": (
                "Whatever details you enter when starting a consultation (like your "
                "name) are visible to that astrologer, and your chat history becomes "
                "visible to them only once you've actually consulted with them."
            ),
            "hi": (
                "कंसल्टेशन शुरू करते समय आप जो भी जानकारी (जैसे अपना नाम) डालते हैं, वह उस "
                "ज्योतिषी को दिखती है, और आपकी चैट हिस्ट्री सिर्फ कंसल्टेशन के बाद ही उन्हें दिखती है।"
            ),
            "ta": (
                "ஆலோசனையைத் தொடங்கும்போது நீங்கள் உள்ளிடும் விவரங்கள் (உங்கள் பெயர் போன்றவை) "
                "அந்த ஜோதிடருக்குத் தெரியும், மேலும் உங்கள் அரட்டை வரலாறு நீங்கள் உண்மையில் "
                "அவருடன் ஆலோசனை செய்த பிறகு மட்டுமே அவருக்குத் தெரியும்."
            ),
            "te": (
                "కన్సల్టేషన్ ప్రారంభించేటప్పుడు మీరు నమోదు చేసే వివరాలు (మీ పేరు వంటివి) ఆ "
                "జ్యోతిష్కుడికి కనిపిస్తాయి, మీ చాట్ హిస్టరీ మీరు నిజంగా కన్సల్ట్ చేసిన తర్వాతే "
                "వారికి కనిపిస్తుంది."
            ),
            "ml": (
                "കൺസൾട്ടേഷൻ തുടങ്ങുമ്പോൾ നിങ്ങൾ നൽകുന്ന വിവരങ്ങൾ (നിങ്ങളുടെ പേര് പോലുള്ളവ) ആ "
                "ജ്യോതിഷിക്ക് കാണാൻ കഴിയും, നിങ്ങളുടെ ചാറ്റ് ചരിത്രം യഥാർത്ഥത്തിൽ കൺസൾട്ട് "
                "ചെയ്ത ശേഷം മാത്രമേ അവർക്ക് കാണാൻ കഴിയൂ."
            ),
        },
    },
    {
        "accuracy_critical": True,
        "keywords": [
            "minimum coins", "how many coins to start", "minimum balance",
            "enough coins to connect", "coins needed to connect", "minimum to connect",
            "न्यूनतम कॉइन", "कितने कॉइन चाहिए", "मिनिमम कॉइन",
            "குறைந்தபட்ச காயின்கள்", "எத்தனை காயின்கள் தேவை",
            "కనీస కాయిన్లు", "ఎన్ని కాయిన్లు కావాలి",
            "കുറഞ്ഞ കോയിനുകൾ", "എത്ര കോയിൻ വേണം",
        ],
        "answer": {
            "en": "You need at least 100 coins in your wallet to start a chat or call.",
            "hi": "चैट या कॉल शुरू करने के लिए आपके वॉलेट में कम से कम 100 कॉइन होने चाहिए।",
            "ta": "அரட்டை அல்லது அழைப்பைத் தொடங்க உங்கள் வாலட்டில் குறைந்தது 100 காயின்கள் இருக்க வேண்டும்.",
            "te": "చాట్ లేదా కాల్ ప్రారంభించడానికి మీ వాలెట్‌లో కనీసం 100 కాయిన్లు ఉండాలి.",
            "ml": "ചാറ്റോ കോളോ തുടങ്ങാൻ നിങ്ങളുടെ വാലറ്റിൽ കുറഞ്ഞത് 100 കോയിൻ വേണം.",
        },
    },
    {
        "keywords": [
            "23 rupees", "paid 23", "100 coins 5 minutes", "23 for 5 minutes", "rs 23",
            "23 रुपये", "23 रुपए", "23 रूपए",
            "23 ரூபாய்", "23 రూపాయలు", "23 രൂപ",
        ],
        "answer": (
            "That's correct pricing — ₹23 gets you 100 coins, which covers about "
            "5 minutes on your first recharge."
        ),
    },
    {
        "accuracy_critical": True,
        "keywords": [
            "search bar", "search option", "option to search", "find astrologer by name",
            "search for astrologer", "search for astro", "search for an astro",
            "search astrologer", "search an astrologer", "search by name",
            "find astro by name", "find an astro by name",
            "सर्च बार", "नाम से खोज", "सर्च ऑप्शन", "नाम से सर्च",
            "தேடல் பட்டி", "பெயர் மூலம் தேடல்",
            "సెర్చ్ బార్", "పేరుతో వెతకడం",
            "സെർച്ച് ബാർ", "പേര് വഴി തിരയൽ",
        ],
        "answer": {
            "en": "There's no search bar in the app right now — just tell me the name and I'll look them up for you.",
            "hi": "अभी ऐप में सर्च बार नहीं है — बस मुझे नाम बताइए, मैं ढूंढ देती हूँ।",
            "ta": "இப்போது ஆப்பில் தேடல் பட்டி இல்லை — பெயரை சொல்லுங்கள், நான் தேடிக் கொடுக்கிறேன்.",
            "te": "ప్రస్తుతం యాప్‌లో సెర్చ్ బార్ లేదు — పేరు చెప్పండి, నేను వెతికి ఇస్తాను.",
            "ml": "ഇപ്പോൾ ആപ്പിൽ സെർച്ച് ബാർ ഇല്ല — പേര് പറയൂ, ഞാൻ തിരഞ്ഞു തരാം.",
        },
    },
    {
        "accuracy_critical": True,
        "keywords": [
            "edit profile", "change my details", "update my name",
            "edit my details", "change profile", "edit my profile",
            "प्रोफाइल एडिट", "डिटेल्स बदलना", "नाम बदलना", "प्रोफाइल बदलना",
            "சுயவிவரத்தை திருத்த", "பெயரை மாற்ற",
            "ప్రొఫైల్ ఎడిట్", "పేరు మార్చడం",
            "പ്രൊഫൈൽ എഡിറ്റ്", "പേര് മാറ്റുക",
        ],
        "answer": {
            "en": (
                "Profile details are locked after you first set them up, but you can "
                "re-enter them at the start of a new consultation."
            ),
            "hi": (
                "प्रोफाइल डिटेल्स पहली बार सेट करने के बाद लॉक हो जाती हैं, लेकिन आप नए "
                "कंसल्टेशन की शुरुआत में उन्हें दोबारा डाल सकते हैं।"
            ),
            "ta": (
                "சுயவிவரத் தகவல்கள் முதலில் அமைத்த பிறகு பூட்டப்படும், ஆனால் புதிய ஆலோசனை "
                "தொடங்கும்போது அவற்றை மீண்டும் உள்ளிடலாம்."
            ),
            "te": (
                "ప్రొఫైల్ వివరాలు మొదటిసారి సెట్ చేసిన తర్వాత లాక్ అవుతాయి, కానీ కొత్త "
                "కన్సల్టేషన్ ప్రారంభంలో వాటిని మళ్లీ నమోదు చేయవచ్చు."
            ),
            "ml": (
                "പ്രൊഫൈൽ വിവരങ്ങൾ ആദ്യം സജ്ജമാക്കിയ ശേഷം ലോക്ക് ആകും, പക്ഷേ പുതിയ "
                "കൺസൾട്ടേഷൻ തുടങ്ങുമ്പോൾ അവ വീണ്ടും നൽകാം."
            ),
        },
    },
    {
        "keywords": [
            "hour call", "hours call", "hour long", "long call",
            "for an hour", "for 2 hour", "for 3 hour", "hour video",
            "घंटे की कॉल", "1 घंटे के लिए", "2 घंटे के लिए", "लंबी कॉल",
            "ஒரு மணி நேரம் கால்", "2 மணி நேரம்",
            "గంట కాల్", "2 గంటలు",
            "ഒരു മണിക്കൂർ കോൾ", "2 മണിക്കൂർ",
        ],
        "answer": (
            "It depends on which astrologer you pick, since per-minute rates vary "
            "— check their price before you start so you know roughly how long "
            "your recharge will last."
        ),
    },
    {
        "keywords": [
            "price went up", "price increased", "rate went up", "rate increased",
            "price changed", "more expensive after", "went up after my first",
            "price per minute went up",
            "कीमत बढ़ गई", "रेट बढ़ गया", "प्राइस बढ़ गई", "रिचार्ज के बाद ज्यादा",
            "விலை அதிகரித்தது", "ரீசார்ஜுக்குப் பிறகு அதிகம்",
            "ధర పెరిగింది", "రీచార్జ్ తర్వాత ఎక్కువ",
            "വില കൂടി", "റീചാർജിന് ശേഷം കൂടുതൽ",
        ],
        "answer": (
            "This is expected, not an error — the first recharge has a special "
            "introductory rate; after that, each astrologer's own regular "
            "per-minute rate applies, which is often higher."
        ),
    },
    {
        "keywords": ["recharge", "add coins", "top up", "topup"],
        "answer": "Tap the wallet icon, pick a package, pay — coins land instantly on success.",
    },
    {
        "keywords": [
            "how does this work", "what is this app", "how does astrolokal work", "what is astrolokal",
            "how do i use", "how to use the app", "how to use this app", "how does the app work",
        ],
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


def _resolve_answer(answer_field, lang: str = "en"):
    """Most entries' answer is a plain string (fine — it only ever reaches
    the model as raw tool data for IT to translate/rephrase). The
    accuracy_critical entries' answer is a {lang: text} dict instead,
    since those are served directly to the visitor with no model
    rephrasing in between (see critical_fact). Falls back to English for
    an unsupported/undetected language."""
    if isinstance(answer_field, dict):
        return answer_field.get(lang) or answer_field.get("en")
    return answer_field


def search(query: str) -> dict:
    """Simple keyword match over FAQ_ENTRIES. Returns {"answer": str} on a
    hit, {"answer": None} on a miss — the model is instructed to say so
    honestly rather than guessing how the app works when this misses."""
    normalized = (query or "").lower()
    for entry in FAQ_ENTRIES:
        if any(keyword in normalized for keyword in entry["keywords"]):
            return {"answer": _resolve_answer(entry["answer"])}
    return {"answer": None}


def critical_fact(query: str, lang: str = "en"):
    """Same keyword match as search(), scoped to only the entries marked
    accuracy_critical=True. Returns the answer string (in `lang`, falling
    back to English) on a hit, None on a miss. Called by app.py BEFORE
    the model runs (see is_prediction_intent's own bypass for the same
    pattern) — these are the handful of questions confirmed live where
    the model skips get_app_faq and guesses wrong instead of calling it,
    so they're answered deterministically rather than left to prompt
    compliance."""
    normalized = (query or "").lower()
    for entry in FAQ_ENTRIES:
        if entry.get("accuracy_critical") and any(keyword in normalized for keyword in entry["keywords"]):
            return _resolve_answer(entry["answer"], lang)
    return None
