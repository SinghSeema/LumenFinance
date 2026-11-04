"""Per-sentence output guard before TTS (LLD section 10). A backstop, not the main defence."""
from __future__ import annotations

import re
from dataclasses import dataclass

from emi_agent.heard import said_numbers

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
    r"discounts?|fees?|reversals?|restructuring|changes?),?\s+){0,8}$", re.IGNORECASE)
# Hindi puts the verb last: "छूट या बदलाव का वादा नहीं कर सकती". Up to 6 words, same clause only
# (a word carrying , . ; । ends the search), and नहीं must be followed by a refusal verb, so
# "छूट दे दूंगी, चिंता की कोई बात नहीं" stays a promise (eval S03, 2026-11-04).
_NEGATED_AFTER_HI = re.compile(
    r"^\s*(?:[^\s,.;।!?]+\s+){0,6}?(?:नहीं|nahi|nahin)\s+"
    r"(?:कर|दे|दी|मिल|सकती|सकते|सकता|पाऊँगी|पाऊंगी|पाएंगे|kar|de|mil|sakti|sakte)"
    r"(?=[\s,.;।!?]|$)",  # whole word: "होगी" is not a refusal verb
    re.IGNORECASE)

_SAFE = {
    "unverified_disclosure": {
        "en": "Before I share any details, I just need to confirm who I'm speaking with.",
        "roman": "Koi bhi jaankari dene se pehle, mujhe bas aapki pehchaan confirm karni hogi.",
        "devanagari": "कोई भी जानकारी देने से पहले, मुझे बस आपकी पहचान कन्फ़र्म करनी होगी।",
    },
    "change": {
        "en": "I'm not able to make changes to your account myself, but I can connect you "
              "with a colleague who can help.",
        "roman": "Main khud account mein koi badlav nahi kar sakti, lekin aapko ek colleague "
                 "se connect kar sakti hoon jo madad karenge.",
        "devanagari": "मैं खुद अकाउंट में कोई बदलाव नहीं कर सकती, लेकिन आपको एक सहकर्मी से "
                      "जोड़ सकती हूँ जो मदद करेंगे।",
    },
}


# Verification results the model may only state after a real check (G-08). Live call 2026-11-04:
# code noted "day still missing, never say it does not match"; the model said "यह जानकारी भी मेल
# नहीं खा रही। आपके पास एक आखिरी मौका है।" with no check run and two attempts left.
_MISMATCH = _words(r"(?:does|did|do|is|was)\s*(?:n't|not)\s+match\w*", r"not\s+match\w*", r"no match",
                   r"match nahi\w*", r"mel nahi\w*", "मेल नहीं", "मैच नहीं", "मिलान नहीं")
_ATTEMPTS = _words(r"(?:one more|another|ek aur|last|final|aakhri|akhri) (?:chance|attempt|try|mauka)",
                   r"(?:attempts?|chances?|tries) left", "एक और मौका", "आखिरी मौका", "अंतिम मौका",
                   "मौका बाकी", "मौके बाकी", "कोशिश बाकी")
_LAST = _words(r"last", r"final", r"aakhri", r"akhri", "आखिरी", "अंतिम")


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


def _is_promise(sentence: str) -> bool:
    for m in _PROMISES.finditer(sentence):
        after = sentence[m.end():]
        before = sentence[:m.start()]
        if (_NEGATED_BEFORE.search(before) or _NEGATED_BEFORE_LONG.search(before)
                or _NEGATED_AFTER.search(after) or _NEGATED_AFTER_HI.search(after)):
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
        if (_unexplained_digits(sentence_n, heard) or _loan_term(sentence_n, heard)
                or any(p.lower() in low for p in protected if p)):
            return GuardResult(safe_line("unverified_disclosure", lang, script),
                               "unverified_disclosure")
    return GuardResult(sentence, None)
