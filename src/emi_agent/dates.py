"""Resolve spoken date phrases against a fixed `today`. Never guesses: unclear input is ambiguous (G-08)."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

# A day that is this far in the past this year is taken to mean next year ("5 Jan" said in November).
_ROLL_TO_NEXT_YEAR_DAYS = 60

_AMBIGUOUS = ["salary", "tankhwah", "tankha", "soon", "jaldi", "jald hi", "weekend", "sometime",
              "some time", "kabhi", "तनख्वाह", "सैलरी", "जल्दी", "जल्द ही"]
# Mixed script as the borrower simulator and Deepgram write it: "महीने के end तक" (v10 eval S13).
_MONTH_END = ["month end", "end of the month", "end of month", "mahine ke end", "mahine ke aakhir",
              "महीने के अंत", "महीने के end", "महीने के एंड", "महीने के आखिर"]
_LAST_DAY = ["last day of the month", "last day of this month", "month's last day",
             "months last day", "mahine ki aakhri tarikh", "mahine ka aakhri din",
             "महीने की आखिरी तारीख"]
_NEXT_WEEK = ["next week", "agle hafte", "agle week", "अगले हफ्ते"]
_NEXT_MONTH = ["next month", "agle mahine", "अगले महीने"]
_AFTER_WEEKS = re.compile(r"(?:after|in)\s+(?:a|one|1|2|two)\s+weeks?|(?:ek|do|1|2|एक|दो)\s*(?:hafte|hafton|हफ्ते|हफ़्ते|हफ्तों)\s*"
                          r"(?:baad|बाद|me|mein|में)|(?:a|one|1)\s+week\s+(?:later|baad|बाद)", re.IGNORECASE)

_RELATIVE = [  # longest phrases first
    (["day after tomorrow", "parso", "parson", "parsoon", "परसों", "परसो"], 2),
    (["tomorrow", "kal", "कल"], 1),
    (["today", "aaj", "आज"], 0),
]

_WEEKDAYS = {
    0: ["monday", "somvar", "somwar", "सोमवार"],
    1: ["tuesday", "mangalvar", "mangalwar", "मंगलवार"],
    2: ["wednesday", "budhvar", "budhwar", "बुधवार"],
    3: ["thursday", "guruvar", "guruwar", "brihaspativar", "गुरुवार"],
    4: ["friday", "shukravar", "shukrawar", "शुक्रवार"],
    5: ["saturday", "shanivar", "shaniwar", "शनिवार"],
    6: ["sunday", "ravivar", "raviwar", "itvaar", "रविवार"],
}
_NEXT_WORDS = r"(?:next|agle|agla|अगले)"

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4,
    "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12, "जनवरी": 1, "फ़रवरी": 2, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4,
    "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "अक्टूबर": 10, "नवंबर": 11,
    "दिसंबर": 12,
}
_MONTH_RE = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"
_DAY_WORD = r"(?:tarikh|tareekh|तारीख)"

# A "word" boundary that also works for Devanagari, where \b fails on vowel signs.
_L = r"(?<![a-z0-9ऀ-ॿ])"
_R = r"(?![a-z0-9ऀ-ॿ])"

_ORD_ONES = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"]
_ORD_WORDS = dict(zip(_ORD_ONES + ["tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
                                   "fifteenth", "sixteenth", "seventeenth", "eighteenth",
                                   "nineteenth", "twentieth"], range(1, 21)))
_ORD_WORDS.update({f"twenty[- ]{w}": 21 + i for i, w in enumerate(_ORD_ONES)})
_ORD_WORDS.update({"thirtieth": 30, "thirty[- ]first": 31})
_ORD_WORD_RE = "|".join(sorted(_ORD_WORDS, key=len, reverse=True))


def _ordinal_value(word: str) -> int:
    return next(v for k, v in _ORD_WORDS.items() if re.fullmatch(k, word))


def _ordinal_words_to_digits(t: str) -> str:
    """'the first of november' -> 'the 1st of november'. Only next to a month name, so
    'ek second' (one moment) never becomes a date."""
    t = re.sub(_L + "(" + _ORD_WORD_RE + r")(?=\s+(?:of\s+)?(?:" + _MONTH_RE + ")" + _R + ")",
               lambda m: f"{_ordinal_value(m.group(1))}th", t)
    return re.sub("(" + _MONTH_RE + r")\s+(?:the\s+)?(" + _ORD_WORD_RE + ")" + _R,
                  lambda m: f"{m.group(1)} {_ordinal_value(m.group(2))}th", t)


@dataclass(frozen=True)
class DateResult:
    date: date | None
    ambiguous: bool
    reason: str  # ok | ambiguous_phrase | unrecognized | past_date | invalid_date | conflict


def _ok(d: date) -> DateResult:
    return DateResult(d, False, "ok")


def _unclear(reason: str) -> DateResult:
    return DateResult(None, True, reason)


def _has(text: str, phrases: list[str]) -> bool:
    return any(re.search(_L + re.escape(p) + _R, text) for p in phrases)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_day(today: date, day: int, month: int | None, next_month: bool) -> DateResult:
    if month is None:
        year, month = today.year, today.month
        if next_month or day < today.day:
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        d = _safe_date(year, month, day)
        return _ok(d) if d else _unclear("invalid_date")
    d = _safe_date(today.year, month, day)
    if d is None:
        return _unclear("invalid_date")
    if d < today:
        if (today - d).days <= _ROLL_TO_NEXT_YEAR_DAYS:
            return _unclear("past_date")
        d = _safe_date(today.year + 1, month, day)
        if d is None:
            return _unclear("invalid_date")
    return _ok(d)


def normalize(text: str, today: date, lang: str | None = None) -> DateResult:
    """English, Hindi and Hinglish forms are matched in one pass, so `lang` is informational."""
    from emi_agent.heard import date_numbers_to_digits  # "पाँच नवंबर" -> "5 नवंबर"
    t = _ordinal_words_to_digits(" ".join(date_numbers_to_digits(text).lower().split()))

    if _has(t, _AMBIGUOUS):
        return _unclear("ambiguous_phrase")
    if _has(t, _LAST_DAY):
        return _ok(today.replace(day=calendar.monthrange(today.year, today.month)[1]))
    if _has(t, _MONTH_END):
        return _unclear("ambiguous_phrase")

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        d = _safe_date(*map(int, m.groups()))
        if d is None:
            return _unclear("invalid_date")
        return _ok(d) if d >= today else _unclear("past_date")

    m = re.search(_L + r"(?:in\s+)?(\d{1,2})\s+(?:days|din)" + _R, t)
    if m:
        return _ok(today + timedelta(days=int(m.group(1))))

    for phrases, offset in _RELATIVE:
        if _has(t, phrases):
            return _ok(today + timedelta(days=offset))

    next_month = _has(t, _NEXT_MONTH)
    explicit = _explicit_day(t, today, next_month)
    weekday = _weekday(t, today)
    if explicit is not None:
        # A weekday that disagrees with the date is never resolved silently (G-08): ask instead.
        if weekday is not None and explicit.date is not None and explicit.date.weekday() != weekday[0]:
            return _unclear("conflict")
        return explicit
    if weekday is not None:
        return _ok(weekday[1])

    # "मैं after 1 week करूंगी" (owner call B004 17:59): vague like "next week", so ask which day.
    if next_month or _has(t, _NEXT_WEEK) or _AFTER_WEEKS.search(t):
        return _unclear("ambiguous_phrase")
    return _unclear("unrecognized")


def _weekday(t: str, today: date) -> tuple[int, date] | None:
    for wd, names in _WEEKDAYS.items():
        names_re = "|".join(map(re.escape, names))
        if re.search(_L + _NEXT_WORDS + r"\s+(?:" + names_re + ")" + _R, t):
            next_monday = today + timedelta(days=7 - today.weekday())
            return wd, next_monday + timedelta(days=wd)
        if _has(t, names):
            return wd, today + timedelta(days=(wd - today.weekday()) % 7 or 7)
    return None


def _explicit_day(t: str, today: date, next_month: bool) -> DateResult | None:
    """A day of the month, with or without a month name; None when there is none."""
    patterns = [
        (_L + r"(\d{1,2})" + _ORD + r"\s*(?:of\s+)?(" + _MONTH_RE + ")" + _R, False),
        (_L + "(" + _MONTH_RE + r")\s+(\d{1,2})" + _ORD + _R, True),
    ]
    for pattern, month_first in patterns:
        m = re.search(pattern, t)
        if m:
            day_s, month_s = (m.group(2), m.group(1)) if month_first else m.groups()
            return _resolve_day(today, int(day_s), _MONTHS[month_s], False)

    # A bare number counts as a day only with an ordinal, "tarikh", or "the"/"on" before it,
    # so amounts like "5000" are never read as dates.
    m = re.search(_L + r"(?:(?:on|the)\s+)+(\d{1,2})" + _ORD + _R, t) \
        or re.search(_L + r"(\d{1,2})(?:st|nd|rd|th)" + _R, t) \
        or re.search(_L + r"(\d{1,2})\s*" + _DAY_WORD, t)
    if m:
        return _resolve_day(today, int(m.group(1)), None, next_month)
    return None
