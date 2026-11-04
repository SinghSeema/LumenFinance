# Low-Level Design

Contracts for the parts where mistakes are costly: state, gating, tools, formatting, dates, outcome schema, config, errors and instrumentation. Everything else stays plain code. Architecture is in `HLD.md`; IDs refer to `requirements.md`.

## 1. Brain interface (shared by voice and eval paths)

```python
class Brain:
    def __init__(self, config, borrower, today, llm, calls_path=None): ...
    async def respond(self, user_text: str, lang_hint: str | None) -> AsyncIterator[Sentence]: ...
    def finish(self) -> OutcomeRecord: ...   # validate, write calls.jsonl, stage ENDED
    state: CallState
    should_hang_up: bool                     # set by code after CLOSING or a stop request
```

- `respond` yields sentences as they are ready (for streaming TTS) and runs tool calls internally (at most 4 tool rounds per borrower turn). `Sentence` carries the guard category if the model's sentence was replaced.
- If the model never records an outcome, `finish` records `stop_requested` (after a stop request) or `unresolved`.
- It has no audio code. The Pipecat processor and the eval runner both call it; swapping the framework touches only that wrapper.

## 2. Call state

```python
class Stage(Enum): GREETING, VERIFYING, REMINDING, INTENT, ACTION, CLOSING, ENDED

class CallState:
    borrower_id: str
    stage: Stage
    verified: bool = False
    verify_attempts: int = 0
    reply_language: str          # "en" | "hi" | "hinglish"
    loan_facts: LoanFacts | None # None until verified
    outcome: OutcomeDraft
    disclosure_log: list[Disclosure]  # field, timestamp, stage
    flags: set[str]
```

Transitions are made by code:

| From | To | Trigger |
| --- | --- | --- |
| GREETING | VERIFYING | Borrower confirms they are the named person |
| GREETING | CLOSING | Wrong number, or a third party says the person is unavailable |
| VERIFYING | REMINDING | `verify_identity` returns verified |
| VERIFYING | CLOSING | Attempts exhausted (3), outcome `verification_failed` |
| REMINDING | INTENT | Reminder delivered |
| INTENT | ACTION | Intent understood |
| ACTION | CLOSING | Link sent, callback set, or escalation flagged |
| any | CLOSING | Stop requested (G-06), max call length reached, or unrecoverable error |
| CLOSING | ENDED | Outcome record validated and written |

## 3. Context assembly (G-01, G-02)

`context.py` is the only place model input text is built. The context is **append-only** (D-18): Haiku 5.5 rejects replayed thinking blocks if the system prompt, the tool list or any earlier message changes.

- **System prompt (fixed for the whole call):** the rules, the starting reply language, the script rule, the lender name, the borrower's first name and ID, and the greeting already spoken. Nothing about the loan, ever.
- **Tool list (fixed for the whole call):** all six tools are declared from the start; gated tools are refused by code at call time (section 4).
- **Loan facts:** enter the context only as the result of a successful `verify_identity` call, as a `LoanFacts` block of pre-spoken strings. `get_loan_details` returns the same strings if the model asks again.
- **Mid-call changes** are appended as a `role: system` note right after the borrower's message, never edited in: language switch (with the facts re-spoken in the new language if verified), stop request, guard hit ("your last sentence was not spoken; the borrower heard..."), time limit.
- `LoanFacts` fields: `amount_spoken`, `due_date_spoken`, `overdue_spoken` (optional), `loan_type`. The model never receives raw numbers it must format.
- Each field that enters the context is appended to `disclosure_log` with its stage and verified flag.

## 4. Tools

| Tool | Callable before verification | Behavior |
| --- | --- | --- |
| `verify_identity(factor, value)` | Yes | `factor` is `dob` or `last4`. Code compares with stored data and returns `{verified, attempts_left}`, plus `loan_facts` on success. The model never sees the stored answer. Fixed refusals, no hints (G-11). A value code cannot read (not a date, not four digits) returns `unreadable_value` and does not use an attempt. A readable value the borrower has not said in this call returns `not_heard` and does not use an attempt (D-23): the last four must equal a whole number the borrower said (digits said one at a time join into one number; neighbouring numbers join only within one utterance), and a date of birth needs its day, month and year each said, in digits or English or Hindi number words (`heard.py`). This check runs before the stored value is compared, so the reply is identical for right and wrong values (G-11). The first call moves GREETING to VERIFYING. |
| `get_loan_details()` | No | Refuses with `not_verified` unless `state.verified`. Returns the `LoanFacts` strings. |
| `send_payment_link(channel)` | No | Mock. `channel` is `sms`, `whatsapp` or `email`. Returns a masked `.example` URL and sets `payment_link_sent`. Fault injection `faults.payment_link_fail`: `none`, `once` (the retry succeeds) or `always`. |
| `schedule_callback(when_text)` | Yes | `when_text` is the borrower's own words. Runs the date normalizer; returns the spoken date or `ambiguous`. Stores `callback_time` as `"<ISO date> (<borrower's words>)"`. Reveals no loan detail. |
| `escalate_to_human(reason)` | Yes | `reason` is `dispute`, `hardship`, `requested` or `other`. Sets `escalate_to_human`. |
| `record_outcome(outcome, promise_to_pay_date, notes)` | Yes | `promise_to_pay_date` is the borrower's own words (D-19); code normalizes it and returns `date_unclear` if ambiguous. Validates against the schema; code merges with state; moves to CLOSING. A `verification_failed` outcome (recorded by code) cannot be overwritten. The model may not record `verification_failed` (`code_only_outcome`; only code records it after three failed checks), nor `stop_requested` unless code detected a stop request (`no_stop_request`) (D-23). |

Tool schemas use `strict: true` (all fields required, nullable where optional). Every call is logged in `state.tool_calls` with arguments, result, attempts, duration and stage.

Tool call rules:
- Every tool call is timed. If one takes more than `behavior.filler_delay_ms` (default 300), a cached filler line plays.
- Timeout `behavior.tool_timeout_s`; one retry; then the failure path in section 11.
- A call to a gated tool returns a structured refusal; the refusal text is fixed, so the model cannot learn anything from it.

## 5. Outcome schema

`will_pay`: the borrower agrees to pay but names no date. `promise_to_pay`: the borrower names a specific date, including the due date itself ("by parso" when parso is the due date), and the record carries that date. Defined 2026-11-03 after S09 recorded a due-date promise as `promise_to_pay` and the scenario expected `will_pay`.

```python
class Outcome(str, Enum):
    will_pay, already_paid, promise_to_pay, hardship, dispute, wrong_number,
    third_party, callback_requested, stop_requested, verification_failed, unresolved

class OutcomeRecord(BaseModel):
    borrower_id: str
    verified: bool
    outcome: Outcome
    promise_to_pay_date: date | None
    payment_link_sent: bool
    escalate_to_human: bool
    notes: str
    language_used: str
    callback_time: str | None
```

Code-enforced consistency (G-05, G-12):
- `outcome` in {`hardship`, `dispute`} forces `escalate_to_human = True`.
- `promise_to_pay_date` is required iff `outcome == promise_to_pay`, and must be after today ("I'll pay today" is `will_pay`).
- `language_used` lists the reply languages in order, joined by `+` (for example `en+hinglish`).
- `verified = False` forbids `will_pay`, `already_paid` and `promise_to_pay`.
- `verified = True` forbids `wrong_number`, `third_party` and `verification_failed`.
- `callback_requested` requires `callback_time` (call `schedule_callback` first).
- One JSON line per call is appended to `results/calls.jsonl`.

## 6. Spoken formatting (`speech_format.py`)

| Function | Behavior |
| --- | --- |
| `amount_to_words(amount, lang)` | 12500 becomes "twelve thousand five hundred rupees"; larger amounts use lakh and crore ("one lakh twenty thousand rupees"). Hindi: Devanagari or Roman per `tts.script`. |
| `date_to_words(date, today, lang)` | "the fifth of November"; "tomorrow" and "day after tomorrow" when applicable; Hindi equivalent. Omits the year unless it differs from today's. |
| `digits_to_words(s, lang)` | Last-four read-backs, one digit at a time with pauses. |

Rules: tools return pre-spoken strings; the model is told never to restate numbers in digits. Unit tests cover rounding edge cases, 100000 and above, and each language.
- Amounts round half up to the nearest rupee. Hinglish uses English number words (provisional, D-11).
- "Today", "tomorrow" and "day after tomorrow" (aaj, kal, parson) are used only for the next two days; past dates always get the full form, because "kal" also means yesterday.
- **Digit backstop (D-21):** just before TTS, any digits or currency symbols the model still wrote are expanded with these functions and counted as a speaking-style eval signal.

## 7. Date normalizer (`dates.py`)

`normalize(text, today, lang) -> {date | None, ambiguous: bool, reason}`

- Resolves: today, tomorrow, "kal", "parso" (taken as the day after tomorrow when the borrower is promising a future date), weekday names, "next Friday", day-of-month, "this month's last day".
- Returns `ambiguous = True` for "month end", "when salary comes", "soon". The agent must ask one clarifying question before any date is recorded (G-08).
- Never returns a past date for a promise; asks again instead.
- A plain weekday is the next one after today ("Tuesday" said on a Tuesday is a week away); "next Friday" is the Friday of next week.
- A day without a month that has already passed this month means next month. A named month earlier this year means next year only if it is more than 60 days back ("5 Jan" said in November); otherwise it is `past_date`.
- A bare number is a day only with an ordinal, "tarikh", or "on/the" before it, so amounts are never read as dates.
- Ambiguity words win over everything else ("Friday if salary comes" is ambiguous). `reason` is `ok`, `ambiguous_phrase`, `unrecognized`, `past_date` or `invalid_date`.

## 8. Language policy

- STT supplies a language tag; code also inspects the script of the transcript.
- `reply_language` is set per turn and passed in the prompt: reply in the borrower's latest language; Hinglish allowed.
- A single English word inside a Hindi turn does not flip the language; two consecutive turns in the other language do. Moves between Hindi and Hinglish are immediate.
- Fixed cached lines (greeting, fillers, closing) exist in both languages, in one grammatical gender matching the TTS voice (D-20).
- **Script of Hindi text sent to TTS** is `tts.script`: `roman`, `devanagari`, or `mixed` (Hindi in Devanagari, English words in Latin). The prompt's script rule and the formatter follow it. Chosen by listening test (D-11).

## 9. Turn-taking and timers

| Item | Setting (config key) |
| --- | --- |
| End-of-speech silence | `turn.vad_stop_secs` |
| End-of-speech silence while digits or a date are expected (verification, date questions), switched by code via `VADParamsUpdateFrame` | `turn.vad_stop_secs_digits` (D-17) |
| Speech start | `turn.vad_start_secs` |
| Words needed to interrupt | `turn.min_words_interrupt` |
| Backchannel handling | `turn.backchannel_filter`: `context_aware` ignores "ok" or "haan" only when no question is pending, never during verification |
| Silence prompts | `turn.silence_prompts_secs`, then `turn.silence_close_secs` |
| Maximum call length | `behavior.max_call_secs` |

Barge-in cancels queued TTS sentences and discards unspoken text; the interrupted turn is kept in history with a marker that it was cut off.

## 10. Output guard

Runs per sentence before TTS, with a budget of a few milliseconds.
- Unverified: block amounts, dates, loan-type words, `₹`, and the raw EMI amount (so a guessed amount is never echoed). Digits are allowed only as times of day (`6 baje`, `6 pm`, `10:30`) or when each number exactly equals a whole number the borrower said earlier in the call (read-back, Q-07; "4 8 2 1" and "4821" are the same number, "98" is not part of "1988"); a month name is allowed only if the borrower said it (date-of-birth read-back). D-22.
- Always: block threat patterns and waiver, discount or restructuring promises.
- A block replaces the sentence with a cached safe line and logs the event. Guard hits are an eval metric, not a silent fix.

## 11. Error handling

| Failure | Response |
| --- | --- |
| Empty or very low-confidence STT | Ask to repeat once; then offer a callback |
| Digits or dates unclear | Read back and ask to confirm; never guess |
| Tool timeout or error | Filler, one retry, then a graceful line and a callback or escalation |
| LLM error or timeout | Fail over to the next model in `llm.fallback`; if all fail, a cached apology and callback |
| TTS error | Fail over to the fallback TTS; if none, end the call with a recorded outcome `unresolved` |
| Caller silent | Prompt twice, then close and record a callback note (Pipecat `UserIdleProcessor`) |
| Model refusal (`stop_reason: refusal`) or tool input truncated | Treated as an LLM error: fail over to the next model |

## 12. Config

Layers: `configs/defaults.yaml`, then a profile, then environment variables, then CLI flags (`--set section.key=value`). Unknown keys are errors. The resolved config, without secrets, is logged at the start of every call and stored beside every eval result. Guardrails are not switchable in call profiles; an ablation flag exists only in eval tooling.

Sections: `stt`, `tts`, `llm`, `turn`, `behavior`, `evals`, `faults`. Example profile: `configs/profiles/hindi_bulbul.yaml`.

## 13. Instrumentation (`latency.py`)

Per turn, record monotonic timestamps: `vad_stop`, `stt_final`, `llm_first_token`, `tts_first_audio`, `playback_start`. Derived: response gap (`vad_stop` to `playback_start`) and per-stage durations. Also log tool call durations, guard hits, token counts and the disclosure log. Reports give median and p90 over at least 30 voice turns.

## 14. Testing

Unit tests, written before the code they cover: gating and `build_context`, the disclosure log, the formatter, the date normalizer, the schema consistency rules, the output guard patterns. The eval harness (`evals/run.py`) drives `Brain.respond()` directly, the same entry point voice uses; the Brain has no eval-specific code.

## 15. Mock data schema

`data/borrowers.json`: a list of records validated by a Pydantic model at load.

```python
class Borrower(BaseModel):
    borrower_id: str
    name: str
    dob: date
    last4: str                 # last four digits of registered number
    loan_type: str             # e.g. "personal loan", "two-wheeler loan"
    emi_amount: int            # rupees
    due_date: date
    days_overdue: int = 0
    preferred_language: Literal["en", "hi", "hinglish"]
```

`preferred_language` sets the language of the first line only; after that the reply follows the borrower's latest language. Stored `dob` and `last4` never enter model context.

## 16. Prompt contract (speaking style)

The system prompt is versioned in `src/emi_agent/prompts/` and must state:
- Role: Lumen Finance voice assistant, polite, warm, firm about the reminder, never pressuring.
- Speech rules: one to two short sentences per turn; one question at a time; no lists, markdown or digits for money and dates (use the pre-spoken strings); contractions and everyday words; acknowledge before asking; do not repeat the same phrase twice in a row.
- Language: reply in the language given by `reply_language`; Hinglish is fine.
- Rules in plain words mirroring G-01 to G-10, and the instruction to call tools rather than guess.
- The prompt never contains stored verification answers or loan data before verification.
