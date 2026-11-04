"""The Brain: one call's conversation loop. No audio code; voice and evals both drive respond()."""
from __future__ import annotations

import asyncio

import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import AsyncIterator

from emi_agent import context, guard, speakable
from emi_agent.heard import find_dob, find_last4, partial_digits
from emi_agent.language import LanguageTracker, detect, language_words, says_hindi_numbers
from emi_agent.llm import LLMError
from emi_agent.outcome import Outcome, OutcomeRecord, append_record
from emi_agent.state import Borrower, CallState, Stage
from emi_agent.tools import TOOL_SCHEMAS, Tools, loan_facts, protected_phrases

MAX_TOOL_ROUNDS = 4
INCOMPLETE_DOB = ("Not checked: the date of birth the borrower gave is incomplete. Ask only for "
                  "the missing part; never guess it.")
HOLD_RETRY_SECS = 1.0  # pause before retrying the model once after a failure
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")

def _words(*patterns: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9ऀ-ॿ])(?:" + "|".join(patterns) + r")(?![a-z0-9ऀ-ॿ])",
                      re.IGNORECASE)


# Always a stop request (G-06).
_STOP_STRONG = re.compile(
    r"stop calling|never call|leave me alone|remove my number|stop (?:this|it) now|"
    r"(?:don'?t|do not) want (?:any )?(?:more )?calls|no more calls|stop bothering|"
    r"(?:don'?t|do not|never) (?:bother|contact) me|"
    r"pareshan (?:mat|na) (?:kar|kij)|dobara (?:call|phone|fon) (?:mat|na)|परेशान मत",
    re.IGNORECASE,
)
# A stop request unless the same sentence names a time or a payment ("don't call me in the
# morning", "bas karo, main pay kar dunga"), which means "not now", not "never" (D-24).
_STOP_WEAK = re.compile(
    r"don'?t call|do not call|^\W*stop\W*$|"
    r"(?:call|phone|fon) (?:mat|na) (?:kar|kij)|mat karo call|band karo|bas karo|"
    r"कॉल मत|फ़ोन मत|फोन मत|बंद करो",
    re.IGNORECASE,
)
_PERMANENT = _words(r"again", r"ever", r"never", r"any ?more", r"kabhi", r"dobara", r"phir se",
                    "कभी", "दोबारा", "फिर से")
_NOT_NOW = _words(
    r"morning", r"evening", r"afternoon", r"night", r"later", r"now", r"today", r"tomorrow",
    r"before", r"after", r"till", r"until", r"\d+", r"o'clock", r"busy", r"driving",
    r"meeting", r"office", r"abhi", r"subah", r"shaam", r"sham", r"dopahar", r"raat", r"baad",
    r"kal", r"parso", "अभी", "सुबह", "शाम", "रात", "बाद", r"pay", r"paid", r"payment", r"bhar",
    r"jama", r"(?:kar|de) (?:dunga|dungi|denge|doongi|doonga)", "भुगतान", "जमा",
)


# "I won't pay" is a refusal, not a payment, so it must not soften a stop.
_NO_PAY = re.compile(
    r"(?:won'?t|will not|not|can'?t|cannot|never)\s+(?:\S+\s+){0,2}?pay\w*|"
    r"pay nahi\w*(?: kar\w*)?|nahi?n? (?:dunga|dungi|karunga|karungi|bharunga|bharungi|denge)",
    re.IGNORECASE)


def is_stop_request(text: str) -> bool:
    t = text.strip().replace("’", "'")
    if _STOP_STRONG.search(t):
        return True
    if not _STOP_WEAK.search(t):
        return False
    return bool(_PERMANENT.search(t)) or not _NOT_NOW.search(_NO_PAY.sub(" ", t))


# "What's this about?" before verification (owner, 2026-11-04): answered first, without the loan.
_ASKS_PURPOSE = re.compile(
    r"what(?:'s| is) (?:this|it|the call) (?:about|regarding|for)|what is this regarding|why (?:are you|did you) call|"
    r"what do you want|kis (?:baare|bare|cheez|chiz) (?:me|mein|main)|kya baat hai|kya kaam hai|kyun call|"
    r"किस (?:बारे|चीज़|चीज) (?:में|मे)|क्या बात है|क्या काम है|क्यों (?:call|फ़ोन|फोन|कॉल)", re.IGNORECASE)


# What the agent's last line asked for, so code knows which value to look for (D-25).
_ASK_DOB = re.compile(r"birth|\bdob\b|janm|जन्म|paidaish|पैदाइश", re.IGNORECASE)
_ASK_LAST4 = re.compile(r"last (?:four|4)|four digits|4 digits|mobile|digits|aakhri|akhri|antim|"
                        r"\bank\b|आखिरी|अंतिम|अंक|मोबाइल", re.IGNORECASE)


@dataclass(frozen=True)
class Sentence:
    text: str
    guard: str | None = None  # guard category if the model's sentence was replaced
    bridge: bool = False  # said by code while a tool ran (no dead air), not by the model
    speech: tuple[str, ...] = ()  # pre-TTS fixes and flags (speakable.py)


class Brain:
    def __init__(self, config: dict, borrower: Borrower, today: date, llm,
                 calls_path: Path | None = None, clock=time.monotonic):
        lang = borrower.preferred_language
        self.config = config
        self.llm = llm
        self.script = config["tts"]["script"]
        self.calls_path = calls_path or Path(config["behavior"]["calls_log"])
        self.state = CallState(borrower_id=borrower.borrower_id, reply_language=lang,
                               languages_used=[lang], numbers=config["behavior"]["hindi_numbers"])
        self.tools = Tools(self.state, borrower, today, config)
        self.lang = LanguageTracker(lang)
        self.greeting = context.greeting(borrower, config["behavior"]["lender_name"], lang, self.script)
        self.system = context.system_prompt(borrower, config, lang, self.greeting)
        self.history: list[dict] = []
        self.transcript: list[tuple[str, str]] = [("agent", self.greeting)]
        self.usage = {"input": 0, "output": 0, "cache_read": 0}
        self.turns_by_provider: dict[str, int] = {}  # shows a fallback to another model
        self.should_hang_up = False
        self._said_closing = False  # the last words before hanging up were actually spoken
        self._first_name = borrower.first_name
        self._protected = protected_phrases(borrower, today)
        self._pending_notes: list[str] = []
        self._dob_pending = False  # a partial date of birth was given; the next answer completes it
        self._last4_pending = False  # fewer than four digits were given ("It's double" | "7 4 3")
        self._attempts_at_turn_start = 0  # a verify result may be stated only if this went up
        self._muted = False  # rest of this reply dropped after an invented verify result
        self._bridges, self._bridge_noted = 0, False
        self._warm_closed = False  # "Please take care..." already said this call
        self._handoff_said = False  # "our internal team will ... call back" already said this call
        self._clock = clock
        self._started = clock()

    async def respond(self, user_text: str, lang_hint: str | None = None) -> AsyncIterator[Sentence]:
        s = self.state
        if s.stage is Stage.ENDED:
            return
        if self.should_hang_up:
            if not self._said_closing:  # the closing turn was cut off or had no words
                yield self._closing_line()
            return
        self.transcript.append(("borrower", user_text))
        s.heard.append(user_text)
        attempts_before = s.verify_attempts  # before _code_verify, which may use one
        self._add_turn_notes(user_text, lang_hint)
        self.history.append({"role": "user", "content": user_text})
        if self._pending_notes:
            self.history.append({"role": "system", "content": "\n".join(self._pending_notes)})
            self._pending_notes = []

        self._attempts_at_turn_start = attempts_before
        self._muted = False  # set when an invented verification result was replaced
        self._bridges, self._bridge_noted = 0, False  # bridge lines said this turn (no dead air)
        spoken: list[str] = []
        finished = False
        held = 0  # 1 once the hold line was said
        try:
            for attempt in (1, 2):
                try:
                    async for out in self._rounds(spoken):
                        yield out
                    break
                except LLMError as e:
                    s.tool_calls.append({"name": "_llm_error", "args": {}, "result": {"error": str(e)},
                                         "attempts": attempt, "ms": 0, "stage": s.stage.value})
                    if attempt == 2 or spoken:  # never repeat words already said
                        raise
                    line = context.hold(s.reply_language, self.script)
                    spoken.append(line)
                    held = 1
                    yield Sentence(line)
                    await asyncio.sleep(HOLD_RETRY_SECS)
            # A bridge line ("I understand.") is not a reply: the closing is still owed.
            if len(spoken) == held + self._bridges and (s.stage is Stage.CLOSING or "stop_requested" in s.flags):
                line = context.closing(s.reply_language, self.script, self._first_name)
                spoken.append(line)
                yield Sentence(line)
            finished = True
        except LLMError:
            s.flags.add("llm_failed")
            # Outcome already recorded: the call's work is done, so thank them instead of apologising.
            line = (context.closing(s.reply_language, self.script, self._first_name)
                    if s.outcome is not None else context.apology(s.reply_language, self.script))
            spoken.append(line)
            self.should_hang_up = True
            yield Sentence(line)
            finished = True
        finally:  # also runs when a barge-in cancels this turn mid-stream
            self._end_turn(spoken, finished)

    async def _rounds(self, spoken: list[str]) -> AsyncIterator[Sentence]:
        """Model requests until it stops calling tools. Appends each spoken sentence to `spoken`."""
        bridge = None
        for _ in range(MAX_TOOL_ROUNDS):
            turn, buf = None, ""
            async for kind, value in self.llm.stream(self.system, self.history, TOOL_SCHEMAS):
                if kind == "turn":
                    turn = value
                    continue
                if kind == "tool_start":
                    if not spoken and not buf.strip() and not self._muted and bridge is None:
                        bridge = context.bridge(value, self.state.reply_language, self.script)
                        self._bridges += 1
                        spoken.append(bridge)
                        yield Sentence(bridge, bridge=True)
                    continue
                buf += value
                *done, buf = _SENTENCE_END.split(buf)
                for sentence in done:
                    if (out := self._guarded(sentence)) and out.text not in spoken:
                        spoken.append(out.text)
                        yield out
            # A sentence already said this turn is not said again: the model wrote the closing,
            # called record_outcome, then wrote it again (v10 eval S04 run 2, S05 run 3).
            if buf.strip() and (out := self._guarded(buf.strip())) and out.text not in spoken:
                spoken.append(out.text)
                yield out
            for k in self.usage:
                self.usage[k] += turn.usage.get(k, 0)
            self.turns_by_provider[turn.provider] = self.turns_by_provider.get(turn.provider, 0) + 1
            self.history.append({"role": "assistant", "content": turn.content})
            if not turn.tool_calls:
                return
            results = [{"type": "tool_result", "tool_use_id": c.id,
                        "content": json.dumps(self._tool(c.name, c.input), ensure_ascii=False)}
                       for c in turn.tool_calls]
            self.history.append({"role": "user", "content": results})
            if bridge and not self._bridge_noted:  # once per turn
                self.history.append({"role": "system", "content": context.note_bridge(bridge)})
                self._bridge_noted = True

    def _closing_line(self) -> Sentence:
        line = context.closing(self.state.reply_language, self.script, self._first_name)
        self._said_closing = True
        self.transcript.append(("agent", line))
        return Sentence(line)

    def _end_turn(self, spoken: list[str], finished: bool) -> None:
        s = self.state
        said = " ".join(spoken)
        if not finished:
            # Interrupted: the reply never reached history, so record what was said and that it was cut (LLD 9).
            said = f"{said} [cut off by the borrower]".strip()
            self.history.append({"role": "assistant", "content": said})
        self.transcript.append(("agent", said))
        if s.stage is Stage.REMINDING and s.verified:
            s.stage = Stage.INTENT
        s.last_agent = said
        if s.stage is Stage.CLOSING or "stop_requested" in s.flags:
            self.should_hang_up = True
        if self.should_hang_up and finished and spoken:
            self._said_closing = True

    def _add_turn_notes(self, user_text: str, lang_hint: str | None) -> None:
        s = self.state
        content = language_words(user_text)
        # A turn that was mostly numbers or months ("Buys, July 19 94.") says nothing about language.
        counts = content == user_text.strip() or len(content.split()) >= 2
        if counts and self.lang.update(detect(content, lang_hint), len(content.split())):
            s.reply_language = self.lang.current
            s.languages_used = list(self.lang.used)
            facts = None
            if s.verified:
                facts = self.tools.facts()
                s.loan_facts = facts
                s.disclose(facts)
            self._pending_notes.append(context.note_language(s.reply_language, facts))
        if s.numbers != "hindi" and says_hindi_numbers(user_text):
            # Mirror the borrower: they said numbers in Hindi words, so the agent does too.
            s.numbers = "hindi"
            if s.verified and s.reply_language != "en":
                facts = self.tools.facts()
                s.loan_facts = facts
                s.disclose(facts)
                self._pending_notes.append(context.note_numbers(facts))
        if not s.verified and _ASKS_PURPOSE.search(user_text):
            self._pending_notes.append(context.NOTE_PURPOSE.format(name=self._first_name))
        if is_stop_request(user_text):
            s.flags.add("stop_requested")
            self._pending_notes.append(context.NOTE_STOP)
        elapsed = self._clock() - self._started
        if elapsed > self.config["behavior"]["max_call_secs"] and "time_up" not in s.flags:
            s.flags.add("time_up")
            self._pending_notes.append(context.NOTE_TIME_UP)
        self._code_verify()

    def _code_verify(self) -> None:
        """Check identity in code when the borrower answers the question, so the model needs one
        request instead of two (D-25). If nothing usable was said, the model's own verify_identity
        call still works."""
        s, tools = self.state, self.tools
        if (s.verified or s.stage in (Stage.CLOSING, Stage.ENDED) or "stop_requested" in s.flags
                or s.verify_attempts >= tools.max_attempts):
            return
        asked = next((t for who, t in reversed(self.transcript) if who == "agent"), "")
        want_dob = self._dob_pending or bool(_ASK_DOB.search(asked))
        want_last4 = self._last4_pending or bool(_ASK_LAST4.search(asked))
        was_partial4, self._last4_pending = self._last4_pending, False
        self._dob_pending = False
        now = s.heard[-1]
        factor = value = None
        if want_dob:
            got, missing = find_dob([now], tools.today)
            if got is None and missing and len(s.heard) > 1:
                prev_got, prev_missing = find_dob([s.heard[-2]], tools.today)
                if prev_got is None and prev_missing:  # join two halves, never a rejected full date
                    got, missing = find_dob(s.heard[-2:], tools.today)
            if got:
                factor, value = "dob", got.isoformat()
            elif missing and missing != {"day", "month", "year"} and not want_last4:
                self._pending_notes.append(context.note_dob_partial(missing))
                self._dob_pending = True
                return
        if factor is None and want_last4:
            digits = find_last4(now)
            if digits is None and was_partial4:  # join two halves, never a rejected full answer
                digits = find_last4(f"{s.heard[-2]} {now}")
            lone_year = digits and want_dob and 1900 <= int(digits) <= tools.today.year
            if digits and not lone_year:
                factor, value = "last4", digits
            elif digits is None and partial_digits(now):
                self._last4_pending = True
        if factor is None:
            return
        result = tools.call("verify_identity", {"factor": factor, "value": value})
        s.tool_calls[-1]["by"] = "code"
        if result.get("verified"):
            self._pending_notes.append(context.note_verified(s.loan_facts))
        elif result.get("attempts_left") == 0:
            self._pending_notes.append(context.NOTE_VERIFY_FAILED)
        else:
            self._pending_notes.append(context.note_verify_mismatch(factor, result["attempts_left"]))

    def _tool(self, name: str, args: dict) -> dict:
        """Run the model's tool call. A date of birth that code found incomplete this turn is not
        checked: the model guessed the missing part ("buys July 19 94" sent as 19 July, live call
        2026-11-04) and used up an attempt."""
        s = self.state
        if (name == "verify_identity" and isinstance(args, dict) and args.get("factor") == "dob"
                and self._dob_pending and not s.verified):
            result = {"verified": False, "error": "incomplete_date", "message": INCOMPLETE_DOB}
            s.tool_calls.append({"name": name, "args": args, "result": result, "attempts": 0,
                                 "ms": 0, "stage": s.stage.value, "by": "blocked"})
            return result
        return self.tools.call(name, args)

    def _guarded(self, sentence: str) -> Sentence | None:
        """The sentence as it will be spoken, or None to drop it."""
        s = self.state
        if self._muted:
            return None
        upset = any(f in s.flags for f in ("escalate:hardship", "escalate:dispute"))  # v14 special situations
        if (upset and guard.anything_else(sentence)) or (guard.warm_close(sentence) and self._warm_closed):
            s.guard_hits.append({"category": "dropped_filler", "original": sentence, "spoken": "",
                                 "stage": s.stage.value, "verified": s.verified})
            return None
        if guard.handoff(sentence):
            if self._handoff_said:  # "our internal team will ... call back": once per call
                s.guard_hits.append({"category": "dropped_filler", "original": sentence, "spoken": "",
                                     "stage": s.stage.value, "verified": s.verified})
                return None
            self._handoff_said = True
        if guard.warm_close(sentence):
            self._warm_closed = True  # "Please take care..." once per call
        if guard.invented_notice(sentence):
            s.guard_hits.append({"category": "invented_notice", "original": sentence, "spoken": "",
                                 "stage": s.stage.value, "verified": s.verified})
            return None
        left = self.tools.max_attempts - s.verify_attempts
        if not s.verified and guard.invented_verify_result(
                sentence, checked_now=s.verify_attempts > self._attempts_at_turn_start,
                attempts_left=left):
            # G-08: no check failed this turn (or "last chance" with more left). One re-ask
            # replaces the rest of this reply, which built on the invented result.
            self._muted = True
            line = context.retry(s.reply_language, self.script)
            s.guard_hits.append({"category": "invented_verify", "original": sentence, "spoken": line,
                                 "stage": s.stage.value, "verified": s.verified})
            self._pending_notes.append(context.note_guard(line))
            return Sentence(line, "invented_verify")
        r = guard.check(sentence, verified=s.verified, protected=self._protected,
                        lang=s.reply_language, script=self.script, heard=s.heard)
        if r.category is not None:
            s.guard_hits.append({"category": r.category, "original": sentence, "spoken": r.text,
                                 "stage": s.stage.value, "verified": s.verified})
            self._pending_notes.append(context.note_guard(r.text))
        text, hits = speakable.prepare(r.text, s.reply_language, self.script, s.numbers,
                                       self._fact_phrases())
        if hits:
            s.speech_hits.append({"hits": hits, "original": r.text, "spoken": text,
                                  "lang": s.reply_language})
        return Sentence(text, r.category, tuple(hits))

    def greeting_cut(self) -> None:
        """The borrower barged in on the greeting: they never heard who we want (bot, 2026-11-04)."""
        s = self.state
        if s.verified or "greeting_cut" in s.flags:
            return
        s.flags.add("greeting_cut")
        self._pending_notes.append(context.NOTE_GREETING_CUT.format(name=self._first_name))

    def silence_line(self, close: bool) -> str:
        """What code says to a silent borrower: the greeting again if it was cut and nothing was
        heard since, else "can you hear me?"; on the second silence, a polite close."""
        s = self.state
        if close:
            line = context.silent_close(s.reply_language, self.script)
            s.flags.add("silent_close")
            self.should_hang_up = True
            self._said_closing = True
        elif "greeting_cut" in s.flags and not s.heard:
            line = self.greeting  # the identity question is in it, so the cut note is not needed
            note = context.NOTE_GREETING_CUT.format(name=self._first_name)
            self._pending_notes = [n for n in self._pending_notes if n != note]
            s.flags.discard("greeting_cut")
        else:
            line = context.nudge(s.reply_language, self.script)
        self.transcript.append(("agent", line))
        if not close:
            self._pending_notes.append(context.note_silence(line))
        return line

    def _fact_phrases(self) -> dict[str, str] | None:
        """This borrower's facts in Hindi number words -> the phrases the tools gave (number style
        english). Only after verification, when the facts may be spoken."""
        s = self.state
        if not s.verified or s.numbers != "english" or s.reply_language == "en":
            return None
        hindi = loan_facts(self.tools.borrower, self.tools.today, "hi", "devanagari").as_dict()
        spoken = self.tools.facts().as_dict()
        return {hindi[k]: spoken[k] for k in hindi if hindi[k] and spoken.get(k) and hindi[k] != spoken[k]}

    def finish(self) -> OutcomeRecord:
        """Validate and write the outcome record (CLOSING -> ENDED). Code picks one if the model didn't."""
        s = self.state
        if s.outcome is None:
            fallback = Outcome.stop_requested if "stop_requested" in s.flags else Outcome.unresolved
            # The model escalated for hardship or a dispute but closed without recording it
            # (v6/v7 evals, S03: 5 of 6 runs). The escalation reason is the outcome.
            for reason in ("hardship", "dispute"):
                if f"escalate:{reason}" in s.flags and fallback is Outcome.unresolved:
                    fallback = Outcome(reason)
            outcome, promise, notes = fallback, None, "recorded by code: model did not record an outcome"
        else:
            # Rebuild so actions taken after record_outcome (e.g. a link sent) are reflected.
            outcome, promise, notes = s.outcome.outcome, s.outcome.promise_to_pay_date, s.outcome.notes
        s.outcome = self.tools.build_record(outcome, promise, notes)
        append_record(s.outcome, self.calls_path)
        s.stage = Stage.ENDED
        self.should_hang_up = True
        return s.outcome
