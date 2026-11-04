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

from emi_agent import context, dates, guard, policy, speakable, special
from emi_agent.heard import find_dob, find_last4, partial_digits
from emi_agent.language import _NEGATION, LanguageTracker, asks_numbers_in, detect, language_words, says_hindi_numbers
from emi_agent.llm import LLMError
from emi_agent.outcome import Outcome, OutcomeRecord, append_record
from emi_agent.state import Borrower, CallState, Stage
from emi_agent.speech_format import amount_to_words, hindi_date_forms
from emi_agent.tools import _YES, TOOL_SCHEMAS, Tools, loan_facts, protected_phrases

# "भेज दीजिए" / "send it" without a "yes" first (C48).
_SEND_IT = re.compile(r"^\W*(?:भेज (?:दीजिए|दीजिये|दो|दें|देना)|bhej (?:dijiye|do|dein|dena)|send (?:it|me|the link))",
                      re.IGNORECASE)

MAX_TOOL_ROUNDS = 4
INCOMPLETE_DOB = ("Not checked: the date of birth the borrower gave is incomplete. Ask only for "
                  "the missing part; never guess it.")
HOLD_RETRY_SECS = 1.0  # pause before retrying the model once after a failure
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")

# The model's hidden reasoning leaking into its reply: "जी। Close politely.</think> क्षमा चाहती हूँ..."
# (v17-dev2 S05 run 2; judge review 2026-11-04). On a call the TTS would read it aloud.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
# A first sentence that reads like an instruction to itself, held until the next text shows whether a
# "</think>" follows. Hindi text and normal replies are never held, so they get no added delay.
_INSTRUCTION = re.compile(
    r"^\s*(?:close|ask|say|respond|reply|answer|greet|end the call|now|next|then|the (?:user|borrower|caller)|user|"
    r"borrower|i should|i need to|i must|i will now|let me think|okay,? so|so,? (?:the|i)\b|we (?:should|need)|"
    r"need to|must|step \d|follow)\b|\b(?:politely|system note|tool call|record_outcome|escalate_to_human)\b",
    re.IGNORECASE)


def _strip_think(text: str) -> tuple[str, bool]:
    """Text without reasoning: whole <think> blocks, or everything before a bare </think>."""
    cleaned = _THINK_BLOCK.sub("", text)
    if "</think>" in cleaned:
        return cleaned.split("</think>")[-1].lstrip(), True
    return cleaned, cleaned != text


def _looks_like_reasoning(sentence: str) -> bool:
    return not re.search(r"[\u0900-\u097F]", sentence) and _INSTRUCTION.search(sentence) is not None

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
    # Mixed script, as Deepgram writes it: "मुझे इस बारे में call मत करो" (v17-stop S12 run 2)
    r"(?:call|phone|कॉल|फ़ोन|फोन) (?:मत|ना) (?:कर|कीज)|"
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


# "I will pay on 20th November, so stop calling me before that" is a promise with a condition, not
# a stop (v17 hold-out S30 run 1: the verification and the date given in the same turn were lost).
_UNTIL_THEN = _words(r"before (?:that|then)", r"till then", r"until then", r"until i pay", r"tab tak",
                     r"uske pehle", r"usse pehle", "तब तक", "उससे पहले", "उसके पहले")


def is_stop_request(text: str) -> bool:
    t = text.strip().replace("’", "'")
    if _STOP_STRONG.search(t) and not (_UNTIL_THEN.search(t) and not _PERMANENT.search(t)):
        return True
    # Sentence by sentence, as D-24 says: "8834. Aur suniye, is topic pe mujhe call mat karo." was not a
    # stop because the digits of the previous sentence counted as "not now" (v17-dev3 S12 run 2).
    for s in re.split(r"(?<=[.!?।])\s+", t):
        if _STOP_WEAK.search(s) and (_PERMANENT.search(s) or not _NOT_NOW.search(_NO_PAY.sub(" ", s))):
            return True
    return False


# The borrower asks to be called later ("call me back", "do ghante baad call kar lijiye").
_CALL_LATER = re.compile(
    r"call (?:me )?(?:back|later)|call back|(?:call|phone|fon|कॉल|फ़ोन|फोन) (?:kar|कर) (?:lijiye|lijie|lena|dena|"
    r"लीजिए|लीजिये|लेना|दीजिए|देना)|baad (?:me|mein|main) (?:call|baat)|बाद में (?:call|कॉल|फ़ोन|फोन|बात)",
    re.IGNORECASE)


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
        # Rupee amounts the agent may say once verified: the EMI, a charge already added, the policy (G-08).
        self._amounts = [borrower.emi_amount] + ([borrower.late_fee] if borrower.days_overdue and borrower.late_fee
                                                 else []) + list(policy.amounts().values())
        self._fee_added = bool(borrower.days_overdue and borrower.late_fee)
        self._pending_notes: list[str] = []
        self._dob_pending = False  # a partial date of birth was given; the next answer completes it
        self._last4_pending = False  # fewer than four digits were given ("It's double" | "7 4 3")
        self._attempts_at_turn_start = 0  # a verify result may be stated only if this went up
        self._muted = False  # rest of this reply dropped after an invented verify result
        self._bridges, self._bridge_noted = 0, False
        self._filler: str | None = None
        self._invited = False
        self._tool_failed = False
        self._bridge_counts: dict[str, int] = {}  # per tool, to rotate bridge lines over the call
        self._said_once: set[str] = set()  # apology / hand-off / contact question / warm close said
        self._once_this_turn: set[str] = set()  # ...of which in the reply being spoken now
        self._cut_chain: list[str] = []  # borrower fragments since the last reply they heard in full
        self._unheard = False  # the reply being cut now was cut within a second: not heard
        self._last_reply_cut = False  # the previous reply was cut by a barge-in
        self._asked_this_turn = False
        self.hardship_voice = False  # hardship heard: the bot slows the voice and waits longer (special.py)
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
        self._asked_this_turn = False  # in a special-situation step, a question ends the reply
        self._muted = False  # set when an invented verification result was replaced
        self._bridges, self._bridge_noted = 0, False  # bridge lines said this turn (no dead air)
        self._filler = None  # the slow-model filler bot.py had code say this turn (C47)
        self._invited = False  # this reply invited more questions: stop there and wait (C51)
        self._tool_failed = False  # a tool failed this turn: the model must say so, no code close (C57)
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
                    if not self._filler:  # "जी, एक second।" already covers the wait
                        line = context.hold(s.reply_language, self.script)
                        spoken.append(line)
                        held = 1
                        yield Sentence(line)
                    await asyncio.sleep(HOLD_RETRY_SECS)
            # A bridge line ("I understand.") is not a reply: the closing is still owed.
            if len(spoken) == held + self._bridges and (s.stage is Stage.CLOSING or "stop_requested" in s.flags):
                line = context.closing(s.reply_language, self.script, self._close_name())
                spoken.append(line)
                yield Sentence(line)
            finished = True
        except LLMError:
            s.flags.add("llm_failed")
            # Outcome already recorded: the call's work is done, so thank them instead of apologising.
            line = (context.closing(s.reply_language, self.script, self._close_name())
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
            turn, buf, held, said_in_round = None, "", None, False
            async for kind, value in self.llm.stream(self.system, self.history, TOOL_SCHEMAS):
                if kind == "turn":
                    turn = value
                    continue
                if kind == "tool_start":
                    if not spoken and not buf.strip() and not self._muted and bridge is None and not self._filler:
                        n = self._bridge_counts.get(value, 0)
                        self._bridge_counts[value] = n + 1
                        bridge = context.bridge(value, self.state.reply_language, self.script,
                                                special=bool(self.state.special), n=n)
                        self._bridges += 1
                        spoken.append(bridge)
                        yield Sentence(bridge, bridge=True)
                    continue
                buf += value
                if "think>" in buf:
                    buf, dropped = _strip_think(buf)
                    if dropped:
                        held = None  # the held sentence was reasoning
                        self.state.guard_hits.append({"category": "reasoning_dropped", "original": value,
                                                      "spoken": "", "stage": self.state.stage.value,
                                                      "verified": self.state.verified})
                    if "<think>" in buf:
                        continue  # still inside the reasoning: wait for its end
                *done, buf = _SENTENCE_END.split(buf)
                for sentence in done:
                    if held is None and not said_in_round and _looks_like_reasoning(sentence):
                        held = sentence
                        continue
                    for s_ in ([held] if held else []) + [sentence]:
                        if (out := self._guarded(s_)) and out.text not in spoken:
                            spoken.append(out.text)
                            said_in_round = True
                            yield out
                    held = None
            # A sentence already said this turn is not said again: the model wrote the closing,
            # called record_outcome, then wrote it again (v10 eval S04 run 2, S05 run 3).
            for s_ in [held, buf.strip()]:
                if s_ and (out := self._guarded(s_)) and out.text not in spoken:
                    spoken.append(out.text)
                    yield out
            for k in self.usage:
                self.usage[k] += turn.usage.get(k, 0)
            self.turns_by_provider[turn.provider] = self.turns_by_provider.get(turn.provider, 0) + 1
            for block in turn.content:  # the model must not see its leaked reasoning as said
                if block.get("type") == "text" and "think>" in block.get("text", ""):
                    block["text"] = _strip_think(block["text"])[0]
            self.history.append({"role": "assistant", "content": turn.content})
            if not turn.tool_calls:
                return
            results = [{"type": "tool_result", "tool_use_id": c.id,
                        "content": json.dumps(self._tool(c.name, c.input), ensure_ascii=False)}
                       for c in turn.tool_calls]
            self.history.append({"role": "user", "content": results})
            if self._plain_close(turn.tool_calls, results, spoken):
                return  # respond() says the code-owned closing line: no model round for fixed words
            if bridge and not self._bridge_noted:  # once per turn
                self.history.append({"role": "system", "content": context.note_bridge(bridge)})
                self._bridge_noted = True

    def _send_link_on_yes(self, user_text: str) -> None:
        """C48, live call B004 19:51: "जी भेज दीजिए" to "क्या link भेज दूँ SMS या WhatsApp पर?" got the
        channel question again, four times in the call. A plain yes to the link offer, naming no channel,
        sends it on WhatsApp now (owner's default); the model only says so."""
        s = self.state
        offered = "link" in s.last_agent.lower() and "?" in s.last_agent
        plain_yes = (bool(_YES.search(user_text)) or bool(_SEND_IT.search(user_text))) and "?" not in user_text
        if (not s.verified or s.payment_link_sent or not offered or not plain_yes
                or guard.names_channel(user_text) or _NEGATION.search(user_text)):
            return
        result = self.tools.call("send_payment_link", {"channel": "whatsapp"})
        if result.get("sent"):
            self._pending_notes.append(context.NOTE_LINK_SENT)

    def slow_filler(self) -> str | None:
        """A short line for bot.py to say when the model has said nothing yet this turn (C47).
        Once per turn; afterwards no tool bridge or hold line, which would say the same thing again."""
        if self._filler is not None:
            return None
        n = self._bridge_counts.get("_slow", 0)
        self._bridge_counts["_slow"] = n + 1
        self._filler = context.bridge("_slow", self.state.reply_language, self.script, n=n)
        return self._filler

    def _plain_close(self, calls: list, results: list[dict], spoken: list[str]) -> bool:
        """C45, live call B004 19:31: after record_outcome the model took one more request (0.4 s, more
        with retries) only to say the fixed goodbye. Skip it when nothing else is owed: the only call
        was record_outcome, its result carries no instruction (special flow, stop, payment check) and no
        promise date to read back, nothing but bridge lines was said this turn, and the borrower's last
        words were not a question (never close on one)."""
        s = self.state
        if len(calls) != 1 or calls[0].name != "record_outcome":
            return False
        # v17 hold-out S14b run 1: the retry of a failed link was announced ("Sure, I'm sending the link
        # now"), failed again, and the code close followed, so the borrower never heard it failed.
        if self._tool_failed:
            return False
        if json.loads(results[0]["content"]) != {"recorded": True}:
            return False
        if len(spoken) != self._bridges or (s.heard and s.heard[-1].rstrip().endswith("?")):
            return False
        s.flags.add("code_closed")
        return True

    def _close_name(self) -> str | None:
        """The borrower's name only once verified: before that the listener may be someone else (C55)."""
        return self._first_name if self.state.verified else None

    def _closing_line(self) -> Sentence:
        line = context.closing(self.state.reply_language, self.script, self._close_name())
        self._said_closing = True
        self.transcript.append(("agent", line))
        return Sentence(line)

    def _end_turn(self, spoken: list[str], finished: bool) -> None:
        s = self.state
        said = " ".join(([self._filler] if self._filler else []) + spoken)
        heard_it = bool(spoken) and (finished or not self._unheard)
        if s.special and heard_it:
            self._next_special_step()
        if not finished:
            # Interrupted: the reply never reached history, so record what was said and that it was cut (LLD 9).
            if self._unheard:
                self._said_once -= self._once_this_turn  # never heard, so not said (apology must come again)
                # Cut within a second of playing (live call 16:03: "मुझे सच में बहुत अफ़सोस है..." was cut
                # 250 ms in; the next reply skipped the apology because history said it was given).
                said = f"[Not heard: the borrower cut in at once, so they did not hear this reply: {said}]"
                self._pending_notes.append(context.NOTE_UNHEARD)
            else:
                said = f"{said} [cut off by the borrower]".strip()
            self.history.append({"role": "assistant", "content": said})
        self._last_reply_cut = not finished
        self._cut_chain = (self._cut_chain + [s.heard[-1]]) if (not finished and s.heard) else []
        self._unheard = False
        self._once_this_turn = set()
        self.transcript.append(("agent", said))
        if s.stage is Stage.REMINDING and s.verified:
            s.stage = Stage.INTENT
        s.last_agent = said
        # An invitation to ask more waits for the answer, even at the close (C51); a stop never waits.
        if (s.stage is Stage.CLOSING and not self._invited) or "stop_requested" in s.flags:
            self.should_hang_up = True
        if self.should_hang_up and finished and spoken:
            self._said_closing = True

    def _next_special_step(self) -> None:
        """The borrower heard this step, so the next one comes; after the close, the call ends.
        A reply cut at once was not heard, so its step is given again (live call 16:03)."""
        s = self.state
        if not special.is_last(s.special, s.special_step):
            s.special_step += 1
        elif "warm_close" in self._said_once or s.outcome is not None:
            self.should_hang_up = True

    def _add_turn_notes(self, user_text: str, lang_hint: str | None) -> None:
        s = self.state
        # Live call 16:03: a Hindi sentence split by barge-ins ended in an English fragment ("the EMI or
        # the late fee can be reduced?") and flipped the reply to English for one turn.
        lang_text = " ".join(self._cut_chain + [user_text])  # the whole sentence the barge-ins split
        content = language_words(lang_text)
        # A turn that was mostly numbers or months ("Buys, July 19 94.") says nothing about language.
        counts = content == lang_text.strip() or len(content.split()) >= 2
        if counts and self.lang.update(detect(content, lang_hint), len(content.split())):
            s.reply_language = self.lang.current
            s.languages_used = list(self.lang.used)
            facts = None
            if s.verified:
                facts = self.tools.facts()
                s.loan_facts = facts
                s.disclose(facts)
            self._pending_notes.append(context.note_language(s.reply_language, facts))
        asked = asks_numbers_in(user_text)
        if asked:
            # C44: the borrower named the language for numbers ("amount English mein bataiye"). Their
            # choice holds for the rest of the call: their own Hindi number words no longer flip it.
            s.flags.add("numbers_chosen")
        if asked or (s.numbers != "hindi" and "numbers_chosen" not in s.flags and says_hindi_numbers(user_text)):
            # Otherwise mirror the borrower: they said numbers in Hindi words, so the agent does too.
            changed = (asked or "hindi") != s.numbers
            s.numbers = asked or "hindi"
            if s.verified and s.reply_language != "en" and (changed or asked):
                facts = self.tools.facts()
                s.loan_facts = facts
                s.disclose(facts)
                self._pending_notes.append(context.note_numbers(facts, s.numbers, asked=bool(asked)))
        if not s.verified and _ASKS_PURPOSE.search(user_text):
            self._pending_notes.append(context.NOTE_PURPOSE.format(name=self._first_name))
        if is_stop_request(user_text):
            s.flags.add("stop_requested")
            self._pending_notes.append(context.note_stop(s.verified, s.loan_facts))
        else:
            self._send_link_on_yes(user_text)
        elapsed = self._clock() - self._started
        if elapsed > self.config["behavior"]["max_call_secs"] and "time_up" not in s.flags:
            s.flags.add("time_up")
            self._pending_notes.append(context.NOTE_TIME_UP)
        self._code_verify()
        if self._cut_chain:  # the borrower's sentence was split by barge-ins (live call B004)
            self._pending_notes.append(context.note_split(lang_text))
        if special.hardship_cue(lang_text) or s.special == "hardship":
            self.hardship_voice = True
        if s.special and not {"stop_requested", "time_up"} & s.flags:
            self._pending_notes.append(special.instruction(s.special, s.special_step, self._said_once))

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
        result = self.tools.call(name, args)
        if isinstance(result, dict) and result.get("error") == "tool_failed":
            self._tool_failed = True
        return result

    def _guarded(self, sentence: str) -> Sentence | None:
        """The sentence as it will be spoken, or None to drop it."""
        s = self.state
        if self._muted:
            return None
        if s.special and self._asked_this_turn:
            # A person stops after a question: "Is this short-term? ... They will arrange a call back."
            # crammed the next step into this one (v16-special3 S03 run 1, S04 run 1).
            s.guard_hits.append({"category": "after_question", "original": sentence, "spoken": "",
                                 "stage": s.stage.value, "verified": s.verified})
            return None
        if self._invited:
            # C51, live call B004 20:13: "यदि कोई और सवाल हो तो बताइए।" then the goodbye at once and a
            # hang-up. A person stops after asking and waits for the answer.
            s.guard_hits.append({"category": "after_invite", "original": sentence, "spoken": "",
                                 "stage": s.stage.value, "verified": s.verified})
            return None
        upset = any(f in s.flags for f in ("escalate:hardship", "escalate:dispute"))  # v14 special situations
        once = guard.once_kind(sentence)  # apology, hand-off, contact question, warm close: once per call
        if ((upset and guard.anything_else(sentence)) or guard.unwanted(sentence)
                or (once and once in self._said_once)):
            s.guard_hits.append({"category": "dropped_filler", "original": sentence, "spoken": "",
                                 "stage": s.stage.value, "verified": s.verified})
            return None
        # After the drops above, so a dropped invitation never makes the call wait (C51).
        if guard.invites_more(sentence) and not s.special and not {"stop_requested", "time_up"} & s.flags:
            if "invited_more" in s.flags:  # once per call: a second invitation would loop
                s.guard_hits.append({"category": "invite_again", "original": sentence, "spoken": "",
                                     "stage": s.stage.value, "verified": s.verified})
                return None
            s.flags.add("invited_more")
            self._invited = True
        if guard.asks_channel(sentence) and (s.payment_link_sent or guard.asks_channel(s.last_agent)):
            # C48: once is enough; after the link went, never.
            s.guard_hits.append({"category": "channel_again", "original": sentence, "spoken": "",
                                 "stage": s.stage.value, "verified": s.verified})
            return None
        if once:
            self._said_once.add(once)
            self._once_this_turn.add(once)
        if guard.cant_change(sentence):  # tracked so the flow can say "already said" (never dropped)
            self._said_once.add("cant_change")
            self._once_this_turn.add("cant_change")
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
        if s.verified and (line := self._money_check(sentence)):
            return line
        r = guard.check(sentence, verified=s.verified, protected=self._protected,
                        lang=s.reply_language, script=self.script, heard=s.heard)
        if r.category and guard.handoff(r.text):  # the safe line carries the hand-off: once per call
            if "handoff" in self._said_once:
                r = guard.GuardResult(guard.safe_line("change_short", s.reply_language, self.script), r.category)
            self._said_once.add("handoff")
            self._once_this_turn.add("handoff")
        if r.category is not None:
            s.guard_hits.append({"category": r.category, "original": sentence, "spoken": r.text,
                                 "stage": s.stage.value, "verified": s.verified})
            self._pending_notes.append(context.note_guard(r.text))
        text, hits = speakable.prepare(r.text, s.reply_language, self.script, s.numbers,
                                       self._fact_phrases())
        if hits:
            s.speech_hits.append({"hits": hits, "original": r.text, "spoken": text,
                                  "lang": s.reply_language})
        if s.special and text.rstrip().endswith("?"):
            self._asked_this_turn = True
        return Sentence(text, r.category, speech=tuple(hits))  # keyword: a positional third arg was "bridge"

    def _money_check(self, sentence: str) -> Sentence | None:
        """G-08: a late charge said to be added when none was, or a rupee amount code never gave."""
        s = self.state
        if not self._fee_added and guard.fee_added_claim(sentence):
            fee = amount_to_words(policy.amounts()["late_fee"], self.tools._nlang, self.script)
            line, category = context.fee_not_added(s.reply_language, self.script, fee), "fee_not_added"
        elif guard.unknown_amount(sentence, set(self._amounts) | {n for u in s.heard for n in guard.amounts_in(u)}):
            line, category = context.no_amount(s.reply_language, self.script), "unknown_amount"
        else:
            return None
        s.guard_hits.append({"category": category, "original": sentence, "spoken": line,
                             "stage": s.stage.value, "verified": s.verified})
        self._pending_notes.append(context.note_guard(line))
        return Sentence(line, category)

    def reply_unheard(self) -> None:
        """The bot cut the agent within a second of playing: the borrower did not hear the reply."""
        self._unheard = True

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
        b, today = self.tools.borrower, self.tools.today
        hindi = loan_facts(b, today, "hi", "devanagari").as_dict()
        spoken = self.tools.facts().as_dict()
        phrases = {}
        # C44, live call B004 19:31: "EMI eighteen thousand rupees है, जो आठवीं November को due है":
        # the amount was mapped, the model's ordinal date was not. Every Hindi form of the due date
        # (longest first) maps to the tools' phrase. Not for kal/parson: those stay as they are.
        if not 0 <= (b.due_date - today).days <= 2:  # date_to_words says aaj/kal/parson only for these
            phrases = {form: spoken["due_date_spoken"] for form in hindi_date_forms(b.due_date)}
        phrases.update({hindi[k]: spoken[k] for k in hindi if hindi[k] and spoken.get(k) and hindi[k] != spoken[k]})
        return phrases or None

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
            # v17 hold-out S24 x3: "driving hoon, do ghante baad call kar lijiye" got "I'll call you back in
            # two hours" in words, but no tool was called. The borrower's request is the outcome.
            asked = next((u for u in reversed(s.heard) if _CALL_LATER.search(u)), None)
            if fallback is Outcome.unresolved and asked and not s.callback_time:
                fallback = Outcome.callback_requested
                when = dates.normalize(asked, self.tools.today, s.reply_language)
                if when.date:
                    s.callback_time = f"{when.date.isoformat()} ({asked})"
            outcome, promise, notes = fallback, None, "recorded by code: model did not record an outcome"
        else:
            # Rebuild so actions taken after record_outcome (e.g. a link sent) are reflected.
            outcome, promise, notes = s.outcome.outcome, s.outcome.promise_to_pay_date, s.outcome.notes
        s.outcome = self.tools.build_record(outcome, promise, notes)
        append_record(s.outcome, self.calls_path)
        s.stage = Stage.ENDED
        self.should_hang_up = True
        return s.outcome
