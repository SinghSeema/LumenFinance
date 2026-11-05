"""Mock tools with code-enforced gating (LLD section 4). The model only ever sees the returned dicts."""
from __future__ import annotations

import re
import time
from datetime import date, datetime

from pydantic import ValidationError

from emi_agent.language import _NEGATION
from emi_agent import context, dates, policy, special
from emi_agent.heard import digits_were_said, dob_was_said, find_dob, find_last4
from emi_agent.outcome import Outcome, OutcomeRecord
from emi_agent.speech_format import amount_to_words, date_to_words, days_to_words, number_lang
from emi_agent.state import Borrower, CallState, LoanFacts, Stage

# get_policy too: talking about late fees tells a stranger there is a loan (G-01).
GATED = {"get_loan_details", "send_payment_link", "get_policy"}
# One fixed refusal for every gated tool, so a refusal tells the model nothing (G-11).
NOT_VERIFIED = {"error": "not_verified", "message": "Identity must be verified first."}
NOT_YET_SAID = ("Not checked: pass only a value the borrower has said in this call. "
                "Ask them for it first; never use an example or a guess.")

_REASONS = ["dispute", "hardship", "requested", "other"]
_CHANNELS = ["sms", "whatsapp", "email"]
_DOB_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS = [
    _tool("verify_identity",
          "Check the borrower's identity. Call it with what the borrower said. For dob pass "
          "YYYY-MM-DD; for last4 pass the four digits of their registered mobile number.",
          {"factor": {"type": "string", "enum": ["dob", "last4"]},
           "value": {"type": "string"}}),
    _tool("get_loan_details",
          "Return the loan facts as ready-to-speak phrases. Works only after verification.", {}),
    _tool("send_payment_link",
          "Send the borrower a payment link. Works only after verification. Never read the link aloud.",
          {"channel": {"type": "string", "enum": _CHANNELS}}),
    _tool("schedule_callback",
          "Schedule a callback. Pass the borrower's own words for when, e.g. 'tomorrow evening'.",
          {"when_text": {"type": "string"}}),
    _tool("escalate_to_human",
          "Flag the call for our internal team to check and call back: dispute, hardship, the borrower asked, or other.",
          {"reason": {"type": "string", "enum": _REASONS}}),
    _tool("get_policy",
          "Our repayment policy in detail, the same for every account, for a follow-up question the short "
          "policy in your instructions does not answer. Works only after verification.",
          {"topic": {"type": "string", "enum": policy.topics()}}),
    _tool("record_outcome",
          "Record how the call ended. will_pay: they agree to pay but name no date. "
          "promise_to_pay: they name a specific date (the due date counts); pass their words for "
          "the date (e.g. 'next Friday'), otherwise null.",
          {"outcome": {"type": "string", "enum": [o.value for o in Outcome]},
           "promise_to_pay_date": {"type": ["string", "null"]},
           "notes": {"type": "string"}}),
]
_SCHEMA_BY_NAME = {t["name"]: t["input_schema"] for t in TOOL_SCHEMAS}


class ToolFailure(Exception):
    pass


def loan_facts(b: Borrower, today: date, lang: str, script: str) -> LoanFacts:
    return LoanFacts(
        amount_spoken=amount_to_words(b.emi_amount, lang, script),
        due_date_spoken=date_to_words(b.due_date, today, lang, script),
        loan_type=b.loan_type,
        overdue_spoken=days_to_words(b.days_overdue, lang, script) if b.days_overdue else None,
        late_fee_spoken=amount_to_words(b.late_fee, lang, script) if b.days_overdue and b.late_fee else None,
    )


def protected_phrases(b: Borrower, today: date) -> list[str]:
    """Every spoken form of this borrower's loan facts, for the output guard."""
    phrases = {b.loan_type, str(b.emi_amount), f"{b.emi_amount:,}"}
    for lang, script in (("en", "roman"), ("hi", "roman"), ("hi", "devanagari"),
                         ("hi-en", "roman"), ("hi-en", "devanagari")):
        f = loan_facts(b, today, lang, script)
        phrases.update(p for p in (f.amount_spoken, f.due_date_spoken, f.overdue_spoken, f.late_fee_spoken) if p)
    return sorted(phrases)


def _valid_args(name: str, args: dict) -> bool:
    schema = _SCHEMA_BY_NAME[name]
    if not isinstance(args, dict) or set(args) != set(schema["properties"]):
        return False
    for key, spec in schema["properties"].items():
        value = args[key]
        allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        if value is None and "null" in allowed:
            continue
        if not isinstance(value, str) or ("enum" in spec and value not in spec["enum"]):
            return False
    return True


def _parse_dob(value: str) -> date | None:
    cleaned = " ".join(value.replace(",", " ").split())
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


# G-05, explicit request: a person word plus a request word in the same utterance, or a yes right
# after the agent offered a colleague. A stray "human" (STT heard "Haan, Lumen Finance" as
# "Human finance", live call 2026-11-03) or "are you human?" is not a request.
_PERSON = re.compile(r"\b(?:human|person|agent|manager|officer|executive|colleague|representative|"
                     r"supervisor|someone|somebody|insaan|insan|aadmi|kisi|sahkarmi)\b|"
                     r"इंसान|आदमी|किसी|मैनेजर|एजेंट|सहकर्मी", re.IGNORECASE)
_REQUEST = re.compile(r"\b(?:talk|speak|connect|transfer|want|need|give|get|put|baat|karao|karwao|"
                      r"karaiye|karwaiye|karwa|chahiye|chahta|chahti|jodo|jodiye)\b|"
                      r"बात|चाहिए|चाहता|चाहती|जोड़|कराओ|कराइए|करवाइए", re.IGNORECASE)
_OFFER = re.compile(r"colleague|human|person|team|sahkarmi|insaan|सहकर्मी|इंसान", re.IGNORECASE)
# The end is a lookahead, not \b: after a Devanagari vowel sign \b never matches, so "जी, भेज दीजिए"
# and "हाँ" were never a yes (found 2026-11-04, C48).
_YES = re.compile(r"^\W*(?:yes|yeah|yep|sure|ok|okay|please|haan|han|ha|ji|jee|theek|thik|zaroor|"
                  r"हाँ|हां|जी|ठीक|ज़रूर|जरूर)(?![a-z\u0900-\u097F])", re.IGNORECASE)


# What to tell the borrower after an escalation (owner, 2026-11-04, prompt v14): help first, then one
# hand-off in the owner's words, "our internal team will check this and arrange a call back".
# Never "a colleague" or "the concerned team"; never a time, a ticket number or a change.
_HANDOFF = ("then say our internal team will check this and arrange a call back. Never say a colleague or "
            "the concerned team will call; never promise when, a waiver or any change.")
_ESCALATION_LINE = {
    "requested": "Acknowledge the request; " + _HANDOFF,
    "other": "Acknowledge what they said and note it; " + _HANDOFF,
}

_DATE_HELP = {
    "ambiguous_phrase": "They gave only a vague time. Ask one clear question: which date exactly "
                        "will they pay? Today is {today}; never suggest a date that has passed.",
    # Live call B004 20:13: "आठ November" was heard as "एक November", and the agent offered "आने वाली
    # first November" (a year away). A past date is most often misheard: ask again, suggest nothing (C52).
    "past_date": "That date has already passed (today is {today}), so it may have been misheard. Say "
                 "briefly that you may not have heard the date right and ask them to say it once more. "
                 "Do not suggest a date yourself.",
}
# C58, v17-holdout-fix S14b run 1: after the failed link the agent offered a call back without a time.
_LINK_FAILED = (" Say plainly that the link could not be sent just now. Offer to call them back to send it, "
                "and ask which day and time suits them. Never say the link was sent and never suggest another "
                "way to pay that the repayment policy does not name.")


def _opener(reply: str | None) -> str:
    """The intent check's suggested warm line (intent.py), given to the agent to open with."""
    if not reply:
        return "Acknowledge what they said in a few words first. "
    return (f'Open with a short, warm acknowledgement in the call\'s language, close to: "{reply}" '
            "(your own words are fine; no amounts, promises or waivers). ")


# Escalations whose call back our internal team arranges (D-35, v14): no schedule_callback, no time.
# "other" too: a waiver demand escalated as "other" scheduled its own callback for "tomorrow" (v13 S11 run 2).
_TEAM_CALLS_BACK = {"escalate:dispute", "escalate:hardship", "escalate:payment_check", "escalate:other"}

# v17-policy4 S17 run 2: "I'll make sure it's paid by the due date" was recorded as already_paid.
# Past tense only: "I'll make sure it is paid by the due date" is not a payment.
_PAID = re.compile(r"\balready\b|\b(?:i|i've|we|we've|have|has|had|just)\s+(?:already\s+)?paid\b|"
                   r"\bpaid (?:it |this |that |the emi )?(?:yesterday|today|last|on|already|ago|by upi|through|via|online)|"
                   r"pay kar (?:diya|di)|bhar (?:diya|di)|jama kar (?:diya|di)|"
                   r"भर दिया|भर दी|जमा कर दिया|जमा कर दी|pay कर दिया|pay कर दी|payment (?:हो गया|हो गई|कर दिया|कर दी)|"
                   r"कर दिया है|कर दी है|ho gaya hai", re.IGNORECASE)

_BY_DUE = re.compile(r"due date (?:तक|se pehle|से पहले|tak|par|पर|ko|को)|(?:by|before|on) the due date|"
                     r"(?:by|before|on) (?:the )?(?:due|deadline)\b", re.IGNORECASE)
_LEAVING = re.compile(r"\bbye\b|alvida|अलविदा|rakhti hoon|rakhta hoon|रखती हूँ|रखता हूँ|फ़ोन रख|फोन रख|"
                      r"\bgotta go\b|\bhave to go\b", re.IGNORECASE)
# The whole reply is "(no,) thank you": the answer to "anything else?" (live call B004 19:31,
# 2026-11-04: "No. Thank you." got the date question and cost a 1.25 s model round trip).
# Anchored to the whole reply, so "no, I can't pay, thank you" is not a goodbye.
_NO_THANKS = re.compile(r"^\W*(?:(?:no|nope|nahi|nahin|नहीं|ना|ji|जी|okay|ok)\W+)*"
                        r"(?:thank you|thanks|thankyou|dhanyavaad|dhanyawad|धन्यवाद|shukriya|शुक्रिया)\W*$",
                        re.IGNORECASE)

# Calls that never reached the borrower: a dispute or hardship escalation makes no sense (D-30).
_NOT_THE_BORROWER = {Outcome.wrong_number, Outcome.third_party}


def asked_for_human(utterance: str, last_agent: str) -> bool:
    if not utterance:
        return False
    if _PERSON.search(utterance) and _REQUEST.search(utterance):
        return True
    return bool(_OFFER.search(last_agent) and _YES.search(utterance))


class Tools:
    intent_check: dict | None = None  # the Brain's LLM intent check for the escalation being called (intent.py)
    check_note: str = ""  # its one-line reason, added to the outcome notes

    def __init__(self, state: CallState, borrower: Borrower, today: date, config: dict):
        self.state = state
        self.borrower = borrower
        self.today = today
        self.config = config
        self.script = config["tts"]["script"]
        self.max_attempts = config["behavior"]["verify_max_attempts"]
        self._link_failures = 0

    @property
    def _nlang(self) -> str:
        return number_lang(self.state.reply_language, self.state.numbers)

    def facts(self) -> LoanFacts:
        return loan_facts(self.borrower, self.today, self._nlang, self.script)

    def call(self, name: str, args: dict) -> dict:
        started = time.monotonic()
        attempts = 0
        result: dict
        if self.state.stage is Stage.ENDED:
            result = {"error": "call_ended"}
        elif name not in _SCHEMA_BY_NAME:
            result = {"error": "unknown_tool"}
        elif name in GATED and not self.state.verified:
            result = NOT_VERIFIED
        elif not _valid_args(name, args):
            result = {"error": "invalid_arguments"}
        else:
            # One retry, then a fixed failure the model must handle without inventing data.
            for attempts in (1, 2):
                try:
                    result = getattr(self, "_" + name)(**args)
                    break
                except ToolFailure:
                    result = {"error": "tool_failed", "message": "The service is unavailable right now."}
                    if name == "send_payment_link":  # C58: v17-holdout-fix S14b run 1
                        result["message"] += _LINK_FAILED
        self.state.tool_calls.append({
            "name": name, "args": args, "result": result, "attempts": attempts,
            "ms": round((time.monotonic() - started) * 1000, 2), "stage": self.state.stage.value,
        })
        return result

    def _verify_identity(self, factor: str, value: str) -> dict:
        s = self.state
        if s.verified:
            return {"verified": True, "loan_facts": self.facts().as_dict()}
        if s.verify_attempts >= self.max_attempts:
            return {"verified": False, "attempts_left": 0}
        if s.stage is Stage.GREETING:
            s.stage = Stage.VERIFYING
        left = self.max_attempts - s.verify_attempts
        if factor == "dob":
            # Digit formats first; else the words as heard ("बाइस July उन्नीस सौ चौरानवे", live call
            # 2026-11-04), read by the same code as the transcript check.
            given = _parse_dob(value) or find_dob([value], self.today)[0]
            match = given == self.borrower.dob
            heard = given is not None and given in self._dobs_heard()
        else:
            digits = "".join(c for c in value if c.isdigit())
            # Digits, else spoken words ("three three six seven", eval S15 2026-11-04).
            given = digits if len(digits) == 4 else find_last4(value)
            match = given == self.borrower.last4
            heard = given is not None and given in self._last4s_heard()
        if given is None:
            return {"verified": False, "attempts_left": left, "error": "unreadable_value",
                    "message": NOT_YET_SAID}
        # Checked before the stored value is compared, so the reply is the same either way (G-11).
        if not heard:
            return {"verified": False, "attempts_left": left, "error": "not_heard",
                    "message": NOT_YET_SAID}
        if match:
            s.verified = True
            s.stage = Stage.REMINDING
            facts = self.facts()
            s.loan_facts = facts
            s.disclose(facts)
            return {"verified": True, "loan_facts": facts.as_dict(), "status": context.status_note(facts)}
        s.verify_attempts += 1
        left = self.max_attempts - s.verify_attempts
        if left == 0:
            s.stage = Stage.CLOSING
            s.outcome = self.build_record(Outcome.verification_failed, None,
                                          "verification attempts exhausted")
        if left == 0:
            return {"verified": False, "attempts_left": 0}
        return {"verified": False, "attempts_left": left, "ask_next": context.next_ask(factor)}

    def _get_loan_details(self) -> dict:
        facts = self.facts()
        self.state.disclose(facts)
        return facts.as_dict()

    def _get_policy(self, topic: str) -> dict:
        return {"topic": topic, "policy": policy.detailed(topic, self._nlang, self.script),
                "message": "Answer only what they asked, in one or two short sentences, from this text. "
                           "Never add an amount, date or rule that is not in it."}

    def _send_payment_link(self, channel: str = "whatsapp") -> dict:  # WhatsApp by default (owner, D-46)
        mode = self.config["faults"]["payment_link_fail"]
        if mode == "always" or (mode == "once" and self._link_failures == 0):
            self._link_failures += 1
            raise ToolFailure()
        self.state.payment_link_sent = True
        self._enter_action()
        url = f"https://pay.lumenfinance.example/l/{self.borrower.borrower_id}-****"
        return {"sent": True, "channel": channel, "url": url}

    def _schedule_callback(self, when_text: str) -> dict:
        if any(f in self.state.flags for f in _TEAM_CALLS_BACK):
            return {"status": "not_needed", "message": "Our internal team arranges this call back "
                    "themselves. Do not schedule one and do not promise a time; record the outcome "
                    "as the escalation reason."}
        r = dates.normalize(when_text, self.today, self.state.reply_language)
        if r.date is None:
            # C58, v17-holdout-fix S14b run 1: "I'll wait for your team to get back to me" came back
            # ambiguous and the agent said "I wasn't able to set that up" and closed.
            return {"status": "ambiguous", "reason": r.reason,
                    "message": "No time was set yet. Ask once, kindly, which day and time suits them (for "
                               "example \"this evening\" or \"tomorrow morning\"), then call this again with "
                               "their words. Never say it could not be set up."}
        self.state.callback_time = f"{r.date.isoformat()} ({when_text})"
        self._enter_action()
        when = date_to_words(r.date, self.today, self._nlang, self.script)
        return {"status": "scheduled", "when_spoken": when}

    # The model's value is checked only if code itself reads that exact value from the borrower's
    # recent turns. Pieces said apart no longer count: "सत्तावन... नहीं मतलब सत्तर... चार... तीन"
    # was checked as 7043 and used an attempt (v10 fix-run S14a run 5).
    def _dobs_heard(self) -> set:
        """Dates code reads from one borrower turn, or from the last two or three together (an
        answer given in parts: "15 March" ... "1988")."""
        h = self.state.heard
        singles = {find_dob([u], self.today)[0] for u in h}
        joined = {find_dob(h[-k:], self.today)[0] for k in (2, 3) if len(h) >= k}
        return (singles | joined) - {None}

    def _last4s_heard(self) -> set:
        h = self.state.heard
        return ({find_last4(u) for u in h} | {find_last4(" ".join(h[-2:]))}) - {None}

    def _today_words(self) -> str:
        return self.today.strftime("%-d %B %Y")

    def _vague_timing(self) -> bool:
        """The borrower's last two turns gave only a vague time ("month end", "when salary comes")."""
        recent = self.state.heard[-2:]
        return any(dates.normalize(u, self.today).reason == "ambiguous_phrase" for u in recent)

    def _escalate_to_human(self, reason: str) -> dict:
        s = self.state
        if (reason != "requested" and s.outcome is not None
                and s.outcome.outcome in _NOT_THE_BORROWER):
            return {"escalated": False, "error": "not_the_borrower",
                    "message": "This person is not the borrower, so there is nothing to escalate. "
                               "Just close politely."}
        if reason == "requested" and not asked_for_human(s.heard[-1] if s.heard else "", s.last_agent):
            return {"escalated": False, "error": "not_requested",
                    "message": "The borrower has not clearly asked for a person. Ask if they would "
                               "like our internal team to arrange a call back; call this again only if they say yes."}
        if s.special:  # already in the flow: never restart it (the steps come from the call system)
            return {"escalated": True, "message": "Already noted for our internal team. Do only the step the "
                    "call system gave you this turn."}
        # v17-dev2 S08: "salary abhi aayi nahi, kal call kar lena" was escalated as hardship. With no sign
        # of a real setback in what they said, the first call is sent back; a second call goes through,
        # so a hardship told in words code does not know is delayed by one check, never refused.
        check = self.intent_check  # set by the Brain for this call only (intent.py), else None
        opener = None
        if check and reason in ("hardship", "dispute") and f"{reason}_checked" not in s.flags:
            if check["intent"] in (reason, None):  # confirmed, or the check failed: let it through
                s.flags.add(f"{reason}_checked")
                if check["intent"] == reason and check["why"]:  # for the person who calls back
                    self.check_note = check["why"]
                opener = check.get("reply")
            else:
                s.flags.add(f"{reason}_checked")
                return {"escalated": False, "error": f"not_{reason}",
                        "message": f"Not {reason}: {check['why']} " + _opener(check.get("reply")) +
                                   "Then answer what they asked and help them pick a payment date or a call back "
                                   f"time. Only if they really describe {reason}, call this again."}
        if (reason == "hardship" and "hardship_checked" not in s.flags
                and not any(special.hardship_cue(u) for u in s.heard[-3:])):
            s.flags.add("hardship_checked")
            return {"escalated": False, "error": "not_hardship",
                    "message": "They have not described a real setback (job loss, illness, an emergency, no income). "
                               "Salary arriving late or money being tight is not hardship: answer what they asked and "
                               "help them pick a payment date or a call back time. Only if they really describe "
                               "hardship, call this again."}
        # v17-dev2-fix2 S08: "pagal ho kya, five hundred ka late fee? app crash hota hai" was escalated as
        # an amount dispute. A dispute is a claim that something is wrong; anger at a correct charge is not.
        if (reason == "dispute" and "dispute_checked" not in s.flags
                and not special.dispute_cue(s.heard[-3:])):
            s.flags.add("dispute_checked")
            return {"escalated": False, "error": "not_a_dispute",
                    "message": "They have not said the amount, date or loan is wrong, or that it is not theirs. A "
                               "complaint that a charge is unfair or that the app failed is not a dispute: acknowledge "
                               "it, say the charge is as per their loan terms, note the complaint, and help them pay "
                               "or pick a date. Only if they really dispute it, call this again."}
        self.state.escalate_to_human = True
        self.state.flags.add(f"escalate:{reason}")
        self._enter_action()
        # Hardship and disputes: code-owned steps, one per turn (special.py, prompt v16).
        lead = _opener(opener) if opener else ""
        if situation := special.situation_for(reason, s.heard):
            s.special, s.special_step = situation, 0
            return {"escalated": True, "message": lead + special.instruction(situation, 0)}
        return {"escalated": True, "message": lead + _ESCALATION_LINE.get(reason, _ESCALATION_LINE["other"])}

    def _yes_to_due_date_question(self) -> bool:
        """C49, live call B004 19:51: "eighth November से पहले payment हो जाएगी ना?" - "जी payment हो जाएगी"
        was recorded as will_pay with no date, and the date was asked again. The agent's own question
        named the due date (not a link offer) and the answer is a plain yes: a promise for the due date."""
        s = self.state
        question = s.last_agent.rsplit("?", 1)[0].rsplit("।", 1)[-1].rsplit(".", 1)[-1] if "?" in s.last_agent else ""
        due = self.facts().due_date_spoken
        names_due = due.lower() in question.lower() or "due date" in question.lower()
        said = s.heard[-1] if s.heard else ""
        return (names_due and "link" not in question.lower() and bool(_YES.search(said))
                and not _NEGATION.search(said) and "?" not in said)

    def _record_outcome(self, outcome: str, promise_to_pay_date: str | None, notes: str) -> dict:
        s = self.state
        if s.outcome is not None and s.outcome.outcome is Outcome.verification_failed:
            return {"error": "already_recorded", "message": "Verification failed; just close politely."}
        if outcome == Outcome.verification_failed.value:
            return {"error": "code_only_outcome",
                    "message": "Only the system records verification_failed, after three failed "
                               "checks. Record what actually happened, e.g. callback_requested "
                               "or unresolved."}
        if (s.special and not special.is_last(s.special, s.special_step)
                and not {"stop_requested", "time_up"} & s.flags):
            return {"error": "not_yet", "message": "Not yet: the call is still going. Do only the step the "
                    "call system gave you this turn; you will be told when to record the outcome."}
        if outcome == Outcome.already_paid.value and not any(_PAID.search(u) for u in s.heard):
            return {"error": "not_paid", "message": "They have not said they already paid. Record what they "
                    "actually said, for example promise_to_pay with the due date if they will pay by then."}
        if outcome == Outcome.stop_requested.value and "stop_requested" not in s.flags:
            return {"error": "no_stop_request",
                    "message": "stop_requested is only for a borrower who asked not to be called "
                               "again; ending the call is not a stop request. Record the real "
                               "outcome. If they did ask to stop, ask them to confirm."}
        if (Outcome(outcome) in _NOT_THE_BORROWER and s.escalate_to_human
                and "escalate:requested" not in s.flags):
            # Escalated before it was clear this is not the borrower (eval S05): undo it (D-30).
            s.escalate_to_human = False
            s.flags = {f for f in s.flags if not f.startswith("escalate:")}
        # v10 eval S13 (0/5): "mahine ke end tak" was recorded as will_pay with no date. On an
        # overdue EMI, a vague time gets one clear question first; after that will_pay is accepted.
        if (Outcome(outcome) is Outcome.will_pay and self.borrower.days_overdue > 0
                and "asked_exact_date" not in s.flags and self._vague_timing()):
            s.flags.add("asked_exact_date")
            return {"error": "vague_date", "message": _DATE_HELP["ambiguous_phrase"].format(
                today=self._today_words()) + " Record promise_to_pay with that date; if they still "
                "cannot name one, record will_pay."}
        # Live call B004 (2026-11-04): the fragment "I need to pay" was recorded as will_pay with no date
        # and the call closed. On a loan not yet due, ask once whether they will pay by the due date.
        # "मैं try करूँगी पूरा amount due date तक pay करने का" is a promise for the due date (the tool
        # description says the due date counts); the model recorded will_pay and the call ended
        # unresolved (v17-dev3-fix S16 run 2). Code records it as what it is.
        if (Outcome(outcome) is Outcome.will_pay and not promise_to_pay_date and s.verified
                and (any(_BY_DUE.search(u) for u in s.heard[-2:]) or self._yes_to_due_date_question())):
            outcome, promise_to_pay_date = Outcome.promise_to_pay.value, self.borrower.due_date.isoformat()
        # Not when they are saying bye: "Ok. You. Bye." got one more question (owner call B004 17:59).
        if (Outcome(outcome) is Outcome.will_pay and not promise_to_pay_date and self.borrower.days_overdue == 0
                and s.verified and "asked_exact_date" not in s.flags
                and not (s.heard and (_LEAVING.search(s.heard[-1]) or _NO_THANKS.search(s.heard[-1])))):
            s.flags.add("asked_exact_date")
            return {"error": "no_date", "message": "Ask once, kindly, on which date they plan to pay (the due date is "
                    + self.facts().due_date_spoken + "; say 'by' the due date, never 'before' it, which means a day "
                    "earlier; a later date is their choice, never push). Record "
                    "promise_to_pay with their words for the date; if they can't say, record will_pay."}
        # An escalated dispute or hardship is the outcome, not a callback: the team arranges the
        # callback (v10 eval S04b run 4 recorded callback_requested and promised "today").
        if Outcome(outcome) is Outcome.callback_requested:
            reason = next((r for r in ("dispute", "hardship") if f"escalate:{r}" in s.flags), None)
            if reason is None and "escalate:other" in s.flags:
                reason = "unresolved"
            if reason:
                outcome = reason
        # "I already paid" while our records show the EMI overdue: the records disagree, so a colleague
        # checks the payment and calls back. Code-owned, not left to the model (owner, 2026-11-04).
        payment_check = Outcome(outcome) is Outcome.already_paid and self.borrower.days_overdue > 0
        promise = None
        if promise_to_pay_date:
            r = dates.normalize(promise_to_pay_date, self.today, s.reply_language)
            if r.date is None:
                return {"error": "date_unclear", "reason": r.reason, "message": _DATE_HELP.get(
                    r.reason, _DATE_HELP["ambiguous_phrase"]).format(today=self._today_words())}
            promise = r.date
        before = (s.escalate_to_human, set(s.flags))
        if payment_check:
            s.escalate_to_human = True
            s.flags.add("escalate:payment_check")
        try:
            record = self.build_record(Outcome(outcome), promise, notes)
        except ValidationError as e:
            s.escalate_to_human, s.flags = before
            return {"error": "invalid_outcome", "message": e.errors()[0]["msg"]}
        s.outcome = record
        s.stage = Stage.CLOSING
        result: dict = {"recorded": True}
        if s.special:
            result["message"] = special.AFTER_RECORD
        elif Outcome(outcome) is Outcome.stop_requested:  # the model writes its last words after this
            result["message"] = "Recorded. Now say your last words as the call system's note said. " + context.note_stop(
                s.verified, s.loan_facts if s.verified else None).split("Close now, in this one reply: ")[-1].split(" Ask nothing")[0]
        if payment_check:
            result |= {"team_will_check": True, "message": "Thank them; say our internal team will check the "
                       "payment and arrange a call back. Never promise when."}
        if promise:
            result["promise_date_spoken"] = date_to_words(promise, self.today, self._nlang, self.script)
        return result

    def build_record(self, outcome: Outcome, promise: date | None, notes: str) -> OutcomeRecord:
        s = self.state
        return OutcomeRecord.model_validate({
            "borrower_id": s.borrower_id,
            "verified": s.verified,
            "outcome": outcome,
            "promise_to_pay_date": promise,
            "payment_link_sent": s.payment_link_sent,
            "escalate_to_human": s.escalate_to_human,
            "notes": f"{notes} [Check: {self.check_note}]" if self.check_note else notes,
            "language_used": "+".join(s.languages_used) or s.reply_language,
            "callback_time": s.callback_time,
        }, context={"today": self.today})

    def _enter_action(self) -> None:
        if self.state.stage in (Stage.REMINDING, Stage.INTENT):
            self.state.stage = Stage.ACTION
