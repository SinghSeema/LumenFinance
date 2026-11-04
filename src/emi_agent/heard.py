"""Numbers the borrower actually said, so code can tell a heard value from an invented one.

Reads digits and number words in English, Hindi (Roman and Devanagari) and Devanagari digits.
Matching is exact on whole numbers: part of a number never counts as said.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from emi_agent.speech_format import (_EN_ONES, _EN_TENS, _HI_DEVA, _HI_ROMAN, _MONTHS_DEVA,
                                     _MONTHS_EN, _ORDINAL_EN)

# Devanagari letters and digits, but not the danda (। ॥): "चौरानवे।" is the word "चौरानवे".
_WORD = re.compile(r"\d+|[a-z]+|[\u0900-\u0963\u0966-\u097F]+")
_HUNDRED = {"hundred", "sau", "सौ"}
# "two thousand one", "दो हज़ार एक" (a 2001 birth year was read as 2002: "July two" + "one").
_THOUSAND = {"thousand", "hazaar", "hazar", "hajar", "हज़ार", "हजार"}
_AND = {"and", "aur", "और"}
# "उन्नीस सौ सतासी" with an unknown spelling of 87 read as the year 1900 (STT bake-off 2026-11-04).
# No borrower was born before this, so an earlier "year" is a misread: the year is missing, ask.
_MIN_BIRTH_YEAR = 1920
_REPEAT = {"double": 2, "triple": 3}
_MAX_WINDOW = 8
# Number words that are also everyday words ("de do" = give, "ek minute", "one moment", "oh").
_EVERYDAY = {"do", "ek", "one", "oh", "teen", "bees", "das", "saath", "दो", "एक"}


def _loose(word: str) -> str:
    """Spelling-tolerant form of romanised Hindi: 'unnis' and 'unnees' both become 'unis'."""
    return re.sub(r"(.)\1+", r"\1", word.replace("ee", "i").replace("oo", "u"))


def _loose_deva(word: str) -> str:
    """Spelling-tolerant Devanagari: long/short vowels, chandrabindu/anusvara, nukta and ब/व are
    written both ways ('बाइस'/'बाईस', 'चौरानबे'/'चौरानवे'). Live call 2026-11-04."""
    for a, b in (("ई", "इ"), ("ी", "ि"), ("ऊ", "उ"), ("ू", "ु"), ("ँ", "ं"), ("़", ""), ("व", "ब")):
        word = word.replace(a, b)
    return word


def _small_numbers() -> dict[str, int]:
    """Words for 0-99. Loose Hindi spellings are added only where they stay unambiguous."""
    words = {w: n for n, w in enumerate(_EN_ONES)} | {"oh": 0}
    words |= {w: n for n, w in enumerate(_HI_ROMAN)} | {w: n for n, w in enumerate(_HI_DEVA)}
    loose: dict[str, set[int]] = {}
    for n, w in enumerate(_HI_ROMAN):
        loose.setdefault(_loose(w), set()).add(n)
    for n, w in enumerate(_HI_DEVA):
        loose.setdefault(_loose_deva(w), set()).add(n)
    for w, ns in loose.items():
        if len(ns) == 1 and w not in words:
            words[w] = ns.pop()
    return words


_SMALL = _small_numbers()
_TENS = {w: 10 * n for n, w in enumerate(_EN_TENS) if w != "_"}
_ORDINALS = {w: n for n, w in _ORDINAL_EN.items()}
_ONES_ORDINAL = {w: n for n, w in _ORDINAL_EN.items() if n < 10}
_MONTH_WORDS: dict[str, int] = {}
for _i, _m in enumerate(_MONTHS_EN, 1):
    _MONTH_WORDS[_m.lower()] = _MONTH_WORDS[_m.lower()[:3]] = _i
for _i, _m in enumerate(_MONTHS_DEVA, 1):
    _MONTH_WORDS[_m] = _MONTH_WORDS[_m.replace("़", "")] = _i
_MONTH_WORDS["sept"] = 9


def _ascii_digits(text: str) -> str:
    return "".join(str(unicodedata.decimal(c)) if c.isdigit() else c for c in text)


# "22nd" is the number 22: split as "22", "nd", the stray "nd" sat between day and month and the
# date read as missing its day (live call 2026-11-04, "22nd July 19 94").
_DIGIT_ORDINAL = re.compile(r"(\d)(?:st|nd|rd|th)(?![a-z])")


def _words(text: str) -> list[str]:
    return _WORD.findall(_DIGIT_ORDINAL.sub(r"\1", _ascii_digits(text.lower())))


def _small_at(words: list[str], i: int) -> tuple[int, int] | None:
    """(value, words used) for a number below 100 starting at words[i]."""
    w = words[i]
    nxt = words[i + 1] if i + 1 < len(words) else ""
    if w in _TENS:
        if nxt in _SMALL and 0 < _SMALL[nxt] < 10 and nxt in _EN_ONES:
            return _TENS[w] + _SMALL[nxt], 2
        if nxt in _ONES_ORDINAL:
            return _TENS[w] + _ONES_ORDINAL[nxt], 2
        return _TENS[w], 1
    if w in _ORDINALS:
        return _ORDINALS[w], 1
    if w in _SMALL:
        return _SMALL[w], 1
    if (lw := _loose(w)) in _SMALL:
        return _SMALL[lw], 1
    if (dw := _loose_deva(w)) in _SMALL:
        return _SMALL[dw], 1
    return None


def _tokens(text: str) -> list[tuple[str, bool, bool]]:
    """(number, from an everyday word like Hindi 'do', spoken as a numeral) in the order said."""
    return [t[:3] for t in _tokens_at(_words(text))]


def _tokens_at(words: list[str]) -> list[tuple[str, bool, bool, int, int]]:
    """As _tokens, plus the [start, end) word positions of each number."""
    out, i = [], 0
    while i < len(words):
        w, start = words[i], i
        if w.isdigit():
            out.append((w, False, True, start, i + 1))
            i += 1
            continue
        nxt = words[i + 1] if i + 1 < len(words) else ""
        # "double seven" or, from Deepgram numerals, "double 7"
        r = (int(nxt), 1) if len(nxt) == 1 and nxt.isdigit() else nxt and _small_at(words, i + 1)
        if w in _REPEAT and r and r[0] < 10:
            out += [(str(r[0]), False, False, start, i + 1 + r[1])] * _REPEAT[w]
            i += 1 + r[1]
            continue
        r = _small_at(words, i)
        if r is None:
            i += 1
            continue
        value, used = r
        everyday = used == 1 and w in _EVERYDAY
        i += used
        if i < len(words) and words[i] in _HUNDRED:
            value, everyday = value * 100, False
            i += 1
            if i < len(words) and (rest := _small_at(words, i)):
                value += rest[0]
                i += rest[1]
        if i < len(words) and words[i] in _THOUSAND:
            value, everyday = value * 1000, False
            j = i + 1 + (i + 1 < len(words) and words[i + 1] in _AND)
            if j < len(words) and (rest := _below_1000(words, j)):
                value += rest[0]
                i = j + rest[1]
            else:
                i += 1
        out.append((str(value), everyday, False, start, i))
    return out


def _below_1000(words: list[str], i: int) -> tuple[int, int] | None:
    """(value, words used) for 1-999 at words[i]: "five", "पाँच सौ", "three hundred twelve"."""
    r = _small_at(words, i)
    if r is None:
        return None
    value, j = r[0], i + r[1]
    if j < len(words) and words[j] in _HUNDRED:
        value, j = value * 100, j + 1
        if j < len(words) and (rest := _small_at(words, j)):
            value, j = value + rest[0], j + rest[1]
    return value, j - i


def number_tokens(text: str) -> list[str]:
    """Each number in the order said, as digit strings. '4 8 2 1' is four tokens here."""
    return [t[0] for t in _tokens(text)]


def _groups(tokens: list[tuple[str, bool, bool]]) -> tuple[list[str], set[str]]:
    """Digits said one at a time form one number: '4 8 2 1' -> '4821'.

    Also returns that run without an everyday word at an edge whose neighbour was a numeral
    ('de do 4 8 2 1' -> '4821'); 'chaar aath do ek' keeps its final 'ek'.
    """
    groups: list[str] = []
    extra: set[str] = set()
    run: list[tuple[str, bool, bool]] = []

    def close() -> None:
        if not run:
            return
        groups.append("".join(t[0] for t in run))
        lo = int(len(run) > 1 and run[0][1] and run[1][2])
        hi = len(run) - int(len(run) > 1 and run[-1][1] and run[-2][2])
        for a, b in ((lo, len(run)), (0, hi), (lo, hi)):
            if b - a >= 1:
                extra.add("".join(t[0] for t in run[a:b]))
        run.clear()

    for t in tokens:
        if len(t[0]) == 1:
            run.append(t)
            continue
        close()
        groups.append(t[0])
    close()
    return groups, extra


def said_numbers(utterances: list[str]) -> set[str]:
    """Whole numbers the borrower said, plus neighbours joined within one utterance ('48 21')."""
    said: set[str] = set()
    for u in utterances:
        # "last four digits three three six seven": the "four" names the question (eval S15).
        g, extra = _groups(_tokens(_FOUR_PHRASE.sub(" ", u)))
        said |= extra
        for i in range(len(g)):
            for j in range(i + 1, min(len(g), i + _MAX_WINDOW) + 1):
                said.add("".join(g[i:j]))
    return said


def digits_were_said(value: str, utterances: list[str]) -> bool:
    """Said in one utterance, or across two in a row when the first was cut off mid-digits
    ("It's double" | "7 4 3"). Whole numbers in separate turns ("48" | "21") don't join."""
    digits = "".join(c for c in _ascii_digits(value) if c.isdigit())
    pairs = [f"{a} {b}" for a, b in zip(utterances, utterances[1:]) if partial_digits(a)]
    return bool(digits) and digits in said_numbers(utterances + pairs)


def dob_was_said(d: date, utterances: list[str]) -> bool:
    """Day, month and year each said by the borrower (in any of their turns), or a compact form."""
    utterances = [_MISHEARD_22.sub("22 ", u) for u in utterances]  # same reading as find_dob
    said = said_numbers(utterances)
    if {d.strftime("%d%m%Y"), d.strftime("%Y%m%d"), d.strftime("%d%m%y")} & said:
        return True
    small = {int(t) for u in utterances for t in number_tokens(u) if len(t) <= 2}
    months = {_MONTH_WORDS[w] for u in utterances for w in _words(u) if w in _MONTH_WORDS}
    year_ok = str(d.year) in said or f"{d.year % 100:02d}" in {t for u in utterances
                                                              for t in number_tokens(u)}
    return d.day in small and (d.month in months or d.month in small) and year_ok


# "last four", "four digits", "aakhri chaar" name the question, they are not part of the answer.
_FOUR_PHRASE = re.compile(r"\b(?:last|final|aakhri|akhri|antim)\s+(?:four|4|chaar|char)\b|"
                          r"\b(?:four|4|chaar|char)\s+(?:digits?|ank|number)\b|आखिरी चार|अंतिम चार",
                          re.IGNORECASE)


def find_last4(text: str) -> str | None:
    """The single four-digit number in one utterance ('7 7 4 3', '77 43', 'saat saat chaar teen')."""
    text = _FOUR_PHRASE.sub(" ", text)
    text = _CORRECTION.split(text)[-1]  # "सत्तावन... नहीं मतलब सत्तर चार तीन": only what follows counts
    if _UNSURE.search(text):  # "...चार... तीन... haan कुछ वही": the borrower is not sure; ask again
        return None
    fours = {n for n in said_numbers([text]) if len(n) == 4}
    if len(fours) > 1:
        # "twenty-two July ninety-four ... three three six seven": 2294 is two DOB parts joined;
        # four digits said one at a time are the answer (eval S15, 2026-11-04).
        fours = {"".join(run) for run in _single_digit_runs(text) if len(run) == 4}
    return fours.pop() if len(fours) == 1 else None


def _single_digit_runs(text: str) -> list[list[str]]:
    """Consecutive numbers of one digit each: '22 94 3 3 6 7' -> [['3', '3', '6', '7']]."""
    runs: list[list[str]] = [[]]
    for t in _tokens(text):
        if len(t[0]) == 1:
            runs[-1].append(t[0])
        elif runs[-1]:
            runs.append([])
    return [r for r in runs if r]


def partial_digits(text: str) -> bool:
    """Some digits but no four-digit number yet ("7 4", "It's double"): the answer was cut off."""
    if find_last4(text) is not None:
        return False
    tokens = [t for t in number_tokens(_FOUR_PHRASE.sub(" ", text)) if len(t) == 1]
    ends_repeat = bool(re.search(r"\b(?:double|triple)\W*$", text, re.IGNORECASE))
    return 0 < len(tokens) < 4 or ends_repeat


# A self-correction: what follows is the answer ("इक्कीस जुलाई... मतलब बाईस जुलाई", eval S15).
# Hedges: the borrower is not sure of the value, so it is asked again, never checked (v10 fix-run
# S14a run 5: "सत्तर... चार... तीन... haan कुछ वही" was checked as 7043).
_UNSURE = re.compile(r"कुछ वही|शायद|पता नहीं|लगता है|\b(?:umm+|uhh*|maybe|i think|pata nahi|shayad|"
                     r"kuch wahi|lagta hai|or)\b|(?<![ऀ-ॿ])या(?![ऀ-ॿ])", re.IGNORECASE)

# "यानी"/"yaani" (that is) restates the same answer; it is not a correction: "nine November
# nineteen eighty-five, यानी zero nine, eleven, one nine eight five" kept only the digit tail and
# read 8 Sep 1985 (v10 fix-run S14a run 5).
_CORRECTION = re.compile(r"\b(?:i mean|sorry|actually|no wait|matlab)\b|मतलब|सॉरी", re.IGNORECASE)


# Deepgram multi writes the Hindi "बाईस" (22) as English-looking words: "Buys, July 19 94",
# "Bies." (live calls 2026-11-04). Read as 22 only right before a month name, where no English
# sense fits. "Twice" also came back, but it is a real word in an answer, so it is not mapped.
_MISHEARD_22 = re.compile(
    r"\b(?:buys|bies|byes|baes|bayes)\b[\s,.]*(?=(?:" + "|".join(sorted(
        (m for m in _MONTH_WORDS if m.isascii() and len(m) > 2), key=len, reverse=True)) + r")\b)",
    re.IGNORECASE)


def find_dob(utterances: list[str], today: date) -> tuple[date | None, set[str]]:
    """A full date of birth from these utterances, in order, plus the parts still missing.

    The day is the number next to the month word ("22 July", "July 22"), never just the first
    small number ("मैंने तीन बार बता दिया... 22 July 1994"). "last four digits" is not part of a date.
    After a correction word, only what follows counts. Two different dates without one: not a date.
    Without a month word: numbers only, year first means year-month-day, else day-month-year.
    Returns (None, set()) when every part is there but they do not form one real date.
    """
    text = _FOUR_PHRASE.sub(" ", " ".join(utterances))
    text = _MISHEARD_22.sub("22 ", text)
    if _UNSURE.search(_CORRECTION.split(text)[-1]):  # "18 March 1985... नहीं, शायद 1987": ask again
        return None, set()
    parts = _CORRECTION.split(text)
    tail = parts[-1]
    if len(parts) > 1 and (_tokens(tail) or any(w in _MONTH_WORDS for w in _words(tail))):
        text = tail
    words = _words(text)
    toks = [(int(v), everyday, v, a, b) for v, everyday, _, a, b in _tokens_at(words)]
    month_at = [i for i, w in enumerate(words) if w in _MONTH_WORDS]

    year = year_tok = None
    for k, (n, _, raw, a, b) in enumerate(toks):
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if len(raw) == 4 and _MIN_BIRTH_YEAR <= n <= today.year:
            year, year_tok = n, {k}
            break
        if n in (19, 20) and nxt and nxt[0] < 100 and _MIN_BIRTH_YEAR <= n * 100 + nxt[0] <= today.year:
            year, year_tok = n * 100 + nxt[0], {k, k + 1}
            break

    if month_at:
        skip = year_tok or set()

        def at(pos: int, edge: int):  # the number whose start (edge 3) or end (edge 4) is at pos
            return next((t for k, t in enumerate(toks) if t[edge] == pos and k not in skip), None)

        dates = set()
        for m in month_at:
            before, after = at(m, 4), at(m + 1, 3)
            day, follow = None, after
            if before and 1 <= before[0] <= 31:      # "22 July"
                day = before[0]
            elif after and 1 <= after[0] <= 31:      # "July 22"
                day, follow = after[0], at(after[4], 3)
            y = year
            if y is None and follow and len(follow[2]) <= 2:  # "July ninety-four" -> 1994
                yy = follow[0]
                y = 1900 + yy if yy > today.year % 100 else 2000 + yy
            dates.add((day, _MONTH_WORDS[words[m]], y))
        if len({d[0] for d in dates if d[0]}) > 1:
            return None, set()  # two different days and no correction word: ask, don't guess
        day, month, year = max(dates, key=lambda d: sum(v is not None for v in d))
    else:
        rest = [t for k, t in enumerate(toks) if k not in (year_tok or set())]
        small = [t[0] for t in rest if 1 <= t[0] <= 31 and not t[1]] or [t[0] for t in rest if 1 <= t[0] <= 31]
        month = day = None
        if len(small) >= 2:
            year_first = year_tok is not None and min(year_tok) == 0
            a, b = small[0], small[1]
            month, day = (a, b) if year_first else (b, a)
        elif small:
            day = small[0]
    missing = {k for k, v in (("day", day), ("month", month), ("year", year)) if v is None}
    if missing:
        return None, missing
    try:
        return date(year, month, day), set()
    except ValueError:
        return None, set()


def has_number_or_month(text: str) -> bool:
    """True for answers like 'April', '9', 'saat': short, but not a backchannel."""
    return bool(_tokens(text)) or any(w in _MONTH_WORDS for w in _words(text))


_DATE_ANCHOR = {"तारीख", "tarikh", "tareekh", "date"}


def date_numbers_to_digits(text: str) -> str:
    """Number words right next to a month name or "तारीख" as digits, the rest unchanged:
    "पाँच नवंबर" -> "5 नवंबर", "नवंबर की बीस तारीख" -> "नवंबर की 20 तारीख". Elsewhere numbers stay
    words, so "एक-दो दिन में" (a day or two) is never read as a date (v10 fix-run S14a run 1)."""
    words = _words(text)
    anchor = lambda i: 0 <= i < len(words) and (words[i] in _MONTH_WORDS or words[i] in _DATE_ANCHOR)
    out, last = [], 0
    for value, _, numeral, a, b in _tokens_at(words):
        if numeral or not (anchor(a - 1) or anchor(b)):
            continue
        out += words[last:a] + [value]
        last = b
    return " ".join(out + words[last:]) if out else text
