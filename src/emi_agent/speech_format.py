"""Spoken forms of amounts, dates and digits. Tools hand the model these strings, never raw numbers."""
from __future__ import annotations

from datetime import date
import re
from decimal import ROUND_HALF_UP, Decimal

_EN_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_EN_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

# Hindi numbers 0-99 are irregular, so they are a lookup table.
_HI_ROMAN = (
    "shunya ek do teen chaar paanch chhah saat aath nau das gyarah barah terah chaudah "
    "pandrah solah satrah atharah unnees bees ikkees baees teis chaubees pachchees "
    "chhabbees sattaees atthaees untees tees ikattees battees taintees chauntees paintees "
    "chhattees saintees adtees untaalees chaalees iktaalees bayaalees taintaalees "
    "chavaalees paintaalees chhiyaalees saintaalees adtaalees unchaas pachaas ikyaavan "
    "baavan tirpan chauvan pachpan chhappan sattaavan atthaavan unsath saath iksath "
    "baasath tirsath chausath painsath chhiyaasath sadsath adsath unhattar sattar "
    "ikhattar bahattar tihattar chauhattar pachhattar chhihattar satattar athhattar "
    "unaasi assi ikyaasi bayaasi tiraasi chauraasi pachaasi chhiyaasi sattaasi atthaasi "
    "navaasi nabbe ikyaanve baanve tiraanve chauraanve pachaanve chhiyaanve sattaanve "
    "atthaanve ninyaanve"
).split()
_HI_DEVA = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह "
    "उन्नीस बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस "
    "तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस "
    "पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन "
    "सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर "
    "इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी "
    "बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे "
    "चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()
assert len(_HI_ROMAN) == 100 and len(_HI_DEVA) == 100

_HI_UNITS = {
    "roman": {"hundred": "sau", "thousand": "hazaar", "lakh": "lakh", "crore": "crore",
              "rupee": "rupaya", "rupees": "rupaye"},
    "devanagari": {"hundred": "सौ", "thousand": "हज़ार", "lakh": "लाख", "crore": "करोड़",
                   "rupee": "रुपया", "rupees": "रुपये"},
}

_MONTHS_EN = ("January February March April May June July August September October "
              "November December").split()
_MONTHS_DEVA = "जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split()

_ORDINAL_EN = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
    8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth",
    14: "fourteenth", 15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
    19: "nineteenth", 20: "twentieth", 30: "thirtieth",
}

_RELATIVE = {
    "en": ("today", "tomorrow", "the day after tomorrow"),
    "roman": ("aaj", "kal", "parson"),
    "devanagari": ("आज", "कल", "परसों"),
}


# Number style (owner, 2026-11-04): Hindi and Hinglish calls say numbers in English by default, inside
# the Hindi sentence ("hi-en"), because Hindi number words are hard for many borrowers to follow. If the
# borrower says numbers in Hindi words, the call switches to Hindi numbers ("hi").
HINDI_SENTENCE_ENGLISH_NUMBERS = "hi-en"


def number_lang(reply_language: str, numbers: str) -> str:
    """The `lang` argument for this module: 'en', 'hi' (Hindi numbers) or 'hi-en'."""
    if reply_language == "en":
        return "en"
    return "hi" if numbers == "hindi" else HINDI_SENTENCE_ENGLISH_NUMBERS


def _hindi_script(lang: str, script: str) -> str | None:
    """None means English number words ('en', 'hi-en', and legacy 'hinglish')."""
    if lang != "hi":
        return None
    if script not in _HI_UNITS:
        raise ValueError(f"Unknown script: {script!r}")
    return script


def _en_below_1000(n: int) -> str:
    words = []
    if n >= 100:
        words += [_EN_ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        words.append(_EN_TENS[n // 10] + ("-" + _EN_ONES[n % 10] if n % 10 else ""))
    elif n or not words:
        words.append(_EN_ONES[n])
    return " ".join(words)


def _hi_below_1000(n: int, table: list[str], units: dict) -> str:
    words = []
    if n >= 100:
        words += [table[n // 100], units["hundred"]]
        n %= 100
    if n or not words:
        words.append(table[n])
    return " ".join(words)


def number_to_words(n: int, lang: str = "en", script: str = "roman") -> str:
    """Indian grouping: crore, lakh, thousand, hundred."""
    if n < 0:
        raise ValueError("negative numbers are not spoken")
    hs = _hindi_script(lang, script)
    if hs:
        table = _HI_ROMAN if hs == "roman" else _HI_DEVA
        units = _HI_UNITS[hs]
        below = lambda x: _hi_below_1000(x, table, units)  # noqa: E731
        names = (units["crore"], units["lakh"], units["thousand"])
    else:
        below = _en_below_1000
        names = ("crore", "lakh", "thousand")
    if n == 0:
        return below(0)
    parts = []
    crore, rest = divmod(n, 10_000_000)
    lakh, rest = divmod(rest, 100_000)
    thousand, rest = divmod(rest, 1000)
    if crore:
        parts += [number_to_words(crore, lang, script), names[0]]
    for value, name in ((lakh, names[1]), (thousand, names[2])):
        if value:
            parts += [below(value), name]
    if rest:
        parts.append(below(rest))
    return " ".join(parts)


def amount_to_words(amount: int | float | Decimal, lang: str = "en", script: str = "roman") -> str:
    """Rounds to the nearest rupee (half up). 12500 -> 'twelve thousand five hundred rupees'."""
    rupees = int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    words = number_to_words(rupees, lang, script)
    hs = _hindi_script(lang, script)
    if hs:
        unit = _HI_UNITS[hs]["rupee" if rupees == 1 else "rupees"]
    else:
        unit = "rupee" if rupees == 1 else "rupees"
    return f"{words} {unit}"


def _en_ordinal(n: int) -> str:
    if n in _ORDINAL_EN:
        return _ORDINAL_EN[n]
    tens, ones = divmod(n, 10)
    return f"{_EN_TENS[tens]}-{_ORDINAL_EN[ones]}"


def _year_words(year: int, lang: str, script: str) -> str:
    if _hindi_script(lang, script):
        return number_to_words(year, lang, script)
    hi, lo = divmod(year, 100)
    return f"{_en_below_1000(hi)} {_en_below_1000(lo)}" if lo else f"{_en_below_1000(hi)} hundred"


def date_to_words(d: date, today: date, lang: str = "en", script: str = "roman") -> str:
    """'the fifth of November'; 'tomorrow' etc. for the next two days; year only if not this year."""
    hs = _hindi_script(lang, script)
    hi_en = lang == HINDI_SENTENCE_ENGLISH_NUMBERS
    key = hs or (script if hi_en else "en")  # 'kal' / 'कल' stays Hindi inside a Hindi sentence
    delta = (d - today).days
    if 0 <= delta <= 2:
        return _RELATIVE[key][delta]
    if hi_en:
        text = f"{_en_ordinal(d.day)} {_MONTHS_EN[d.month - 1]}"  # "twenty-fifth November"
    elif hs:
        months = _MONTHS_EN if hs == "roman" else _MONTHS_DEVA
        text = f"{number_to_words(d.day, lang, script)} {months[d.month - 1]}"
    else:
        text = f"the {_en_ordinal(d.day)} of {_MONTHS_EN[d.month - 1]}"
    if d.year != today.year:
        text += ("," if not (hs or hi_en) else "") + f" {_year_words(d.year, lang, script)}"
    return text


def days_to_words(days: int, lang: str = "en", script: str = "roman") -> str:
    n = number_to_words(days, lang, script)
    hs = _hindi_script(lang, script)
    if hs == "roman":
        return f"{n} din"
    if hs == "devanagari":
        return f"{n} दिन"
    return f"{n} day" if days == 1 else f"{n} days"


def digits_to_words(s: str, lang: str = "en", script: str = "roman") -> str:
    """'4821' -> 'four, eight, two, one'. Commas give the TTS a pause between digits."""
    digits = [c for c in s if c.isdigit()]
    if not digits:
        raise ValueError(f"no digits in {s!r}")
    hs = _hindi_script(lang, script)
    table = _EN_ONES if not hs else (_HI_ROMAN if hs == "roman" else _HI_DEVA)
    return ", ".join(table[int(c)] for c in digits)


_MARKUP = re.compile(r"[*`#~]+|^\s*[-•]\s+", re.MULTILINE)
_INVISIBLE = re.compile(r"[​‌‍⁠﻿]")


def clean_for_tts(text: str) -> str:
    """Drop markdown and zero-width characters the model sometimes emits, so TTS never reads them."""
    text = _INVISIBLE.sub("", text)
    text = _MARKUP.sub(" ", text)
    return " ".join(text.split())
