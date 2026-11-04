"""Last step before TTS: make a guarded sentence safe to speak, and count every fix (plan step 2, 2026-11-04).

Deterministic, no model, no dictionary of open vocabulary. Each fix is returned as a hit so evals can
see how often the model slipped; a hit is a speaking-style signal, not a silent repair (D-21).
  a. Drop what must never be spoken: URLs, emoji, markdown.
  b. Digits the model still wrote -> words in the sentence's own script (D-21 backstop).
  c. Script backstop: in a sentence that has Devanagari, romanised Hindi words from a closed list go to
     Devanagari. Both TTS engines misread this mix (results/tts_script_check, c024, c028).
  d. Wrong language: an English call answered in Devanagari is flagged, not rewritten (that is a
     translation, which belongs to the prompt).
"""
from __future__ import annotations

import re

from emi_agent.speech_format import (_HI_DEVA, _HI_ROMAN, _year_words, clean_for_tts, digits_to_words,
                                     number_to_words)

_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w-]+\.(?:example|com|in|ai)(?:/\S*)?", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_DEVANAGARI_WORD = re.compile(r"[ऀ-ॿ]+")
_DIGIT_RUN = re.compile(r"₹\s*\d[\d,]*|\d(?:[ -]?\d)*")
_MONTH = re.compile(r"january|february|march|april|may|june|july|august|september|october|november"
                    r"|december|जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर",
                    re.I)

# Romanised Hindi words that are never English words. Ambiguous ones stay out on purpose:
# main (मैं / main), to, do (दो / do), hi, band, mat, teen (तीन / teen), par is kept (rare in English).
_ROMAN_TO_DEVA = {
    "hai": "है", "hain": "हैं", "nahi": "नहीं", "nahin": "नहीं", "aap": "आप", "aapka": "आपका",
    "aapki": "आपकी", "aapke": "आपके", "aapko": "आपको", "mujhe": "मुझे", "mera": "मेरा", "meri": "मेरी",
    "mere": "मेरे", "kya": "क्या", "kab": "कब", "kaise": "कैसे", "kyun": "क्यों", "tak": "तक", "ko": "को",
    "ki": "की", "ke": "के", "ka": "का", "se": "से", "par": "पर", "mein": "में", "hoon": "हूँ", "hun": "हूँ",
    "raha": "रहा", "rahi": "रही", "rahe": "रहे", "tha": "था", "thi": "थी", "theek": "ठीक", "thik": "ठीक",
    "ji": "जी", "haan": "हाँ", "accha": "अच्छा", "achha": "अच्छा", "abhi": "अभी", "kal": "कल", "aaj": "आज",
    "parson": "परसों", "karenge": "करेंगे", "karungi": "करूँगी", "kar": "कर", "dijiye": "दीजिए",
    "sakte": "सकते", "sakti": "सकती", "hoga": "होगा", "hogi": "होगी", "bhi": "भी", "toh": "तो", "aur": "और",
    "ya": "या", "lekin": "लेकिन", "dhanyavaad": "धन्यवाद", "rupaye": "रुपये", "hazaar": "हज़ार",
    "sau": "सौ", "lakh": "लाख",
}
_ROMAN_TO_DEVA.update({r: d for r, d in zip(_HI_ROMAN, _HI_DEVA) if r not in {"do", "teen", "das"}})
_ROMAN_WORD = re.compile(r"(?<![\w])[A-Za-z]+(?![\w])")


def devanagari_share(text: str) -> float:
    words = text.split()
    return len(_DEVANAGARI_WORD.findall(text)) / len(words) if words else 0.0


def _number_words(raw: str, deva: bool, month: bool, lang: str, script: str, numbers: str) -> str:
    if numbers == "hindi" and lang != "en":
        nlang, nscript = ("hi", "devanagari") if deva else ("hi", script)
    else:
        nlang, nscript = "en", script
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("₹"):
        return number_to_words(int(digits), nlang, nscript) + (" रुपये" if nlang == "hi" and deva else " rupees")
    if month and len(digits) == 4 and digits[:2] in ("19", "20"):
        return _year_words(int(digits), nlang, nscript)  # a date-of-birth read-back
    if len(digits) >= 4 or re.search(r"[ -]", raw):
        return digits_to_words(digits, nlang, nscript)  # codes and last-four read-backs: digit by digit
    return number_to_words(int(digits), nlang, nscript)


def prepare(sentence: str, lang: str, script: str, numbers: str = "english",
            fact_phrases: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """The text to send to TTS and the hits ("url", "emoji", "digits", "number_style", "script_mix",
    "wrong_language"). `fact_phrases` maps this borrower's facts in Hindi number words to the
    phrase the tools gave (number style english); the model tends to translate them back."""
    hits = []
    if fact_phrases:
        before = sentence
        for hindi, spoken in fact_phrases.items():
            for form in {hindi, hindi.replace("़", "")}:  # "हज़ार" and "हजार"
                sentence = sentence.replace(form, spoken)
        if sentence != before:
            hits.append("number_style")
    text = _URL.sub("", sentence)
    if text != sentence:
        hits.append("url")
    no_emoji = _EMOJI.sub("", text)
    if no_emoji != text:
        hits.append("emoji")
    text = clean_for_tts(no_emoji)
    deva = _DEVANAGARI.search(text) is not None
    if _DIGIT_RUN.search(text):
        month = _MONTH.search(text) is not None
        text = _DIGIT_RUN.sub(lambda m: _number_words(m.group(), deva, month, lang, script, numbers), text)
        hits.append("digits")
    if deva and script == "devanagari":
        fixed = _ROMAN_WORD.sub(lambda m: _ROMAN_TO_DEVA.get(m.group().lower(), m.group()), text)
        if fixed != text:
            text = fixed
            hits.append("script_mix")
    if lang == "en" and devanagari_share(text) >= 0.33:
        hits.append("wrong_language")
    return " ".join(text.split()), hits
