"""Deepgram keyterm prompting: words the borrower is likely to say that Nova-3 multi mishears.

Live call 2026-11-04: "बाईस July" came back as "buys, July"; earlier "Hello" as "2." and "Lumen
Finance" as "Human finance". Keyterms work with model=nova-3&language=multi (Deepgram changelog,
2025-11-26). Measured with bakeoff/stt_keyterms.py before any set is used by default.

Sets (config stt.keyterms):
  none   no keyterms (not "off": YAML reads a bare off as false)
  dates  Hindi 11-31, "उन्नीस सौ", Hindi month names, call words, the borrower's first name
  full   dates plus Hindi 0-10, which are short everyday words too ("दो" is also "give"), so
         they are the likeliest to make Deepgram hear numbers that were not said
"""
from __future__ import annotations

from emi_agent.speech_format import _HI_DEVA, _MONTHS_DEVA

_CALL_WORDS = ["Lumen Finance", "EMI", "UPI", "hello", "date of birth"]
_DAYS_11_31 = _HI_DEVA[11:32]
_DIGITS_0_10 = _HI_DEVA[0:11]

SETS = {
    "none": [],
    "dates": _DAYS_11_31 + ["उन्नीस सौ"] + list(_MONTHS_DEVA) + _CALL_WORDS,
    "full": _DIGITS_0_10 + _DAYS_11_31 + ["उन्नीस सौ"] + list(_MONTHS_DEVA) + _CALL_WORDS,
}


def for_call(name: str, first_name: str | None = None) -> list[str]:
    """The keyterms for one call: the named set plus the borrower's first name (not for "none")."""
    if name not in SETS:
        raise ValueError(f"Unknown stt.keyterms set: {name!r} (expected one of {sorted(SETS)})")
    terms = list(SETS[name])
    if terms and first_name:
        terms.append(first_name)
    return terms
