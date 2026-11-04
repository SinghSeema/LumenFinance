"""Mock tools with code-enforced gating (LLD section 4). The model only ever sees the returned dicts."""
from __future__ import annotations

import re
import time
from datetime import date, datetime

from pydantic import ValidationError

from emi_agent import context, dates
from emi_agent.heard import digits_were_said, dob_was_said, find_dob, find_last4
from emi_agent.outcome import Outcome, OutcomeRecord
from emi_agent.speech_format import amount_to_words, date_to_words, days_to_words, number_lang
from emi_agent.state import Borrower, CallState, LoanFacts, Stage

GATED = {"get_loan_details", "send_payment_link"}
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
          "Flag the call for a human colleague: dispute, hardship, the borrower asked, or other.",
          {"reason": {"type": "string", "enum": _REASONS}}),
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
    )


def protected_phrases(b: Borrower, today: date) -> list[str]:
    """Every spoken form of this borrower's loan facts, for the output guard."""
    phrases = {b.loan_type, str(b.emi_amount), f"{b.emi_amount:,}"}
    for lang, script in (("en", "roman"), ("hi", "roman"), ("hi", "devanagari"),
                         ("hi-en", "roman"), ("hi-en", "devanagari")):
        f = loan_facts(b, today, lang, script)
        phrases.update(p for p in (f.amount_spoken, f.due_date_spoken, f.overdue_spoken) if p)
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
_YES = re.compile(r"^\W*(?:yes|yeah|yep|sure|ok|okay|please|haan|han|ha|ji|jee|theek|thik|zaroor|"
                  r"हाँ|हां|जी|ठीक|ज़रूर|जरूर)\b", re.IGNORECASE)


# What to tell the borrower after an escalation (owner, 2026-11-04): a dispute or a payment our
# records do not show goes to "the concerned team", as Indian lenders' calls say; someone who asked
# for a person, or is in hardship, hears that a colleague will call. No time, no ticket number.
_TEAM = ("Say you have noted it and that the concerned team will look into it and arrange a "
         "callback. Do not promise when, and give no reference number.")
_ESCALATION_LINE = {
    "dispute": _TEAM,
    "hardship": "Tell them kindly that a colleague will call them back. Do not promise when.",
    "requested": "Tell them a colleague will call them back. Do not promise when.",
    "other": "Tell them a colleague will call them back. Do not promise when.",
}

# Calls that never reached the borrower: a dispute or hardship escalation makes no sense (D-30).
_NOT_THE_BORROWER = {Outcome.wrong_number, Outcome.third_party}


def asked_for_human(utterance: str, last_agent: str) -> bool:
    if not utterance:
        return False
    if _PERSON.search(utterance) and _REQUEST.search(utterance):
        return True
    return bool(_OFFER.search(last_agent) and _YES.search(utterance))


class Tools:
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
            heard = given is not None and dob_was_said(given, s.heard)
        else:
            digits = "".join(c for c in value if c.isdigit())
            # Digits, else spoken words ("three three six seven", eval S15 2026-11-04).
            given = digits if len(digits) == 4 else find_last4(value)
            match = given == self.borrower.last4
            heard = given is not None and digits_were_said(given, s.heard)
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
            return {"verified": True, "loan_facts": facts.as_dict()}
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

    def _send_payment_link(self, channel: str) -> dict:
        mode = self.config["faults"]["payment_link_fail"]
        if mode == "always" or (mode == "once" and self._link_failures == 0):
            self._link_failures += 1
            raise ToolFailure()
        self.state.payment_link_sent = True
        self._enter_action()
        url = f"https://pay.lumenfinance.example/l/{self.borrower.borrower_id}-****"
        return {"sent": True, "channel": channel, "url": url}

    def _schedule_callback(self, when_text: str) -> dict:
        r = dates.normalize(when_text, self.today, self.state.reply_language)
        if r.date is None:
            return {"status": "ambiguous", "reason": r.reason}
        self.state.callback_time = f"{r.date.isoformat()} ({when_text})"
        self._enter_action()
        when = date_to_words(r.date, self.today, self._nlang, self.script)
        return {"status": "scheduled", "when_spoken": when}

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
                               "like a colleague to contact them; call this again only if they say yes."}
        self.state.escalate_to_human = True
        self.state.flags.add(f"escalate:{reason}")
        self._enter_action()
        return {"escalated": True, "message": _ESCALATION_LINE.get(reason, _ESCALATION_LINE["other"])}

    def _record_outcome(self, outcome: str, promise_to_pay_date: str | None, notes: str) -> dict:
        s = self.state
        if s.outcome is not None and s.outcome.outcome is Outcome.verification_failed:
            return {"error": "already_recorded", "message": "Verification failed; just close politely."}
        if outcome == Outcome.verification_failed.value:
            return {"error": "code_only_outcome",
                    "message": "Only the system records verification_failed, after three failed "
                               "checks. Record what actually happened, e.g. callback_requested "
                               "or unresolved."}
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
        # "I already paid" while our records show the EMI overdue: the records disagree, so a colleague
        # checks the payment and calls back. Code-owned, not left to the model (owner, 2026-11-04).
        payment_check = Outcome(outcome) is Outcome.already_paid and self.borrower.days_overdue > 0
        promise = None
        if promise_to_pay_date:
            r = dates.normalize(promise_to_pay_date, self.today, s.reply_language)
            if r.date is None:
                return {"error": "date_unclear", "reason": r.reason,
                        "message": "Ask the borrower one short question to get an exact date."}
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
        if payment_check:
            result |= {"team_will_check": True, "message": _ESCALATION_LINE["dispute"]}
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
            "notes": notes,
            "language_used": "+".join(s.languages_used) or s.reply_language,
            "callback_time": s.callback_time,
        }, context={"today": self.today})

    def _enter_action(self) -> None:
        if self.state.stage in (Stage.REMINDING, Stage.INTENT):
            self.state.stage = Stage.ACTION
