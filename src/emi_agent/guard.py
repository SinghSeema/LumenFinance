"""Per-sentence output guard before TTS (LLD section 10). A backstop, not the main defence."""
from __future__ import annotations

import re
from dataclasses import dataclass

from emi_agent.heard import number_tokens, said_numbers

_L = r"(?<![a-z0-9ऀ-ॿ])"
_R = r"(?![a-z0-9ऀ-ॿ])"


def _words(*patterns: str) -> re.Pattern:
    return re.compile(_L + "(?:" + "|".join(patterns) + ")" + _R, re.IGNORECASE)


# "may" is left out of the months on purpose ("May I ask...").
_LOAN_TERMS = _words(
    r"loans?", r"emis?", r"e\.m\.i\.?", r"instal+ments?", r"kis+h?t", r"outstanding", r"overdue",
    r"dues?", r"amount", r"payments?", r"rupees?", r"rupaye", r"rupaya", r"rs\.?", r"inr",
    r"lakhs?", r"crores?", r"thousand", r"hundred", r"haz+aa?r", r"january", r"february",
    r"march", r"april", r"june", r"july", r"august", r"september", r"october", r"november",
    r"december", "लोन", "ईएमआई", "किस्त", "बकाया", "रुपये", "रुपया", "हज़ार", "हजार", "लाख",
    "करोड़", "राशि",
)
_MONTH_NAMES = {"january", "february", "march", "april", "june", "july", "august", "september",
                "october", "november", "december"}
_SPELLED_DIGITS = re.compile(r"(?<!\d)\d(?:[ -]\d)+(?!\d)")
_TIME_OF_DAY = re.compile(
    r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.|o'clock|baje|बजे)" + _R, re.IGNORECASE)

_THREATS = _words(
    r"legal (?:action|notice|case)", r"lawsuit", r"sue you", r"courts?", r"police",
    r"arrest\w*", r"jail", r"prison", r"seiz\w*", r"confiscat\w*", r"repossess\w*",
    r"recovery (?:agents?|team)", r"blacklist\w*", r"your family will", r"tell your family",
    r"inform your (?:employer|family|office)", r"embarrass\w*", r"ruin your", r"kanooni",
    r"adalat", r"giraftar\w*", r"badnaam\w*", r"ghar (?:aa|aayenge|aa jayenge)",
    "पुलिस", "जेल", "अदालत", "कानूनी", "गिरफ्तार", "गिरफ्तारी", "बदनाम", "बदनामी",
    "लीगल नोटिस", "रिकवरी एजेंट", "रिकवरी टीम", "ब्लैकलिस्ट", "जब्त", "कुर्क",
    r"घर (?:आ|आएंगे|आएँगे|आ जाएंगे|पहुंच)", r"(?:परिवार|घरवालों|ऑफिस) (?:को|में) बता",
)
# Devanagari "maaf" in both spellings of फ़ (precomposed, or फ + nukta).
_MAAF = "मा(?:\u092b\u093c?|\u095e)"
_PROMISES = _words(
    r"waiv\w*", r"discount\w*", r"restructur\w*", r"write[- ]?off", r"revers\w* (?:the |your )?(?:late )?fees?",
    r"fee reversal", r"reduce (?:the|your) (?:amount|emi|interest|dues)", r"settlement",
    r"no penalty", r"(?:penalty|fee|fees|byaaj|interest) maaf", r"chhoo?t",
    # Devanagari (sarvam-105b replies in it). Waiver wording only: "माफ़ कीजिए" is "sorry".
    r"(?:ब्याज|पेनल्टी|जुर्माना|फीस|फ़ीस|शुल्क|चार्ज) (?:भी )?" + _MAAF,
    _MAAF + r" कर (?:दूंगी|दूँगी|दूंगा|देंगे|देंगी|दिया|दी|सकती हूं|सकती हूँ|सकते हैं)",
    _MAAF + r"ी मिल", "छूट", "डिस्काउंट", "रीस्ट्रक्चर", "रिस्ट्रक्चर", "पुनर्गठन", "सेटलमेंट",
    "राइट ऑफ", r"(?:किस्त|ईएमआई|राशि|रकम|ब्याज|बकाया) कम कर", r"(?:फीस|फ़ीस|शुल्क|चार्ज) वापस",
)
# A promise word right after "can't"/"not able to" etc. is a refusal, not a promise.
_NEGATED_BEFORE = re.compile(
    r"(?:can't|cannot|can not|not able to|unable to|won't be able to|not allowed to|"
    r"don't have the authority to)\s+(?:\S+\s+){0,3}$", re.IGNORECASE)
_NEGATED_AFTER = re.compile(r"^\s*(?:\S+\s+){0,3}?(?:nahi|nahin|नहीं)", re.IGNORECASE)
# "I cannot promise or suggest any waiver, discount, or fee reversal" (eval S03, 2026-11-04):
# a longer reach back to the refusal, but only over promise-related words and list items, so
# "I cannot wait to give you a discount" is still a promise.
_NEGATED_BEFORE_LONG = re.compile(
    r"(?:can't|cannot|can not|not able to|unable to|won't be able to|not allowed to|"
    r"don't have the authority to)\s+(?:(?:promise|offer|give|provide|suggest|approve|arrange|"
    r"grant|guarantee|discuss|or|and|any|a|an|the|you|your|kind|type|of|late|waivers?|"
    r"discounts?|fees?|reversals?|restructuring|changes?|hint|at|reductions?|delays?|myself|"
    r"anything|such|due|date|dates|emi|amount|this|it|that|change),?\s+){0,10}$", re.IGNORECASE)
# Hindi puts the verb last: "छूट या बदलाव का वादा नहीं कर सकती". Up to 6 words, same clause only
# (a word carrying , . ; । ends the search), and नहीं must be followed by a refusal verb, so
# "छूट दे दूंगी, चिंता की कोई बात नहीं" stays a promise (eval S03, 2026-11-04).
_NEGATED_AFTER_HI = re.compile(
    r"^\s*(?:[^\s,.;।!?]+\s+){0,6}?(?:नहीं|nahi|nahin)\s+"
    r"(?:कर|दे|दी|मिल|सकती|सकते|सकता|पाऊँगी|पाऊंगी|पाएंगे|kar|de|mil|sakti|sakte)"
    r"(?=[\s,.;।!?]|$)",  # whole word: "होगी" is not a refusal verb
    re.IGNORECASE)

_SAFE = {
    "change_short": {
        "en": "I'm not able to make changes to your account myself.",
        "roman": "Main khud account mein koi badlav nahi kar sakti.",
        "devanagari": "मैं खुद अकाउंट में कोई बदलाव नहीं कर सकती।",
    },
    "unverified_disclosure": {
        "en": "Before I share any details, I just need to confirm who I'm speaking with.",
        "roman": "Koi bhi jaankari dene se pehle, mujhe bas aapki pehchaan confirm karni hogi.",
        "devanagari": "कोई भी जानकारी देने से पहले, मुझे बस आपकी पहचान कन्फ़र्म करनी होगी।",
    },
    "change": {
        "en": "I'm not able to make changes to your account myself, but our internal team will check "
              "this and arrange a call back for you.",
        "roman": "Main khud account mein koi badlav nahi kar sakti, lekin hamari internal team ise check "
                 "karke aapko callback arrange karegi.",
        "devanagari": "मैं खुद अकाउंट में कोई बदलाव नहीं कर सकती, लेकिन हमारी internal team इसे check "
                      "करके आपको callback arrange करेगी।",
    },
}


# Verification results the model may only state after a real check (G-08). Live call 2026-11-04:
# code noted "day still missing, never say it does not match"; the model said "यह जानकारी भी मेल
# नहीं खा रही। आपके पास एक आखिरी मौका है।" with no check run and two attempts left.
# Mixed script too: "वो भी match नहीं हुआ। आपके पास बस एक ही मौका बचा है" and "कोई मौका नहीं बचा"
# were said with no check run (v10 fix-run 2, S10 run 1).
_MISMATCH = _words(r"(?:does|did|do|is|was)\s*(?:n't|not)\s+match\w*", r"not\s+match\w*", r"no match",
                   r"match nahi\w*", r"mel nahi\w*", r"match\s+नहीं", r"मेल\s+नहीं", "मैच नहीं", "मिलान नहीं")
_ATTEMPTS = _words(r"(?:one more|another|ek aur|last|final|aakhri|akhri) (?:chance|attempt|try|mauka)",
                   r"(?:attempts?|chances?|tries) left", r"no (?:more )?(?:attempts|chances)",
                   r"(?:mauka|mauke) (?:baaki|bacha|bache)", "एक और मौका", "आखिरी मौका", "अंतिम मौका",
                   r"मौक[ाे] (?:बाकी|बचा|बचे)", r"मौका नहीं बचा", "कोशिश बाकी",
                   # testing-regression S10 run 1: "आपके पास बस एक ही attempt बचा है", "attempts पूरे हो चुके हैं"
                   r"attempts? (?:बाकी|बचा|बचे|पूरे|ख़त्म|खत्म)", r"attempts? (?:baaki|bacha|bache|poore|khatam)")
_LAST = _words(r"last", r"final", r"aakhri", r"akhri", "आखिरी", "अंतिम")


# Notices nobody wrote (C2 again, prompt v12 run: "Just a reminder, this call is being recorded.",
# "For your privacy, please do not share this payment link with anyone." before any link). Invented
# policy (G-08), so the sentence is dropped, not replaced.
_NOTICE = re.compile(
    r"\b(?:this|the) call (?:is being|may be|will be|is|might be) recorded|"
    r"call (?:record|recording) (?:ho rahi|ki ja rahi|hoti)|(?:कॉल|call) (?:रिकॉर्ड|record) (?:हो रही|की जा रही|हो सकती)|"
    r"\b(?:do not|don't|never) share (?:this|the|your) (?:payment )?link", re.IGNORECASE)


# Hardship (owner review, prompt v13): the model still asked "Is there anything else I can help
# with?" and repeated "Please take care..." every turn (v13-hardship2 S03). Dropped in code.
_ANYTHING_ELSE = re.compile(r"\b(?:is there )?anything else (?:i can|i could|you need|you'd like)|"
                            r"और कुछ (?:मदद|जानना|चाहिए|बताइए)|aur kuch (?:madad|chahiye|bataiye)", re.IGNORECASE)
# Owner, 2026-11-04 (v15): close with "आपका दिन शुभ हो" / "have a good day", once per call.
_WARM_CLOSE = re.compile(r"\bhave a (?:good|nice|great) day\b|दिन शुभ हो|din shubh ho", re.IGNORECASE)
# Owner, 2026-11-04 (v15): no "take care" and no "just tell me whatever is comfortable / whatever your
# situation is" after the gentle question ("आप जो comfortable हों, बस वही बताइए।", "आप का ध्यान रखें।").
_UNWANTED = re.compile(r"\btake care\b|(?:अपना|आप ?का) (?:ख्याल|ध्यान) (?:रखिए|रखें|रखियेगा|रखिएगा|रखना)|"
                       r"(?:apna|aap ?ka) (?:khayal|dhyan) (?:rakhiye|rakhein|rakhna)|"
                       r"बस वही बता(?:इए|ईए| दीजिए| दीजिये| दें| दो)|bas wahi bata(?:iye| dijiye| do)|whatever (?:is|you're|you are|feels) comfortable|"
                       r"only (?:what|whatever) (?:is|you're|you are|feels) comfortable", re.IGNORECASE)


# "SMS या WhatsApp?" Live call B004 19:51 asked it four times (C48).
_SMS = re.compile(r"\bsms\b|एसएमएस", re.IGNORECASE)
_WHATSAPP = re.compile(r"whats ?app|व्हाट्सऐप|व्हाट्सएप|व्हाट्सअप", re.IGNORECASE)


def asks_channel(text: str) -> bool:
    """A question that offers both channels."""
    return "?" in text and bool(_SMS.search(text)) and bool(_WHATSAPP.search(text))


def names_channel(text: str) -> bool:
    return bool(_SMS.search(text) or _WHATSAPP.search(text)
                or re.search(r"message|मैसेज|e-?mail|ईमेल|what'?s it", text, re.IGNORECASE))


def unwanted(sentence: str) -> bool:
    return _UNWANTED.search(sentence) is not None


# The hand-off, said 2-3 times per call in v14-special (once even after the goodbye). Once is enough.
_HANDOFF_SAID = re.compile(r"internal team .{0,80}(?:call ?back|callback)|internal team .{0,60}(?:arrange|करेगी)",
                           re.IGNORECASE)


def handoff(sentence: str) -> bool:
    return _HANDOFF_SAID.search(sentence) is not None


_CONTACT_Q = re.compile(r"best (?:number|one) to reach|reach you on|number .{0,40}(?:best|सही)|"
                        r"(?:यह|ये|yeh?) number .{0,40}(?:best|सही|sahi|ठीक)", re.IGNORECASE)


_APOLOGY = re.compile(r"\b(?:i'?m|i am) (?:really |so |very |truly )?sorry to hear|सच में (?:बहुत )?(?:अफ़सोस|अफसोस|दुख)|"
                      r"सच में माफ़?ी चाहती|माफ़?ी चाहती हूँ.{0,40}(?:परेशानी|job|नौकरी)|"
                      r"यह सुनकर (?:मुझे )?(?:सच में )?(?:बहुत )?(?:दुख|अफ़सोस)|sun ?kar (?:mujhe )?(?:bahut )?dukh",
                      re.IGNORECASE)


_SHORT_TERM_Q = re.compile(r"short[- ]term|कुछ समय की (?:परेशानी|बात|situation)|kuch samay ki", re.IGNORECASE)


def once_kind(sentence: str) -> str | None:
    """Phrases said at most once per call (live call 16:03 and v14 runs repeated each of them)."""
    if _APOLOGY.search(sentence):
        return "apology"
    if _SHORT_TERM_Q.search(sentence):
        return "short_term"
    if handoff(sentence):
        return "handoff"
    if contact_question(sentence):
        return "contact"
    if warm_close(sentence):
        return "warm_close"
    return None


# "I can't change it myself": tracked, never dropped (the same sentence often carries the hand-off),
# so the next flow step can say it was already said (special.instruction).
_CANT_CHANGE = re.compile(r"(?:can'?t|cannot|can not|not able to|unable to) (?:\w+ ){0,3}?(?:change|waive|remove|reduce|"
                          r"mark|confirm|make (?:any )?changes?|delay)|"
                          r"खुद .{0,40}(?:नहीं|नही) (?:कर|बदल|करवा|हटा)|(?:बदल|हटा|माफ़|माफ) नहीं (?:सकती|कर सकती)|"
                          r"khud .{0,40}nahi? (?:kar|badal|hata)", re.IGNORECASE)


# G-08, live call B004 (2026-11-04): "five hundred rupees का late payment charge लग गया है" on a loan with
# no charge. Any rupee amount must be one code gave (this borrower's facts or the policy); a claim
# that a late charge was added is checked against the facts.
_MONEY = re.compile(r"₹|\brs\.?(?=\s*\d)|\binr\b|\brupees?\b|\brupa[iy]?e\b|\brupaiye\b|रुपये|रुपया|रूपये", re.IGNORECASE)


def amounts_in(text: str) -> set[int]:
    """Rupee-sized numbers in the text, in words or digits ("बारह हज़ार", "twelve thousand", "Rs 18,000").
    Below 100 are counts or dates; 1900-2100 are years."""
    nums = {int(n) for n in number_tokens(re.sub(r"(?<=\d),(?=\d)", "", text)) if n.isdigit()}
    return {n for n in nums if n >= 100 and not 1900 <= n <= 2100}


def unknown_amount(sentence: str, allowed: set[int]) -> bool:
    """A rupee amount in the sentence that is not one code gave or the borrower said."""
    return bool(_MONEY.search(sentence)) and bool(amounts_in(sentence) - allowed)


_FEE = re.compile(r"late (?:payment )?(?:fee|charge)s?|penalty|पेनल्टी|लेट फ़?ीस|जुर्माना", re.IGNORECASE)
_ADDED = re.compile(r"has been (?:added|applied|charged|levied)|have been (?:added|applied)|(?:is|was) (?:added|applied|charged)|"
                    r"already (?:added|applied|charged)|लग (?:गया|गई|चुका|चुकी)|लगा (?:है|दिया)|लगाया (?:गया|है)|"
                    r"जोड़ (?:दिया|दी)|add (?:हो गया|हो गई|कर दिया|किया)|lag (?:gaya|gayi|chuka)|laga (?:hai|diya)",
                    re.IGNORECASE)
_NOT = re.compile(r"\bno\b|\bnot\b|\bnahi\b|नहीं|\bkoi\b|कोई|\bif\b|अगर|\bagar\b", re.IGNORECASE)


def fee_added_claim(sentence: str) -> bool:
    """Says a late charge has been added (not "no charge" or "if ... then a charge applies")."""
    return bool(_FEE.search(sentence) and _ADDED.search(sentence) and not _NOT.search(sentence))


def cant_change(sentence: str) -> bool:
    return _CANT_CHANGE.search(sentence) is not None


def contact_question(sentence: str) -> bool:
    return "?" in sentence and _CONTACT_Q.search(sentence) is not None


def anything_else(sentence: str) -> bool:
    return _ANYTHING_ELSE.search(sentence) is not None


# An invitation to ask more, in any wording (C51, live call B004 20:13: "यदि कोई और सवाल हो तो बताइए।"
# then the goodbye in the same breath, and the call hung up).
_INVITE = re.compile(r"anything else|any (?:other|more|further) questions?|(?:other|more) questions?\?|"
                     r"(?:कोई|koi) (?:और|aur) (?:सवाल|प्रश्न|बात|sawal|sawaal|baat|question)|"
                     r"(?:और|aur) (?:कुछ|kuch) (?:पूछना|जानना|मदद|चाहिए|बताइए|puchna|poochna|jaanna|madad|chahiye)",
                     re.IGNORECASE)


def invites_more(sentence: str) -> bool:
    return _INVITE.search(sentence) is not None


def warm_close(sentence: str) -> bool:
    return _WARM_CLOSE.search(sentence) is not None


def warm_close_lead(sentence: str) -> str:
    """The words before the goodbye, without punctuation ("Time given for your time")."""
    m = _WARM_CLOSE.search(sentence)
    lead = re.sub(r"[^\w\sऀ-ॿ]", " ", sentence[:m.start()] if m else sentence)
    return re.sub(r"\s*(?:आपका|aapka)\s*$", "", lead, flags=re.IGNORECASE).strip()  # "आपका दिन शुभ हो"


def invented_notice(sentence: str) -> bool:
    return _NOTICE.search(sentence.replace("’", "'")) is not None


def invented_verify_result(sentence: str, *, checked_now: bool, attempts_left: int) -> bool:
    """A mismatch or attempts claim with no failed check this turn, or "last chance" when more
    than one attempt is left."""
    s = sentence.replace("’", "'")
    claims = _MISMATCH.search(s) or _ATTEMPTS.search(s)
    if claims and not checked_now:
        return True
    return bool(_ATTEMPTS.search(s) and _LAST.search(s) and attempts_left != 1)


@dataclass(frozen=True)
class GuardResult:
    text: str
    category: str | None  # None | unverified_disclosure | threat | promise


def safe_line(kind: str, lang: str, script: str = "roman") -> str:
    key = "en" if lang == "en" else script
    return _SAFE[kind][key]


# Reading back the borrower's own request is not a promise: "I've noted that you would like some time and
# a waiver on the late fee", "I understand you're asking for the fee to be waived" were replaced as
# promises (v16-special2 S03, S11), and the safe line then doubled the hand-off. Allowed only when the
# sentence holds no commitment by us; anything else with a waiver word is still replaced (G-04).
_REPORTED_BEFORE = re.compile(
    r"\byou(?:'re|'d|'ve| are| would| have| had)?\s+(?:\S+\s+){0,2}?(?:want\w*|like|asking|asked|ask|"
    r"expect\w*|request\w*|need|hoping|hope|wish\w*)\b(?:\s+\S+){0,12}\s*$|"
    # "...along with your request to delay the EMI or waive the late fee" (v17-hardship-practical S03 run 1)
    r"\byour (?:question|request|concern|query) (?:about|for|regarding|on|to)\b(?:\s+\S+){0,6}\s*$", re.IGNORECASE)
_REPORTED_AFTER_HI = re.compile(r"^\s*(?:\S+\s+){0,4}?(?:चाहती|चाहते|चाहता|chahti|chahte|chahta|माँग|मांग|"
                                r"request)", re.IGNORECASE)
_COMMITMENT = re.compile(
    r"\b(?:i|we)(?:'ll| will| shall| can(?!'t|not)| could| am going to| are going to)\b|"
    r"\b(?:i|we)(?:'ve| have) (?!noted|recorded|written|flagged|made a note|passed)\w+|"
    r"\bwill (?:be )?(?:waived|reduced|removed|reversed|cancelled|dropped)|"
    r"\b(?:has|have|is|are) (?:been )?(?:waived|reduced|removed|reversed)\b|don'?t worry|no need to pay|"
    r"(?:दूंगी|दूँगी|देंगे|देंगी|कर दिया|कर दी|हो जाएगा|हो जाएगी|हो जाएँगे|सकती हूँ|सकती हूं|सकते हैं|चिंता मत)",
    re.IGNORECASE)


def _is_promise(sentence: str) -> bool:
    reported_ok = not _COMMITMENT.search(sentence)
    for m in _PROMISES.finditer(sentence):
        after = sentence[m.end():]
        before = sentence[:m.start()]
        if (_NEGATED_BEFORE.search(before) or _NEGATED_BEFORE_LONG.search(before)
                or _NEGATED_AFTER.search(after) or _NEGATED_AFTER_HI.search(after)):
            continue
        if reported_ok and (_REPORTED_BEFORE.search(before) or _REPORTED_AFTER_HI.search(after)):
            continue
        return True
    return False


def _unexplained_digits(sentence: str, heard: list[str]) -> bool:
    """Digits other than times of day and whole numbers the borrower said (Q-07 read-back)."""
    if "₹" in sentence:
        return True
    said = said_numbers(heard)
    return any(n not in said for n in _numbers(_TIME_OF_DAY.sub(" ", sentence)))


def _numbers(text: str) -> list[str]:
    """Whole numbers in text; digits spoken one at a time ("4 8 2 1", "4-8-2-1") count as one."""
    joined = _SPELLED_DIGITS.sub(lambda m: re.sub(r"\D", "", m.group()), text)
    return re.findall(r"\d+", joined)


def _loan_term(sentence: str, heard: list[str]) -> bool:
    """A month name the borrower said (a date-of-birth read-back) is not a loan term."""
    low_heard = " ".join(heard).lower()
    return any(not (m.group().lower() in _MONTH_NAMES and _has_word(low_heard, m.group().lower()))
               for m in _LOAN_TERMS.finditer(sentence))


def _has_word(text: str, word: str) -> bool:
    return re.search(_L + re.escape(word) + _R, text) is not None


# C59 (v17-holdout-stress: all 6 early-disclosure replacements were the agent refusing): a sentence that only
# refuses to share ("मैं loan या account की details किसी और को share नहीं कर सकती") may name general words
# (loan, EMI, payment, amount, details). It is still replaced if it carries any fact: a digit, a money word,
# a month the borrower did not say, or any of this borrower's protected phrases (amount, dates, loan type).
_REFUSAL = re.compile(
    r"\b(?:can'?t|cannot|won'?t|will not|not able to|unable to|not allowed to)\s+(?:\w+\s+){0,2}?"
    r"(?:share|tell|discuss|disclose|give|reveal|confirm)\b|"
    r"(?:share|बता|बताना|discuss|साझा|disclose)\S*\s+(?:\S+\s+){0,2}?नहीं\s+(?:कर\s+)?"
    r"(?:सकती|सकता|सकते|पाऊँगी|पाऊंगी|पाऊँगा|पाऊंगा)|"
    r"(?:share|bata|bataa|discuss)\w*\s+(?:\w+\s+){0,2}?nahi?n?\s+(?:kar\s+)?(?:sakti|sakta|sakte|paungi|paunga)|"
    r"नहीं\s+(?:share\s+कर|बता|बताना|साझा\s+कर)\s*(?:सकती|सकता|सकते|पाऊँगी|पाऊंगी)|"
    r"nahi?n?\s+(?:share\s+kar|bata)\s+(?:sakti|sakta|sakte|paungi)",
    re.IGNORECASE)
_MONEY_WORDS = _words(r"rupees?", r"rupaye", r"rupaya", r"rs\.?", r"inr", r"lakhs?", r"crores?", r"thousand",
                      r"hundred", r"haz+aa?r", "रुपये", "रुपया", "हज़ार", "हजार", "लाख", "करोड़", "सौ")


def _month_said_not_heard(sentence: str, heard: list[str]) -> bool:
    low_heard = " ".join(heard).lower()
    return any(m.group().lower() in _MONTH_NAMES and not _has_word(low_heard, m.group().lower())
               for m in _LOAN_TERMS.finditer(sentence))


def check(sentence: str, *, verified: bool, protected: list[str], lang: str,
          script: str = "roman", heard: list[str] = ()) -> GuardResult:
    """`heard` is the borrower's utterances so far in this call."""
    sentence_n = sentence.replace("’", "'")
    if _THREATS.search(sentence_n):
        return GuardResult(safe_line("change", lang, script), "threat")
    if _is_promise(sentence_n):
        return GuardResult(safe_line("change", lang, script), "promise")
    if not verified:
        low = sentence_n.lower()
        fact = (_unexplained_digits(sentence_n, heard) or "₹" in sentence_n or _MONEY_WORDS.search(sentence_n)
                or _month_said_not_heard(sentence_n, heard) or any(p.lower() in low for p in protected if p))
        if fact or (_loan_term(sentence_n, heard) and not _REFUSAL.search(sentence_n)):
            return GuardResult(safe_line("unverified_disclosure", lang, script),
                               "unverified_disclosure")
    return GuardResult(sentence, None)
