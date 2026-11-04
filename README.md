# Lumen Finance EMI Reminder Voice Agent

An outbound voice agent that calls a borrower about an upcoming or overdue EMI, in English, Hindi or Hinglish, and tries to sound like a capable, polite human caller. It verifies the borrower, states the EMI, finds out what they intend to do (pay, promise a date, dispute, hardship, callback, stop) and writes one structured outcome record per call.

Lumen Finance is fictional and all borrower data is mock data. Everything runs on a laptop: [Pipecat](https://github.com/pipecat-ai/pipecat) for the voice pipeline, a local [LiveKit](https://livekit.io) server for audio, and a small browser page as the phone.

## Core idea: code owns the rules, the LLM owns the language

The model writes the words, handles empathy and follows the borrower's language. Anything that must never go wrong is decided in code, with the prompt as a second line of defence:

| Rule | Where it is enforced |
|---|---|
| No loan detail before identity is verified (G-01, G-02) | The model sees no loan data before verification; loan tools refuse; a per-sentence output guard; a disclosure log the evals check |
| Identity check and attempt counting | Code reads the last four digits or the date of birth from the transcript and checks it; at most 3 attempts; no hints |
| No made-up verification result | Code replaces "that doesn't match" or "last chance" when no check has failed in that turn |
| No threats, shaming, waivers or discounts (G-03, G-04) | Output guard in English and Hindi (Devanagari and romanised) replaces the sentence with a safe line |
| Escalation for dispute, hardship, explicit request (G-05) | Tool flag set by code; a dispute or unmatched payment goes to "the concerned team, who will arrange a callback" |
| Stop means stop (G-06) | Code detects stop requests and closes the call |
| Amounts and dates said aloud correctly | Code formats them ("twelve thousand five hundred rupees", "the twenty-eighth of October") and resolves "next Friday" or "parso" against a fixed today |
| A valid outcome record | Pydantic schema; code fills in the outcome if the model forgets |

## How a call goes

1. Greeting: confirms it is speaking to the borrower. Wrong number or a family member: polite close, nothing disclosed.
2. Verification: asks for the **last four digits of the registered mobile number**; if they do not match, asks for the **date of birth**. A date of birth offered first is accepted too. Three attempts at most.
3. Reminder: the EMI amount and due date, or how many days it is overdue.
4. Intent: payment link (mock SMS or WhatsApp), promise-to-pay date, callback time, "already paid" (the concerned team checks it), dispute or hardship (escalated), stop.
5. Close: thanks the borrower and writes the outcome to `results/calls.jsonl`.

Example outcome record from a live call:

```json
{"borrower_id":"B002","verified":true,"outcome":"already_paid","promise_to_pay_date":null,
 "payment_link_sent":false,"escalate_to_human":true,
 "notes":"Borrower says she already paid via UPI but did not specify the date.",
 "language_used":"hi+en","callback_time":null}
```

## Architecture

```mermaid
flowchart LR
  B[Browser client] <-->|WebRTC audio| LK[Local LiveKit server]
  LK <--> AG
  subgraph AG[Pipecat agent process]
    VAD[Silero VAD] --> STT[Deepgram Nova-3 multi]
    STT --> BR[Brain: LLM plus rules]
    BR --> G[Output guard and pre-TTS fixes]
    G --> TTS[Sarvam Bulbul TTS]
    BR <--> TL[Tools: verify, loan facts, payment link, callback, escalate, outcome]
  end
  TL --> OUT[results/calls.jsonl]
  EV[Text eval harness] -->|same Brain| BR
  SIM[LLM borrower simulator] --> EV
```

The Brain (`src/emi_agent/brain.py`) is plain Python with no audio code. The voice pipeline and the text eval harness call the same Brain, so what is evaluated is what is shipped.

### Current stack

| Part | Choice | Why |
|---|---|---|
| STT | Deepgram Nova-3, `language=multi`, EU host, endpointing 100 ms | Handles Hindi-English code-switching in one stream; EU host measured ~150 ms closer than the US default from here |
| LLM | Sarvam `sarvam-105b-conversations`; Groq `gpt-oss` as fallback | Fastest first text measured for Hinglish replies; billed in rupees |
| TTS | Sarvam Bulbul v4-flash (Hindi in Devanagari, English terms in Latin) | Reads Hinglish and spoken numbers correctly in clip checks; the voice is a placeholder until blind listening scores are in; Cartesia Sonic is a config option |
| Turn detection | Silero VAD (0.5 s stop) plus an "unfinished sentence" wait | Smart Turn v3 was tried and is kept as an option, not the default |
| Transport | Pipecat LiveKit transport, local `livekit-server --dev` | Local only, no cloud deployment |

Providers are chosen by config profile (`configs/`), not code. Every decision and its alternatives are in [`docs/design.md`](docs/design.md).

## Run it locally

Requirements: Linux or macOS, Python 3.10+, the [`livekit-server`](https://docs.livekit.io/home/self-hosting/local/) binary, a microphone, and API keys for Deepgram and Sarvam (Groq optional for the fallback, Cartesia optional).

```bash
git clone https://github.com/SinghSeema/LumenFinance.git && cd LumenFinance
./run.sh setup          # installs, creates .env (add DEEPGRAM_API_KEY and SARVAM_API_KEY), checks keys and livekit-server, runs the tests
./run.sh call B002      # starts LiveKit, the browser client and the agent; open http://localhost:8080 and press call
```

Ctrl-C ends the call and stops all three processes. Logs go to `logs/` (LiveKit, client, and `bot-<borrower>.log`). `./run.sh` with no argument lists the other commands (`test`, `evals`, `judge`, `report`).

The same steps by hand, in three terminals:

```bash
livekit-server --dev                                              # 1: local LiveKit (keys devkey / secret)
PYTHONPATH=src python3 -m emi_agent.bot --profile hindi_bulbul --borrower B002   # 2: the agent joins the room
python3 scripts/serve_client.py                                   # 3: open http://localhost:8080
```

`--borrower` picks a mock borrower from [`data/borrowers.json`](data/borrowers.json); `--today 2026-11-04` fixes the date; `--set section.key=value` overrides any config value. A missing key fails at start-up with the variable's name.

After each call the agent prints the outcome record, token usage and a latency table (VAD stop to transcript, transcript to first sentence, first sentence to audio).

## Evals: how they work

The brief asks how we know the agent is good, and where it is not. The answer has four parts.

**1. Scenarios.** 17 YAML files in [`evals/scenarios/`](evals/scenarios/), one per borrower type the brief lists plus tricky cases (wrong verification three times, waiver demand, "stop calling", vague dates, mumbled digits, a payment-link tool that fails, "already paid" while overdue). Each states the borrower **persona**, a **hidden goal** the simulated borrower follows, and the **expected outcome record**. 12 are dev scenarios used while building; 5 are hold-out scenarios kept unseen until the baseline run.

**2. Automated runs with a simulated borrower.** `evals/run.py` runs the real agent (the same Brain, tools, guards and prompt as the voice bot; only audio is skipped) against an LLM playing the borrower (`evals/simulator.py`, `glm5.3-flash`, a different model family from the agent's `sarvam-105b`). The simulator sees the persona, the hidden goal and today's date, never the expected outcome. Every scenario runs 5 times because conversations vary. Each conversation is saved in full: turns, tool calls and results, the outcome record, guard hits and a log of every loan fact the model was shown.

**3. Scoring: rules first, a judge only where no rule works.**

| How | What it scores | Trust |
|---|---|---|
| Rules (`evals/score.py`) | Outcome record vs expected (M1); loan detail before verification, from the disclosure log plus a scan of every unverified turn (M2, zero tolerance); promise date (M3); escalation precision and recall (M4); reply language (M6); process checks: says it is an AI when asked, no invented payment link after a tool failure, asks one clear question on a vague date (M11); turns per call (M8) | High |
| Code guard, counted per run | Sentences replaced before speech for threats, waiver promises or early disclosure (M5) | High |
| LLM judge (`evals/judge.py`) | Tone (M7): politeness, empathy, firmness without pressure, clarity of the reminder, natural phrasing, each 1 to 5, plus guardrail flags. `glm5.3-flash`, temperature 0, one call per conversation. A flag must quote the agent's exact words; a quote not found in the transcript is dropped | **Low**: a spot check found it marking correct refusals down (not telling a family member the amount; ending the call after "stop calling me"). Not calibrated against hand labels yet, so its scores are shown but never decide a pass |
| Hand review (`evals/failure_review.yaml`) | Every failed conversation is read in full and marked as an agent defect, a code bug, or a test-side error (simulator off script, scorer wrong) | The final word on what a failure means |
| Live-call timestamps (`results/latency/`) | Latency per turn (M9): VAD stop, final transcript, first sentence, first audio | High for what was measured |

**4. Report and dashboard.** [`docs/eval-report.md`](docs/eval-report.md) is the write-up the brief asks for: results, real failures, what changed, what to fix next. `results/dashboard.html` shows the same evidence visually: pass rate per borrower type before and after fixes, metrics with trust, a latency histogram against the targets, the reviewed failures, and every conversation readable with its tool calls and outcome record. Open it in a browser; it needs no network.

```bash
./run.sh test                                    # 706 unit and replay tests, no network
./run.sh evals --scenarios all --runs 5          # all 17 scenarios x5 (paid; prints a cost estimate and asks first)
./run.sh evals --scenarios S13,S14a --runs 5     # chosen scenarios
./run.sh evals --scenarios all --runs 5 --resume results/<run>   # finish a run that was cut short
./run.sh judge results/<run>                     # tone judge on a finished run (paid; asks first)
./run.sh report results/<run>                    # markdown tables for the report
./run.sh dashboard results/<baseline> results/<after-fix>   # writes results/dashboard.html
```

Bake-off scripts for provider choices are in [`bakeoff/`](bakeoff/), for example `python3 -m bakeoff.stt_keyterms run` (Deepgram keyterm prompting on Hindi numbers and dates).

## Results so far

Measured figures only. Full write-up: [`docs/eval-report.md`](docs/eval-report.md); visual version with every conversation: `results/dashboard.html`.

| What | Result | Source |
|---|---|---|
| Text evals, prompt v10, all 17 scenarios x5 (baseline) | 76/85 pass (dev 59/60, hold-out 17/25); 0 leaks before verification; escalation precision 0.93, recall 1.0 | `results/20261104-133125-v10-all5/` |
| After two rounds of fixes (re-run scenarios x5) | Round 1: 36/40. Round 2: 28/30; S13 vague dates 0/5 to 5/5; the 2 remaining failures are the simulator going off script | `results/20261104-135904-v10-fixes/`, `...-140824-v10-fixes2/`, `...-141354-v10-fixes3/` |
| Leaks before verification | 0 in all 155 conversations | same runs |
| Tone (LLM judge, uncalibrated, low trust) | politeness 4.93, empathy 3.06, firmness 4.55, clarity 4.27, naturalness 4.19 (baseline) | `judge.json` in the baseline run |
| Live call latency, current stack (9 calls, 46 turns) | Median 1020 ms (target 1.2 s met), p90 2393 ms (target 1.8 s missed), plus the 0.5 s VAD stop | `results/latency/` |
| Deepgram keyterms (75 synthetic clips) | Date of birth read correctly 15/36 without, 26/36 with; last four digits 9/18 without, 15/18 with; no false numbers | `results/stt_keyterms/20261104-130330.txt` |

**Not yet done:** the tone judge has not been calibrated against hand labels; the hold-out scenarios have now been fixed against, so a fresh unseen set is needed; the keyterm gain is from synthetic voices.

Every defect found so far, with evidence, root cause, fix and how it was checked, is in [`docs/issues-and-fixes.md`](docs/issues-and-fixes.md); the day-by-day measurement log is [`results/LOG.md`](results/LOG.md).

## Known limitations

- Deepgram's multilingual model sometimes mishears Hindi number words ("बाईस" as "buys") and on short answers can write Hindi or English in Telugu, Kannada or Bengali script. Keyterm prompting reduces this on synthetic clips; it stays off by default until it is checked on real recordings.
- The LLM sometimes ignores instructions (for example it once said "doesn't match" with no check run). Each case found is now caught in code, but new ones can appear; this is why rules live in code.
- Payment links, callbacks and escalations are mock tools; nothing is sent.
- One browser "phone" over local LiveKit; no telephony (SIP) in this build.

## Repository layout

```
src/emi_agent/   agent code: bot (Pipecat pipeline), brain, tools, guard, heard (number and date reading),
                 speech_format, dates, language, outcome, providers, keyterms, prompts/ (versioned v1 to v10)
configs/         defaults.yaml and provider profiles
data/            mock borrowers
evals/           runner, LLM borrower simulator, rule-based scorer, scenarios
bakeoff/         provider and tuning experiments (STT latency and keyterms, TTS checks, LLM speed)
scripts/         browser client and its server, issues page renderer
tests/           unit and replay tests
docs/            requirements, HLD, LLD, design decisions, evals, issues and fixes, tuning playbook
results/         eval runs, latency logs, bake-off reports, LOG.md
```

## Documentation

| File | Holds |
|---|---|
| [`docs/requirements.md`](docs/requirements.md) | Functional and quality requirements, guardrail IDs (G-01 to G-11) |
| [`docs/HLD.md`](docs/HLD.md) | Architecture, the hybrid split, where each guardrail is enforced |
| [`docs/LLD.md`](docs/LLD.md) | State machine, tools, gating, formatter, dates, schema, config |
| [`docs/design.md`](docs/design.md) | Decision log (D-01 to D-35) with alternatives and reasons |
| [`docs/evals.md`](docs/evals.md) | Scenarios, metrics, judge calibration plan |
| [`docs/issues-and-fixes.md`](docs/issues-and-fixes.md) | Every defect found and how it was fixed and checked |
| [`docs/tuning-playbook.md`](docs/tuning-playbook.md) | Latency and voice tuning experiments |

Built with AI assistance (Claude Code); [`CLAUDE.md`](CLAUDE.md) holds the working rules it followed.
