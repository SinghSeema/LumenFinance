"""A second opinion on an escalation the word lists could not confirm (owner, 2026-11-04).

The word lists in special.py cannot cover every way a borrower names money trouble or a dispute.
When the agent escalates as hardship or dispute and no listed words match, one short LLM request
reads only the borrower's last lines, with every digit replaced by '#' (no name, no loan facts, none
of the agent's lines), and names their intent. Code decides from the intent label only. The short
'why' goes to the outcome notes, the call page and the logs; the suggested 'reply' (1-2 warm lines)
goes to the agent as the line to open with, so the agent stays the one voice and every word it says
still passes the output guard. Model: a small Sarvam model (behavior.intent_check_model), no fallback.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from loguru import logger

INTENTS = {"hardship", "dispute", "late_salary", "complaint", "other"}

PROMPT = """You help on a lender's EMI reminder call. The caller reminded the borrower that their monthly loan
EMI is due or overdue and asked when they can pay. Read what the borrower said back, name their intent,
and suggest a short, warm reply.
Reply with JSON only, no other text:
{"intent": "<label>", "why": "<at most 12 words, English>", "reply": "<one sentence, at most 20 words>"}
Labels:
- hardship: they describe a real setback that makes paying hard now: lost job or income, illness, accident, a death in the family, business loss, or serious money trouble.
- dispute: they say a fact in our records is wrong: the amount or due date is not what their loan says, the loan is not theirs, or they already paid and it is not shown.
- late_salary: only that salary or money comes a little later; no setback.
- complaint: anger or annoyance that a charge exists ("why a late fee?", "pagal ho kya"), at the calls, the app or the process, without saying a fact in our records is wrong.
- other: anything else.
"reply": what a respectful woman from the lender's collections team would say first: one short, plain,
practical sentence that shows she understood and that this makes the EMI hard right now, like a bank
officer, not a counsellor (e.g. "समझ सकती हूँ, नौकरी जाने पर EMI भरना मुश्किल हो जाता है।"). No "don't
worry" / "चिंता न करें", no "we understand you" / "हम आपकी बात समझते हैं". Same language and script as the borrower (English,
Hindi in Devanagari, or Hinglish). In Hindi use feminine forms for yourself ("समझ सकती हूँ", "samajh
sakti hoon"). No amounts, dates, fees, promises of any action, waivers, advice or question. Never
"take care" or "ध्यान रखिए". Numbers are hidden as #."""

_DIGITS = re.compile(r"[0-9०-९]")
_EARLY = re.compile(r'"intent"\s*:\s*"(\w+)"')


def redact(text: str) -> str:
    """Digits (a date of birth, mobile digits, amounts) never leave in the check."""
    return _DIGITS.sub("#", text)


def _parse(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).split("</think>")[-1]
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    intent = str(data.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        return None
    return {"intent": intent, "why": str(data.get("why", "")).strip()[:200],
            "reply": str(data.get("reply", "")).strip()[:300]}


async def check(llm, utterances: list[str], timeout: float) -> dict:
    """{"intent", "why", "ms", "usage"}; intent is None when the check failed or was too slow."""
    lines = "\n".join(redact(u) for u in utterances[-3:])
    messages = [{"role": "user", "content": f"The borrower said:\n{lines}"}]
    start, out, usage = time.monotonic(), "", {}

    async def run() -> None:
        nonlocal out, usage
        async for kind, value in llm.stream(PROMPT, messages, []):
            if kind == "text":
                out += value
            elif kind == "turn":
                usage = value.usage

    try:
        await asyncio.wait_for(run(), timeout)
        result = _parse(out) or {"intent": None, "why": "unreadable answer", "reply": ""}
    except Exception as e:  # timeout or provider error: the escalation goes through (owner, 2026-11-04)
        # The label is the first field: a slow answer still gives it (probe 2026-11-04: 1.1-2.2 s in all).
        early = _EARLY.search(out)
        label = early.group(1).lower() if early and early.group(1).lower() in INTENTS else None
        result = {"intent": label, "why": f"check cut at the time limit ({type(e).__name__})" if label
                  else f"check failed: {type(e).__name__}", "reply": ""}
    result.update(ms=round((time.monotonic() - start) * 1000), usage=usage)
    logger.info(f"intent check: {result['intent']} ({result['why']}) in {result['ms']} ms")
    return result
