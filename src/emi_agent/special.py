"""Special situations (hardship, wrong amount, not my loan) as a code-owned flow (prompt v16).

How a good human agent handles these calls, in a few short turns: acknowledge once, ask one useful
question, read back what was noted with the one hand-off, then close warmly. Code decides which step
comes next and tells the model only that step; the model only words it. A step counts as done only
when the borrower heard the reply (brain._end_turn), so a reply cut at once is given again.
"""
from __future__ import annotations

import re

# The owner's words (2026-11-04, D-39); never "a colleague" or "the concerned team".
_HANDOFF = ('say our internal team will check this and arrange a call back ("our internal team will check this '
            'and arrange a call back for you"; in Hindi "हमारी internal team आपकी बात check करके आपको callback '
            'arrange करेगी")')
_CONTACT = ('ask whether this number is the best one to reach them ("Is this number the best one to reach you '
            'on?"; in Hindi "क्या यह number आपसे बात करने के लिए ठीक रहेगा?")')
# Words first, then the tool: a tool call with nothing said yet plays a bridge line, and a bare
# "Okay." before the close sounded curt (v16-special S03 run 3).
_CLOSE = ('close once with thanks ("Thank you for talking with me, <name>. Have a good day."; in Hindi "बात करने '
          'के लिए धन्यवाद, <name> जी। आपका दिन शुभ हो।"), and after those words call record_outcome with outcome '
          '{outcome} and a short note of what they said')
# v16-special S03 run 3 and S04 run 2: "make sure someone actually calls me" and "I won't pay until this
# is fixed" got only "Okay. Thank you...".
_ANSWER = ("First answer what they just said, in one or two short sentences: if they asked for something (a real "
           "person to call, a call back soon, no surprise charges, a time that suits them), say you have noted it "
           "and name what they asked, in their own words; if they said they "
           "won't pay until it is sorted, say you understand. ")

STEPS: dict[str, list[str]] = {
    "hardship": [
        "In one sentence, say warmly, in their own terms, that you are really sorry to hear it and thank them "
        "for telling you. Then, only if they have not already said how long it may last or when they might "
        "manage, gently ask whether it is something short-term or hard to say right now. Say nothing after the "
        "question. Do not mention the amount, the late fee or paying.",
        "Do not apologise again. In one sentence, read back what you noted, in their words (their situation, "
        "and anything they said about timing or worries). Then " + _HANDOFF + ". Then " + _CONTACT + ".",
        _ANSWER + "Then " + _CLOSE.format(outcome="hardship") + ".",
    ],
    "dispute_amount": [
        "Say you understand a wrong amount is worrying, say what our records show (amount, due date and any "
        "late charge), and ask what amount they expected; if they already said, do not ask. Never ask whether "
        "they applied for the loan, and never guess why the amounts differ.",
        "Read back both in one sentence (\"I've noted that you expect <their amount>, while our records show "
        "<amount_spoken>\"). Say a sanction letter or an earlier EMI receipt will help. Then " + _HANDOFF
        + ". Then " + _CONTACT + ".",
        _ANSWER + "Then " + _CLOSE.format(outcome="dispute") + ".",
    ],
    "not_mine": [
        "Take their fear seriously and say they did the right thing to tell you. Say what our records show in "
        "their name (the loan type), and ask only whether they ever applied for a loan with us or any app. "
        "Never say whether it is or is not fraud.",
        "Briefly acknowledge their answer, then ask only whether they received any loan money, or recently "
        "shared documents or an OTP with anyone. If they already told you, do not ask; just acknowledge it.",
        "Read back in one sentence what you noted (whether they applied, received money, shared documents or an "
        "OTP). Say you have flagged it for review on their account. Then " + _HANDOFF + ". Then " + _CONTACT + ".",
        _ANSWER + "Advise them kindly not to share any OTP, PIN or bank details with anyone. Then "
        + _CLOSE.format(outcome="dispute") + ".",
    ],
    # A demand to waive or remove a charge, or to mark the EMI paid: not an amount dispute, so no "what
    # amount did you expect?" and no sanction letter (v16-special S11: a "senior manager" got both).
    "waiver": [
        "Say calmly that you understand what they are asking for, and say plainly, once, that you can't waive, "
        "remove or change any charge or mark the EMI paid yourself. Read back in one sentence what they asked "
        "for and why (in their words). Then " + _HANDOFF + ". Then " + _CONTACT + ". Never argue with their "
        "claim and never say whether it is true.",
        _ANSWER + "Then " + _CLOSE.format(outcome="dispute") + ".",
    ],
}

# v16-special S04b: "how did this loan come in my name?" asked three times, never answered.
_RULES = ("Say only this step, in at most four short sentences, in the borrower's language. If they asked for "
          "a waiver, a lower amount, more time or a fee removal, say kindly that you can't change that yourself "
          "and that you have noted it for the team. If they asked something the repayment policy in your "
          "instructions answers (the late fee, part payment, how to pay), answer it first from the policy in one "
          "short sentence; never say you don't have that detail. If they asked something you have no detail for (how or when "
          "a loan was made, when exactly the team will call), say honestly that you don't have that detail and "
          "the team will check it. Never promise when the call back will come, a waiver or any change. Never ask "
          "\"Is there anything else?\".")

# What code saw the agent say earlier in the call (brain._said_once), so each step can say "don't
# repeat it": "I can't change the amount myself" came three times in one call (v16-special S03 run 2).
_SAID = {"apology": "that you are sorry", "cant_change": "that you can't change it yourself",
         "handoff": "the internal team hand-off", "contact": "the question about the best number"}


def instruction(situation: str, step: int, said: set[str] = frozenset()) -> str:
    steps = STEPS[situation]
    where = f"step {step + 1} of {len(steps)}"
    text = f"Special situation ({situation.replace('_', ' ')}), {where}, from the call system. {steps[step]} {_RULES}"
    if done := [_SAID[k] for k in _SAID if k in said]:
        text += " Already said in this call, so do not say again: " + "; ".join(done) + "."
    return text


# In the record_outcome result: the model often calls the tool first and then only says thanks,
# skipping what the borrower just asked (v16-special2 S04 run 1).
AFTER_RECORD = ("Recorded. Now speak, in the borrower's language, unless you already said it this turn: " + _ANSWER
                + "Then close once with thanks (\"Thank you for talking with me, <name>. Have a good day.\"; in Hindi "
                "\"बात करने के लिए धन्यवाद, <name> जी। आपका दिन शुभ हो।\"). Do not repeat what you already said in the call.")


def is_last(situation: str, step: int) -> bool:
    return step >= len(STEPS[situation]) - 1


# "Not my loan" vs "wrong amount" need different questions; both are escalated as "dispute"
# (v14 S04: "did you ever apply?" was asked twice in an amount dispute).
_NOT_MINE = re.compile(r"not my loan|never (?:took|taken|applied|borrowed)|didn'?t (?:take|apply)|not mine|misuse|"
                       r"kabhi (?:koi )?loan nahi|loan nahi liya|mera nahi|नहीं लिया|मेरा नहीं|कभी .{0,20}apply नहीं",
                       re.IGNORECASE)


_WAIVER = re.compile(r"waive|waiver|write ?off|remove (?:the|this|that) (?:late )?(?:fee|charge)|"
                     r"mark (?:it|this|the emi)? ?(?:as )?(?:paid|settled)|maaf|माफ़ कर|माफ कर|हटा (?:दो|दीजिए)|"
                     r"special arrangement", re.IGNORECASE)
# The borrower says the amount or date itself is wrong: a real amount dispute even if they also ask
# for something to be removed.
_WRONG = re.compile(r"\bwrong\b|incorrect|not (?:right|correct)|galat|गलत|ग़लत|should be \w+ thousand|"
                    r"(?:emi|amount|किस्त) (?:is|है|hai) \w+ thousand|"
                    # "मेरी EMI twelve thousand है, eighteen thousand नहीं": a different amount stated
                    r"(?:emi|amount|किस्त)\s+(?:\S+\s+){0,3}?(?:thousand|हज़ार|हजार|hazaar)\s+(?:है|hai|is)|"
                    r"(?:thousand|हज़ार|हजार|hazaar) (?:नहीं|nahi|nahin)|\bnot \w+ thousand", re.IGNORECASE)


def dispute_cue(heard: list[str]) -> bool:
    """The borrower says something is wrong, not theirs, or should be waived."""
    return any(_WRONG.search(u) or _NOT_MINE.search(u) or _WAIVER.search(u) for u in heard)


def situation_for(reason: str, heard: list[str]) -> str | None:
    recent = heard[-3:]
    if reason == "hardship":
        return "hardship"
    if reason in ("dispute", "other") and any(_WAIVER.search(u) for u in recent) \
            and not any(_WRONG.search(u) or _NOT_MINE.search(u) for u in recent):
        return "waiver"
    if reason == "dispute":
        return "not_mine" if any(_NOT_MINE.search(u) for u in recent) else "dispute_amount"
    return None


# Clear signs of hardship in what the borrower said, so the voice can slow down before the reply
# starts (the model's escalation comes mid-reply). A false alarm only slows the voice a little; the
# flow itself still starts only from the model's escalate_to_human.
_HARDSHIP_CUE = re.compile(
    r"lost (?:my|his|her) job|job (?:loss|chal[ia] gay[ia]|chhoot|chhut|gone)|laid off|layoff|"
    r"no (?:job|income)|naukri (?:chal[ia] gay[ia]|chhoot|chhut|nahi)|"
    r"hospital|illness|sick|surgery|operation|accident|passed away|died|death|emergency|medical|"
    r"business (?:band|closed|shut|loss|doob)|lost (?:my|our) (?:business|shop)|no income|"
    r"bimar|beemar|bimaari|beemari|guzar gay|tabiyat|"
    r"(?:job|नौकरी) (?:चली गई|चला गया|छूट)|नौकरी नहीं|अस्पताल|hospital में|बीमार|बीमारी|"
    r"ऑपरेशन|एक्सीडेंट|गुज़र गए|गुजर गए|देहांत|तबीयत|इमरजेंसी|emergency",
    re.IGNORECASE)


def hardship_cue(text: str) -> bool:
    return _HARDSHIP_CUE.search(text) is not None
