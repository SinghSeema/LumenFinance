"""Reply-language policy: follow the borrower's latest language (LLD section 8)."""
from __future__ import annotations

import re

_HINDI_MARKERS = set("""
haan han ji nahi nahin hai hain hoon hun main mera meri mere mujhe aap aapka aapki kya kyun
kal aaj parso tak kar karo karunga karungi dunga dungi denge raha rahi rahe tha thi theek thik
acha accha achha bhai bolo bol baat abhi paise paisa kab kaise matlab toh bhi wala wali
hoga hogi sakta sakti chahiye mat band samajh pata
""".split())

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-z]+")


_DEVANAGARI_WORD = re.compile(r"[ऀ-ॿ]+")


def detect(text: str, stt_hint: str | None = None) -> str:
    """'hi' for Devanagari, 'hinglish' for romanised Hindi (with or without English), else 'en'.
    Devanagari is judged by words, not letters: "हाँ मैं pay कर दूँगा twenty fifth को" is Hindi,
    though English loanwords hold most of its letters (was 'en' until 2026-11-04)."""
    words = text.split()
    deva = len(_DEVANAGARI_WORD.findall(text))
    # Hinglish carries its grammar in Devanagari and its nouns in English: "Actually, मेरा अभी job चला
    # गया है and मैं अभी pay नहीं कर सकती हूं" is 11 of 35 words (live call 2026-11-04 16:03).
    if words and (deva / len(words) >= 1 / 3 or (deva >= 3 and deva / len(words) >= 1 / 5)):
        return "hi"
    words = _WORD.findall(text.lower())
    markers = sum(w in _HINDI_MARKERS for w in words)
    # Deepgram's "hi" tag alone is not enough: it tagged every turn of the live calls "hi", English
    # ones too ("Can you please tell me the EMI amount in English?", confidence 1.00, call B004
    # 19:51; "Just wanted to share that I have lost my job" flipped the English call B007 16:03 to
    # Hinglish). In multi mode Hindi comes back in Devanagari, so Roman text with no Hindi word is English.
    if stt_hint == "hi" and markers >= 1:
        return "hinglish"
    if markers >= 2 or (words and markers / len(words) >= 0.3):
        return "hinglish"
    return "en"


def _family(lang: str) -> str:
    return "en" if lang == "en" else "hi"


FULL_SENTENCE_WORDS = 5  # a turn this long in the other language switches at once
DEVANAGARI_SWITCH_WORDS = 3  # Deepgram writes Devanagari only for Hindi it heard: a clear signal


class LanguageTracker:
    """Within Hindi/Hinglish a switch is immediate. Between English and Hindi, a full sentence
    (5+ words, or 3+ words in Devanagari) switches at once; a short turn needs two in a row, so one
    stray word cannot flip the call."""

    def __init__(self, initial: str):
        self.current = initial
        self.used = [initial]
        self._pending = 0
        self.pinned = False

    def pin(self, lang: str) -> bool:
        """C80: the borrower asked for this language by name. It holds until they ask again: their
        own words in the other language ("haan theek hai" after "can you speak in English?") no
        longer flip it. A request for the family already spoken changes nothing."""
        self.pinned, self._pending = True, 0
        if _family(lang) == _family(self.current):
            return False
        return self._set(lang)

    def update(self, detected: str, words: int = 0) -> bool:
        if self.pinned and _family(detected) != _family(self.current):
            return False
        if _family(detected) == _family(self.current):
            self._pending = 0
            return self._set(detected)
        self._pending += 1
        if (self._pending >= 2 or words >= FULL_SENTENCE_WORDS
                or (detected == "hi" and words >= DEVANAGARI_SWITCH_WORDS)):
            self._pending = 0
            return self._set(detected)
        return False

    def _set(self, lang: str) -> bool:
        if lang == self.current:
            return False
        self.current = lang
        if lang not in self.used:
            self.used.append(lang)
        return True


# Hindi number words a borrower might say. Left out because they are everyday words too:
# एक/ek, दो/do ("give"), teen, das, saath ("with"), sau.
from emi_agent.speech_format import _HI_DEVA, _HI_ROMAN  # noqa: E402

_HINDI_NUMBER_WORDS = ({w for w in _HI_DEVA if w not in {"एक", "दो"}}
                       | {w for w in _HI_ROMAN if w not in {"ek", "do", "teen", "das", "saath", "sau"}}
                       | {"हज़ार", "हजार", "hazaar", "hazar"})
_TOKEN = re.compile(r"[a-zऀ-ॿ]+")


# An explicit request for a number style (owner, 2026-11-04: "if a Hindi caller asks to repeat the
# number in English"). "English mein bataiye", "अंग्रेज़ी में बोलिए", "in English please"; same for Hindi.
_IN = r"\s*(?:me|mein|mai|main|में|मे|मैं)(?![a-z])"
_ASK_NUMBERS = {
    "english": re.compile(r"(?:(?<![a-z])english|इंग्लिश|अंग्रेज़ी|अंग्रेजी|angrezi|angreji)" + _IN + r"|\bin english\b",
                          re.IGNORECASE),
    "hindi": re.compile(r"(?:(?<![a-z])hindi|हिंदी|हिन्दी)" + _IN + r"|\bin hindi\b", re.IGNORECASE),
}


_NEGATION = re.compile(r"नहीं|नही|मत|\b(?:nahi|nahin|nai|mat|not|don't|dont|no|never)\b", re.IGNORECASE)


def asks_numbers_in(text: str) -> str | None:
    """'english' or 'hindi' when the borrower asks for that language by name, else None."""
    if _NEGATION.search(text):  # "मैं English में नहीं समझती" asks for the opposite: leave it to the model
        return None
    hits = [style for style, pattern in _ASK_NUMBERS.items() if pattern.search(text)]
    return hits[0] if len(hits) == 1 else None


# C80 (live call B002, 2026-11-12): "क्या आप English में बात कर सकते हैं?" is itself a Hindi sentence,
# and "Can you speak in English?" is too short to switch alone, so the call stayed in Hindi. A request
# for the reply language by name now switches at once. Only with a speaking verb: "amount English mein
# bataiye" still asks for the numbers only (C44).
_LANG_NAMES = {
    "en": re.compile(r"(?<![a-z])(?:english|angrezi|angreji)(?![a-z])|इंग्लिश|अंग्रेज़ी|अंग्रेजी", re.IGNORECASE),
    "hi": re.compile(r"(?<![a-z])hindi(?![a-z])|हिंदी|हिन्दी", re.IGNORECASE),
}
_TALK = re.compile(r"\b(?:speak\w*|talk\w*|conversation|converse|chat|communicate|continue|baat|bol\w*)\b"
                   r"|बात|बोल", re.IGNORECASE)
_NUMBER_TOPIC = re.compile(r"\b(?:amount|number|numbers|rupees?|rupaye|rupaiye|digits?|figures?|dates?|tareekh|tarikh"
                           r"|paise|paisa)\b|रकम|राशि|अमाउंट|नंबर|रुपय|रुपए|रुपये|तारीख|पैसे", re.IGNORECASE)
# "I don't understand Hindi", "Hindi samajh nahi aati", "मुझे English नहीं आती": asks for the other one.
_CANT = re.compile(r"\b(?:don'?t|do not|can'?t|cannot)\s+(?:speak|understand|follow|know)\b"
                   r"|\bsamajh\w*\s+(?:nahi|nahin|nai)\b|\b(?:nahi|nahin|nai)\s+(?:samajh|aat|bol|kar\s+(?:sak|pa))\w*"
                   r"|(?:नहीं|नही)\s*(?:आत|समझ|बोल|कर\s*(?:सक|पा))|समझ\S*\s+(?:नहीं|नही)\b|(?:नहीं|नही)\s*$"
                   # "Hindi mein money ki baat thodi difficult lagti hai" (text run S32, C80)
                   r"|\b(?:difficult|mushkil|hard)\b|मुश्किल|कठिन", re.IGNORECASE)
_PLEASE_ONLY = re.compile(r"\W*(?:in\s+)?\S+\s*(?:please|plz|प्लीज़|प्लीज)\W*", re.IGNORECASE)


def asks_language(text: str) -> str | None:
    """'en' or 'hi' when the borrower asks for the reply language by name, else None. Each sentence is
    read alone: "Kya aap English mein baat kar sakte hain? Hindi mein money ki baat difficult lagti hai"
    names both languages, but both sentences ask for English. Sentences that disagree: None."""
    asked = {a for part in re.split(r"[?.!।,;]+", text) if (a := _asks_one(part))}
    return asked.pop() if len(asked) == 1 else None


def _asks_one(text: str) -> str | None:
    names = [lang for lang, p in _LANG_NAMES.items() if p.search(text)]
    if len(names) != 1 or _NUMBER_TOPIC.search(text):
        return None  # "Hindi nahi, English mein" or "amount in English": left as before
    named = names[0]
    if _CANT.search(text):
        return "hi" if named == "en" else "en"
    if _NEGATION.search(text):
        return None
    if _TALK.search(text) or _PLEASE_ONLY.fullmatch(text.strip()):
        return named
    return None


def says_hindi_numbers(text: str) -> bool:
    return any(t in _HINDI_NUMBER_WORDS for t in _TOKEN.findall(text.lower()))


# Numbers, digits and month names carry no language signal ("July", "19 94" are the same in a Hindi
# or an English answer). Live call 2026-11-04: STT turned "बाइस July 19 94" into "Buys, July 19 94."
from emi_agent.speech_format import _EN_ONES, _EN_TENS, _MONTHS_DEVA, _MONTHS_EN  # noqa: E402

_NUMBER_OR_MONTH = ({w.lower() for w in _MONTHS_EN} | {w[:3].lower() for w in _MONTHS_EN} | set(_MONTHS_DEVA)
                    | set(_EN_ONES) | {w for w in _EN_TENS if w != "_"} | set(_HI_DEVA) | set(_HI_ROMAN)
                    | {"hundred", "thousand", "lakh", "sau", "सौ", "हज़ार", "हजार", "double", "triple"}
                    # Said the same way in a Hindi or an English call: "Yes." then "After 4 PM?" flipped a
                    # Hindi call to English (replay of the 16:03 call).
                    | {"yes", "no", "ok", "okay", "thank", "thanks", "you", "hello", "hi", "sorry", "pm", "am",
                       "after", "before", "bye",
                       # Names said the same in both languages: "WhatsApp." alone flipped a Hindi call (replay, C46)
                       "whatsapp", "sms", "upi", "emi"})


from emi_agent.heard import _loose_deva  # noqa: E402

_NUMBER_OR_MONTH |= {_loose_deva(w) for w in _NUMBER_OR_MONTH}


def language_words(text: str) -> str:
    """The text without digits, number words and month names (Devanagari spelling-tolerant)."""
    def is_number(w: str) -> bool:
        w = w.lower().strip(".,?!।")
        return bool(re.fullmatch(r"[\d.,:/-]*", w)) or w in _NUMBER_OR_MONTH or _loose_deva(w) in _NUMBER_OR_MONTH
    return " ".join(w for w in text.split() if not is_number(w))
