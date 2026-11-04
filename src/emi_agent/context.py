"""The only place model input text is built (LLD section 3).

The system prompt is fixed for the whole call and holds no loan data. Anything that changes
mid-call (language, stop, guard hits, time limit) is appended as a system note, never edited in,
so the history stays append-only. Loan facts arrive only in verify_identity's result.
"""
from __future__ import annotations

from pathlib import Path

from emi_agent import policy
from emi_agent.state import Borrower, LoanFacts

_PROMPTS = Path(__file__).parent / "prompts"

LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "hinglish": "Hinglish (Hindi and English mixed, as the borrower speaks)",
}

# Owner, 2026-11-04: say once, lightly, that this is an AI assistant (trust before asking for details).
_GREETING = {
    "en": "Hello, I'm an AI assistant calling from {lender}. Am I speaking with {name}?",
    "roman": "Namaste, main {lender} ki taraf se AI assistant bol rahi hoon. "
             "Kya meri baat {name} ji se ho rahi hai?",
    "devanagari": "नमस्ते, मैं {lender} की तरफ़ से AI assistant बोल रही हूँ। "
                  "क्या मेरी बात {name} जी से हो रही है?",
}

_APOLOGY = {
    "en": "I'm sorry, I'm having a technical problem. Our internal team will arrange a call back for you. Thank you.",
    "roman": "Maaf kijiye, abhi kuch technical dikkat aa rahi hai. Hamari internal team aapko "
             "callback arrange karegi. Dhanyavaad.",
    "devanagari": "माफ़ कीजिए, अभी कुछ तकनीकी दिक्कत आ रही है। हमारी internal team आपको callback "
                  "arrange करेगी। धन्यवाद।",
}

# Owner, 2026-11-04 (labelling S12 run 1, naturalness 1): "noted, thank you" sounded cold. A person
# apologises for the trouble, says it was only a gentle reminder so no late fee is added, and stops.
# The EMI is named only after verification (G-01); still one turn, and the call ends (G-06).
def note_stop(verified: bool, facts: "LoanFacts | None" = None) -> str:
    if verified and facts is not None:
        why = (f"then say it was only a gentle reminder that their EMI is due on {facts.due_date_spoken}"
               + (" (it is overdue)" if facts.overdue_spoken else "")
               + ", so that no late fee is added; no amount, no request to pay")
    else:
        why = "never mention a loan, EMI, amount or date (they are not verified)"
    return ("The borrower asked to stop. Close now, in this one reply: apologise briefly for disturbing them, "
            f"{why}; say you have noted that they don't want calls about this; thank them. Ask nothing, call "
            "record_outcome with stop_requested.")
NOTE_TIME_UP = ("The call has reached its time limit. Wrap up in one sentence, call "
                "record_outcome, and close with thanks.")


def _key(lang: str, script: str) -> str:
    return "en" if lang == "en" else script


def _script_rule(script: str) -> str:
    if script == "devanagari":
        return "When you speak Hindi, write it in Devanagari script."
    return "When you speak Hindi, write it in Roman letters, as people type it in chat."


def greeting(borrower: Borrower, lender: str, lang: str, script: str) -> str:
    return _GREETING[_key(lang, script)].format(lender=lender, name=borrower.first_name)


def apology(lang: str, script: str) -> str:
    return _APOLOGY[_key(lang, script)]


# Said while the model is retried once after a failure (rate limit, timeout).
_HOLD = {"en": "One moment, please.", "roman": "Ek second, please.", "devanagari": "एक सेकंड।"}

# Said when the call is closing but the model's closing words never played (cut off or empty).
# Thanks, not "goodbye" or "alvida": that is how a polite Indian caller ends a call.
_CLOSING = {
    "en": "Thank you for your time, {name} ji. Have a good day.",
    "roman": "Aapke samay ke liye dhanyavaad, {name} ji. Aapka din shubh ho.",
    "devanagari": "आपके समय के लिए धन्यवाद, {name} जी। आपका दिन शुभ हो।",
}


# Said by code when the borrower is silent (bot silence prompt, 2026-11-04). "आप ... सुन पा रहे हैं"
# is the respectful plural, right for any listener; the agent's own words stay feminine (D-20).
_NUDGE = {
    "en": "Hello, can you hear me?",
    "roman": "Hello, kya aap mujhe sun pa rahe hain?",
    "devanagari": "Hello, क्या आप मुझे सुन पा रहे हैं?",
}
_SILENT_CLOSE = {
    "en": "I can't hear you, so I'll end the call now. We'll call you again later. Thank you.",
    "roman": "Mujhe aapki awaaz nahi aa rahi, isliye main call band kar rahi hoon. Hum aapko baad mein "
             "phir call karenge. Dhanyavaad.",
    "devanagari": "मुझे आपकी आवाज़ नहीं आ रही, इसलिए मैं call बंद कर रही हूँ। हम आपको बाद में फिर call "
                  "करेंगे। धन्यवाद।",
}
NOTE_GREETING_CUT = ("Your greeting was cut off after a moment, so the borrower did not hear who you "
                     "want to speak to. Before anything else, ask again whether you are speaking with {name}.")


# Said instead of a verification result the model invented (guard.invented_verify_result).
_RETRY = {
    "en": "Sorry, I didn't catch all of that. Could you say it once more?",
    "roman": "Maaf kijiye, main poora sun nahi paayi. Ek baar phir bata dijiye?",
    "devanagari": "माफ़ कीजिए, मैं पूरा सुन नहीं पाई। एक बार फिर बता दीजिए?",
}


# Said the moment the model starts a tool call with nothing said yet in the turn, so the caller
# hears a natural line, not silence: tool turns took 1.8-2.4 s to first audio against 0.3-0.7 s
# for other turns (owner call 2026-11-04 13:46). The brief: "tool calls without dead air".
_BRIDGE = {
    "verify_identity": {"en": "One moment, let me check that.", "roman": "Ek second, main check kar leti hoon.",
                        "devanagari": "एक second, मैं check कर लेती हूँ।"},
    "send_payment_link": {"en": "Sure, I'm sending the link now.", "roman": "Theek hai, main link bhej rahi hoon.",
                          "devanagari": "ठीक है, मैं link भेज रही हूँ।"},
    # Before "I'm really sorry to hear about your job": "Okay." was jarring (v13 S03).
    # Hindi kept to "जी।": "जी, मैं समझ रही हूँ।" doubled the model's own "आपकी चिंता मैं समझ सकती हूँ" (v14-special3).
    "escalate_to_human": {"en": "I hear you.", "roman": "Ji.", "devanagari": "जी।"},
    # Short on purpose: "जी, समझ गई।" was followed by the model's own "ठीक है, मैं समझ गई।" (v11 S06).
    "_ack": {"en": "Okay.", "roman": "Ji.", "devanagari": "जी।"},
    # Owner, 2026-11-04: a policy look-up may take a moment, so say so.
    # Rotated (bridge(n=...)): the same line before each of three questions sounded scripted (v17-policy S17).
    "get_policy": [{"en": "One moment, let me check the details for you.",
                    "roman": "Ek second, main details check kar leti hoon.",
                    "devanagari": "एक second, मैं details check कर लेती हूँ।"},
                   {"en": "Sure, let me see.", "roman": "Ji, dekhti hoon.", "devanagari": "जी, देखती हूँ।"},
                   {"en": "Good question, one second.", "roman": "Ji, bas ek second.", "devanagari": "जी, बस एक second।"}],
    # Closing a hardship or dispute call: "Okay." before the close sounded curt (v16-special S03).
    "_special_close": {"en": "I understand.", "roman": "Ji.", "devanagari": "जी।"},
}


def bridge(tool: str, lang: str, script: str, special: bool = False, n: int = 0) -> str:
    """n: how many times this tool's bridge was said before in the call, to rotate the variants."""
    if special and tool == "record_outcome":
        tool = "_special_close"
    lines = _BRIDGE.get(tool, _BRIDGE["_ack"])
    if isinstance(lines, list):
        lines = lines[n % len(lines)]
    return lines[_key(lang, script)]


def note_bridge(line: str) -> str:
    return (f"While that tool ran, the caller already heard you say: \"{line}\". Do not say it "
            "again; continue naturally from there.")


def retry(lang: str, script: str) -> str:
    return _RETRY[_key(lang, script)]


def nudge(lang: str, script: str) -> str:
    return _NUDGE[_key(lang, script)]


def silent_close(lang: str, script: str) -> str:
    return _SILENT_CLOSE[_key(lang, script)]


def note_silence(line: str) -> str:
    return (f"The borrower was silent, so the call system said: \"{line}\". If they answer, "
            "continue from where the conversation was.")


def hold(lang: str, script: str) -> str:
    return _HOLD[_key(lang, script)]


def closing(lang: str, script: str, name: str) -> str:
    return _CLOSING[_key(lang, script)].format(name=name)


def system_prompt(borrower: Borrower, config: dict, lang: str, opening: str) -> str:
    template = (_PROMPTS / f"system_{config['behavior']['prompt_version']}.md").read_text()
    return template.format(
        lender=config["behavior"]["lender_name"],
        first_name=borrower.first_name,
        borrower_id=borrower.borrower_id,
        greeting=opening,
        language_name=LANGUAGE_NAMES[lang],
        script_rule=_script_rule(config["tts"]["script"]),
        policy=policy.short(),  # generic repayment policy (data/policy.yaml); unused by prompts before v17
    )


def note_numbers(facts: LoanFacts) -> str:
    spoken = "; ".join(f"{k}: {v}" for k, v in facts.as_dict().items())
    return ("The borrower says numbers in Hindi words. Say numbers in Hindi words from now on. "
            f"Loan facts as phrases: {spoken}.")


def note_language(lang: str, facts: LoanFacts | None) -> str:
    note = f"The borrower is now speaking {LANGUAGE_NAMES[lang]}. Reply in that language from now on."
    if facts:
        spoken = "; ".join(f"{k}: {v}" for k, v in facts.as_dict().items())
        note += f" Loan facts in this language: {spoken}."
    return note


def note_guard(spoken_instead: str) -> str:
    return ("Your last sentence was not spoken because it broke a call rule. "
            f"The borrower heard this instead: \"{spoken_instead}\" Continue from there.")


# Code-driven verification (D-25): code checks identity before the model runs and tells it the result.
_FACTOR = {"dob": "date of birth", "last4": "last four digits of the registered mobile number"}


def status_note(facts: LoanFacts) -> str:
    """Whether the EMI is due or overdue, in code's words. Live call B004 (2026-11-04): a loan due in
    four days was told "was due" and "can you pay it today?", then "a late charge has been added"."""
    if facts.overdue_spoken:
        return (f"This EMI is overdue by {facts.overdue_spoken}. Ask whether they can clear it today and offer "
                "the payment link.")
    return (f"This EMI is not yet due: it is due on {facts.due_date_spoken}. Say \"is due\", never \"was due\" or "
            "\"overdue\". No late charge has been added. Offer the payment link so it is paid before the due "
            "date; do not ask them to pay today.")


def note_verified(facts: LoanFacts) -> str:
    spoken = "; ".join(f"{k}: {v}" for k, v in facts.as_dict().items())
    return ("The system has verified the borrower's identity from what they just said; this replaces "
            "any earlier note about a missing or wrong answer, so do not ask for it again. Do not call "
            "verify_identity or get_loan_details. Thank them briefly and give the reminder now. "
            f"Loan facts, to say as written: {spoken}. {status_note(facts)}")


def note_split(whole: str) -> str:
    """Live call B004: "...what is the late payment?" [agent cut in] "I need to pay" was read as "I will pay"."""
    return ("The borrower's sentence was split by interruptions. All together they said: "
            f"\"{whole}\". Answer that whole sentence; the last part alone is not a new answer.")


# Said instead of a sentence with a rupee amount that is not this borrower's or in our policy (G-08).
_NO_AMOUNT = {
    "en": "I don't have that exact amount with me; our internal team can confirm it.",
    "roman": "Woh sahi amount abhi mere paas nahi hai; hamari internal team confirm kar degi.",
    "devanagari": "वह सही amount अभी मेरे पास नहीं है; हमारी internal team confirm कर देगी।",
}
# Said instead of "a late charge has been added" on a loan with no charge (live call B004).
_FEE_NOT_ADDED = {
    "en": "No late charge has been added; if the EMI isn't paid by the due date, a late payment charge of {fee} applies.",
    "roman": "Abhi koi late charge nahi laga hai; agar due date tak EMI nahi bhari gayi, to {fee} ka late payment "
             "charge lagega.",
    "devanagari": "अभी कोई late charge नहीं लगा है; अगर due date तक EMI नहीं भरी गई, तो {fee} का late payment "
                  "charge लगेगा।",
}


def no_amount(lang: str, script: str) -> str:
    return _NO_AMOUNT[_key(lang, script)]


def fee_not_added(lang: str, script: str, fee: str) -> str:
    return _FEE_NOT_ADDED[_key(lang, script)].format(fee=fee)


def next_ask(factor: str) -> str:
    """What to ask after a failed check (owner, 2026-11-04): last four digits first, then the
    date of birth; after a date of birth that fails, either one."""
    if factor == "last4":
        return "their date of birth instead (do not ask for the digits again)"
    return "once more, for their date of birth or the last four digits of their registered mobile number"


def note_verify_mismatch(factor: str, left: int) -> str:
    return (f"The system checked the {_FACTOR[factor]} the borrower just gave: it does not match "
            f"our records. Attempts left: {left}. Say kindly that it did not match and ask "
            f"{next_ask(factor)}. Never say which part was wrong. Do not call "
            "verify_identity for this answer.")


# Owner, 2026-11-04 (labelling): "What's this about?" was ignored and the digits asked for at once.
# Answer it first without naming the loan or EMI (G-01: whoever answered may not be the borrower).
# v11 check, S06 run 2: a relative asked, and was told "your account" and asked for the digits.
# Owner, 2026-11-04: "account verification", never "privacy".
NOTE_PURPOSE = ("They asked what this call is about. Answer that first, warmly, in one short sentence, and "
                "never say loan, EMI, an amount or a date. If they have said they are {name}: it is regarding "
                "their account with Lumen Finance; then, for account verification, ask for the last four digits "
                "of their registered mobile number. If someone "
                "else answered (a relative, a colleague, or they have not said who they are and {name} is not "
                "available): say it is a personal matter for {name}, ask when is a good time to reach {name}, "
                "and never ask them for any verification details.")


NOTE_UNHEARD = ("Your last reply was cut off at once, so the borrower did not hear it. Answer what they "
                "say now, and briefly include its key point again (for example your apology or your "
                "question); do not repeat it word for word.")


NOTE_VERIFY_FAILED = ("Identity could not be verified after three attempts. Apologise briefly, say our "
                      "internal team will arrange a call back, and close with thanks. Share no loan detail and call no tool.")


def note_dob_partial(missing: set[str]) -> str:
    parts = " and ".join(p for p in ("day", "month", "year") if p in missing)
    return (f"The borrower gave only part of their date of birth; the {parts} is still missing. "
            "Nothing has been checked yet, so never say it does not match. Ask only for the "
            "missing part, in one short natural question (for example \"जुलाई की कौन सी तारीख?\" / "
            "\"Which day in July?\"), with no format instructions. Do not call verify_identity yet.")
