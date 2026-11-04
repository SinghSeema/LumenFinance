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
    if words and len(_DEVANAGARI_WORD.findall(text)) / len(words) >= 1 / 3:
        return "hi"
    words = _WORD.findall(text.lower())
    markers = sum(w in _HINDI_MARKERS for w in words)
    if stt_hint == "hi" and words:
        return "hinglish"
    if markers >= 2 or (words and markers / len(words) >= 0.3):
        return "hinglish"
    return "en"


def _family(lang: str) -> str:
    return "en" if lang == "en" else "hi"


FULL_SENTENCE_WORDS = 5  # a turn this long in the other language switches at once


class LanguageTracker:
    """Within Hindi/Hinglish a switch is immediate. Between English and Hindi, a full sentence
    (5+ words) switches at once; a short turn ("Okay.", "Thank you") needs two in a row, so one
    stray word cannot flip the call."""

    def __init__(self, initial: str):
        self.current = initial
        self.used = [initial]
        self._pending = 0

    def update(self, detected: str, words: int = 0) -> bool:
        if _family(detected) == _family(self.current):
            self._pending = 0
            return self._set(detected)
        self._pending += 1
        if self._pending >= 2 or words >= FULL_SENTENCE_WORDS:
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


def says_hindi_numbers(text: str) -> bool:
    return any(t in _HINDI_NUMBER_WORDS for t in _TOKEN.findall(text.lower()))


# Numbers, digits and month names carry no language signal ("July", "19 94" are the same in a Hindi
# or an English answer). Live call 2026-11-04: STT turned "बाइस July 19 94" into "Buys, July 19 94."
from emi_agent.speech_format import _EN_ONES, _EN_TENS, _MONTHS_DEVA, _MONTHS_EN  # noqa: E402

_NUMBER_OR_MONTH = ({w.lower() for w in _MONTHS_EN} | {w[:3].lower() for w in _MONTHS_EN} | set(_MONTHS_DEVA)
                    | set(_EN_ONES) | {w for w in _EN_TENS if w != "_"} | set(_HI_DEVA) | set(_HI_ROMAN)
                    | {"hundred", "thousand", "lakh", "sau", "सौ", "हज़ार", "हजार", "double", "triple"})


from emi_agent.heard import _loose_deva  # noqa: E402

_NUMBER_OR_MONTH |= {_loose_deva(w) for w in _NUMBER_OR_MONTH}


def language_words(text: str) -> str:
    """The text without digits, number words and month names (Devanagari spelling-tolerant)."""
    def is_number(w: str) -> bool:
        w = w.lower().strip(".,?!।")
        return bool(re.fullmatch(r"[\d.,:/-]*", w)) or w in _NUMBER_OR_MONTH or _loose_deva(w) in _NUMBER_OR_MONTH
    return " ".join(w for w in text.split() if not is_number(w))
