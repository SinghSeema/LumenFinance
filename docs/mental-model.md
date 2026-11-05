# Mental Model: Evals, Rules and the LLM

How the Lumen Finance EMI agent decides what it says, and how it is evaluated. Checked against the code on branch `testing` (5 Nov 2026); file:line references are to that branch. Use it to explain the system aloud.

## In 60 seconds

> The model talks; code rules. Pipecat and LiveKit carry the audio, Deepgram turns speech into text, and a code layer decides everything that matters before and after the model speaks. Before the model runs, code checks identity itself, detects stop requests, tracks the language, and tells the model the exact step it is on in a hardship or dispute. The model (Sarvam 105b) sees loan facts only after verification, and only as ready-to-speak phrases, so it never formats a number. Every tool call passes code gates: no loan tools before verification, verification only on values the borrower actually said, outcomes validated against a schema and business rules. Every sentence the model writes passes an output guard that drops or replaces threats, waiver promises, invented amounts, invented verification results and early disclosures, before Sarvam TTS speaks it. The evals run the same Brain against an LLM borrower; pass or fail is decided by rules only, and an LLM judge, calibrated against my labels, scores tone only.

**Analogy.** The LLM is a fluent, warm new call-centre hire on a headset. Code is the supervisor beside them with the customer file and a mute button. The supervisor checks the caller's ID before opening the file, hands over cue cards with the amounts already written in words, slides over one card per turn in a difficult call ("now acknowledge, now ask one question"), mutes any sentence that promises a waiver or threatens and says a safe line instead, and checks the call log before filing it. The new hire picks the words; the supervisor owns the policy.

```
 EARS        Deepgram STT · VAD · unfinished-turn wait · barge-in rules          [code, bot.py]
   │
 UNDERSTAND  stop detector · language tracker · code verification ·              [code, brain._add_turn_notes]
   │         link-on-yes · purpose answer · special-step instruction ──► notes
   │
 LANGUAGE    Sarvam-105b: the words, and which tool to call                      [LLM]
   │   ▲         │ tool calls
   │   │         ▼
 RULES     tool gates · heard-value check · escalation gates (word lists, then  [code, tools.py]
   │       an LLM intent label; fail-open) · record_outcome rules · schema
   │
 GUARD       per sentence: drop / replace / mute (threat, promise, amount,        [code, guard.py, brain._guarded]
   │         disclosure, invented verification result, once-per-call lines)
   │
 SPEAKABLE   digits to words, script fixes, URL and emoji stripped               [code, speakable.py]
   │
 MOUTH       bridges, fillers, fixed closing lines (code) + Sarvam Bulbul TTS    [code + TTS]
   │
 RECORD      finish(): fallback outcome, validated OutcomeRecord, calls.jsonl     [code]
```

Every layer except LANGUAGE, and the intent label inside RULES, is deterministic. "The LLM proposes, code disposes."

## Which LLM does what

| Role | Model | Settings | Config / code |
|---|---|---|---|
| Agent (voice and evals) | `sarvam:sarvam-105b-conversations` (OpenAI-compatible API) | temperature 0.3, max_tokens 1024; voice: 2.5 s timeout, no retries (`bot.py:48`); evals: 8 retries, no fallback (`run.py:46`) | `llm.*` |
| Voice fallbacks | `groq:openai/gpt-oss-20b`, then `gpt-oss-120b` | used only if the primary fails before any text streamed (`FallbackLLM`, `llm.py`) | `llm.fallback` |
| Escalation intent check | `sarvam:sarvam-105b-conversations`, its own narrow prompt | temperature 0, 1.5 s limit, no fallback (`intent.py`) | `behavior.intent_check_*` |
| Borrower simulator (evals only) | `sarvam:glm5.3-flash` | temperature 0.8 | `evals.simulator_*` |
| Tone judge (evals only) | `sarvam:glm5.3-flash` | temperature 0 | `evals.judge_*` |

The simulator and the judge are the same model, a different family from the agent. Expect that question.

When the agent model fails mid-call: a hold line (unless a filler already covered the wait), wait 1 s, retry once; then the apology (or the closing line if an outcome is recorded) and hang up, never repeating words already said (`brain.py`, `respond`).

## Rules vs LLM, component by component

| Component | Code decides | The LLM decides | How code constrains it |
|---|---|---|---|
| Call stages (`state.py`) | Every transition: greeting, verifying, reminding, intent, action, closing, ended | Nothing | No tool sets a stage |
| Verification | `_code_verify` reads what the agent asked, extracts the value from the borrower's words (`heard.py`), joins halves across turns, holds a partial date of birth, calls `verify_identity` itself; attempts counted only on a real mismatch, at most 3; `verification_failed` recorded by code | How to ask | The model's own value is checked only if code read that exact value from the borrower (`not_heard`); a date code found incomplete is blocked |
| What the model sees | No loan facts before verification (prompt has name, greeting, policy); after it, facts as pre-spoken phrases | Nothing | — |
| Tools | `get_loan_details`, `send_payment_link`, `get_policy` refused before verification; strict argument schema; one retry, then a fixed failure | Which tool, which arguments | Per-tool rules below |
| Output guard | Per sentence: invented verification result (replace and mute the rest), money check (unknown amount, a fee claimed on a loan with none), threat, waiver promise (read-backs of the borrower's own request allowed), early disclosure (digits, money words, unheard months, protected phrases; a plain refusal allowed), invented notices, once-per-call lines, one question per flow step | The sentence | Each replacement is logged and noted in history |
| Numbers and dates | Spoken by code in the call's language and number style; leftover digits turned into words before TTS; the borrower's own date words resolved against a fixed today (or "ambiguous", "past", "conflict") | Says the phrases | The model never gets a raw number |
| Language | `LanguageTracker`: switch at once on Hindi/Hinglish or 3+ Devanagari words; a short turn needs two in a row; facts re-spoken in the new language | Writes in it | — |
| Stop | `is_stop_request`: strong phrases, or weak ones without "not now" words, sentence by sentence; a conditional stop is not a stop; "band karo" only when short or about calls | Words the one-turn close | `stop_requested` refused unless the flag is set |
| Special flows (`special.py`) | Which flow (hardship, wrong amount, not mine, waiver); one step per turn; a step moves on only if heard, and is undone if its audio was cut; `record_outcome` refused before the last step; hardship slows the voice | Words each step; whether to escalate at all | Text after the step's question is dropped |
| Escalation gates | Explicit request needs a person word plus a request word, or a yes to the offer; hardship or dispute confirmed by word lists, else by the LLM intent check; a timeout or failure lets it through | Proposes the escalation; the intent model labels it | Fixed label set; digits hidden; only borrower lines sent |
| Outcome | Already-paid needs past-tense paid words; a vague time asks once; "by/before the due date" means the due date; already-paid on an overdue loan becomes a team payment check; pydantic schema; `finish()` fills in the outcome if the model forgot | Proposes outcome and notes | Refusals come back with guidance |
| Closing | Fixed closing line when nothing else is owed (not after hardship or a dispute); a garbled model goodbye replaced; an "anything else?" waits for the answer | Words the close in hard calls | — |
| Fillers and bridges | Bridge line when a tool starts with nothing said; filler at 1.2 s, a second at +2 s; silence nudge at 8 s, polite close 10 s later | Nothing | The model is told what was already said |
| Turn-taking | VAD 0.5 s; wait for a pending transcript; longer wait if the words sound unfinished (longer in hardship); barge-in needs speech plus 2 words; cut within 1 s = not heard | Nothing | — |

**Rough split.** By lines in `src/emi_agent/*.py` (4,070 non-blank, non-comment): deterministic call logic about 67%, LLM plumbing (`llm.py`, `intent.py`) about 8%, voice and infrastructure about 25%. About 140 regex or word-list sites hold most of the rule knowledge. By authority, the more honest framing: the LLM writes almost every sentence and makes the first call on intent and tool choice; code has the final say on every safety- or record-relevant decision. The share of spoken sentences written by code has not been measured.

## One turn, from transcript to audio

1. `bot.py` decides the turn is over (VAD stop, pending transcript, unfinished-sentence wait), starts the reply and the filler timer.
2. `Brain.respond` records the utterance.
3. `_add_turn_notes`, before the model runs: language and number-style notes, purpose answer, stop detection or link-on-yes, time limit, code verification, split-sentence note, hardship voice, the current flow step. All go into history as one system message.
4. `_rounds`, up to 4 model requests: a bridge line when a tool starts; each sentence through `_guarded` (drop, replace, mute) and `speakable`; tool calls through the intent check (when needed), the date-of-birth block and `Tools.call` (gates and rules); the plain close may end the turn without another model request.
5. `_end_turn`: the flow step moves on only if heard; a cut reply is recorded as cut; hang-up decided.
6. Bot `send`: cleaned text to TTS (slower voice once hardship was heard); after the closing turn, wait for quiet, end the call; `finish()` validates and writes the outcome.

**Verification turn.** Agent asked for the last four digits; the borrower says "चार आठ दो एक". Code reads 4821, calls `verify_identity` itself; on a match the model gets "verified, loan facts to say as written: …" and one request writes the reminder; on a mismatch one attempt is used and the note says ask the date of birth, never which part failed; "last chance" with two left is replaced and the rest muted.

**Hardship turn.** "I lost my job last month, I can't pay right now." The word list matches, the voice slows; the model calls `escalate_to_human(hardship)` (bridge "I hear you." plays); code starts the hardship flow and returns step 1: one plain line, the facts (one-time charge, part payment goes towards the EMI, the team can check options), one question. The guard drops anything after the question and any waiver hint. Steps 2 and 3: read back, hand-off, contact question; then answer, close, record. With no listed words ("ghar mein haath tang hai") the LLM intent check decides in about half a second; a timeout lets the escalation through.

## The evaluation process

**Scenario YAML** (`evals/scenarios/`, 31 files): `id`, `name`, `borrower_id`, a fixed `today`, `persona`, `opening`, `hidden_goal` (only the simulator sees these), optional `knows_identity` (wrong number, family member), `faults` (for example a failing payment link), `expected` (verified, outcome or accepted outcomes, escalation, link sent, language, promise date, callback date), optional `process_checks`, `covers` (requirement IDs), `runs`, `set`. Sets: 16 dev, 5 original hold-out (fixed against since), 10 stress (S21 to S30; only their first run, 24/30, counts as clean).

**Runner** (`evals/run.py`): a fresh Brain per conversation (the same class as the voice bot) with a simulated clock; up to 16 turns of simulator reply then agent reply; ends on a hang-up, the agent ending the call, or the turn limit; any exception is saved as an infra error; `finish()` always runs. Each conversation is saved in full: turns, tool calls (with who made them: model, code, blocked), the outcome, the disclosure log, guard and speech fixes, tokens per model, end reason. Cost is estimated before a run and printed after; `--resume` finishes a cut run.

**Scoring** (`evals/score.py`, rules only): a pass needs every expected field of the outcome record plus process checks (says it is an AI when asked; no invented link after a tool failure; "what is this about?" answered without loan detail; one clear question on a vague date where the scenario asks for it). Reported separately: leaks before verification (zero tolerance: the disclosure log plus a scan of every unverified agent turn for this borrower's protected phrases), escalation precision and recall, language mismatches, turns. Infra errors are excluded from the pass count; a simulator that goes off its script still counts as a fail and is marked "test" in the hand review.

**Judge** (`evals/judge.py`): `glm5.3-flash`, temperature 0, rubric v3. The prompt first lists what is correct and must never lower a score (refusing details before verification, a quick close on stop, no waiver, the internal-team hand-off, saying it is an AI, a factual credit-score answer only when asked). Five scores, 1 to 5 or n/a: politeness, empathy (n/a if no emotion), firmness and clarity (n/a if no reminder could be given), naturalness. Flags (threat, shaming or pressure, false promise, off-topic) must quote the agent's exact words; a quote not found in the transcript is dropped.

**Calibration** (`evals/calibrate.py`): judges compared with the owner's hand labels on 9 conversations: glm5.3-flash 78% within one point (best), gpt-oss-120b 73%; exact agreement only 11 to 44%; the judge is lenient (clarity +1.17, naturalness +1.11). Judge scores are reported with that offset and never decide a pass.

**Hand review** (`evals/failure_review.yaml`): every failed conversation is read and marked agent, code or test; expected outcomes are never changed after seeing results.

## Strengths and risks

**Strongest**
1. Disclosure safety is structural: no facts before verification, gated tools, verification only on heard values, then the guard. 0 leaks in every run.
2. Numbers and dates belong to code (vague dates went 0/5 to 5/5).
3. Honest evals: rules decide passes, a clean stress run (24/30 before fixes), every failure read and labelled, the judge calibrated and offset, contamination admitted.
4. Hard situations are a code-owned step machine; an LLM fallback understands unknown phrasing and fails open.
5. Every defect has a test from the exact words (1095 tests).

**Weakest**
1. About 140 word-list sites: brittle on new phrasing and prone to false positives (C59, C65, C66, C70, C75). A threat is replaced by the "I can't make changes, our team will call back" line, which does not fit a threat.
2. Latency tail: p90 2527 ms against the 1.8 s target; Sarvam timeouts (C69).
3. Eval limits: 2 to 3 runs per scenario, judge calibrated on 9 labels, simulator and judge the same model, text-level only.
4. Tone: empathy 3.0 to 3.6; naturalness about 2.9 after the offset.
5. Open: the hedged-answer verification loop (C34); `brain.py` is about 800 lines with many interacting flags.

## Likely questions

1. **Why not let the LLM verify identity?** The most important rule cannot depend on a model's compliance. Code reads the value, compares it, counts attempts and gates the tools; the model cannot even pass a value the borrower did not say.
2. **Is 0 leaks real, or is the guard just strict?** Both. Structurally the model has no facts before verification. The guard was over-strict (all 6 replacements in the stress run were refusals, C59); the testing branch lets plain refusals through and still replaces anything with a fact.
3. **How far do you trust the judge?** Not much: 78% within one point on 9 labels, about one point lenient on two dimensions. It never decides a pass; it compares versions and finds the worst lines.
4. **What if Sarvam fails mid-call?** 2.5 s timeout, the Groq fallback if nothing was spoken yet, fillers at 1.2 s and +2 s, a hold line and one retry, then an apology and a recorded outcome.
5. **An unseen borrower says hardship in words none of your lists know?** The agent model still understands and proposes the escalation; the intent check labels it from the borrower's lines only; a failed check lets it through. Safety does not depend on recognising the situation, only the quality of the flow does.

## Where docs and code disagreed (5 Nov)

- Agent temperature: the config comment said "Groq only"; 0.3 is sent to Sarvam too. Comment fixed.
- Voice timeout: the comment said 4 s; the bot sets 2.5 s (D-46). Comment fixed.
- Judge: the config said "uncalibrated"; it was calibrated on 4 Nov. Comment fixed.
- `docs/eval-report.md` and `docs/evals.md` still describe 30 scenarios and an older hold-out list; `docs/issues-and-fixes.md` "Still open" section is stale. To update when `testing` is merged.
