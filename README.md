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
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
cp .env.example .env        # fill in your keys; .env is gitignored
```

Then, in three terminals:

```bash
livekit-server --dev                                        # 1: local LiveKit (keys devkey / secret)
python3 -m emi_agent.bot --profile hindi_bulbul --borrower B002   # 2: the agent joins the room
python3 scripts/serve_client.py                             # 3: open http://localhost:8080 and press call
```

`--borrower` picks a mock borrower from [`data/borrowers.json`](data/borrowers.json); `--today 2026-11-04` fixes the date; `--set section.key=value` overrides any config value. A missing key fails at start-up with the variable's name.

After each call the agent prints the outcome record, token usage and a latency table (VAD stop to transcript, transcript to first sentence, first sentence to audio).

## Tests and evals

```bash
python3 -m pytest -q                                   # 668 unit and replay tests, no network
python3 -m evals.run --scenarios dev --runs 5          # text evals: real Brain vs an LLM-played borrower
python3 -m evals.run --scenarios holdout --runs 5      # hold-out scenarios, run once fixes are frozen
python3 -m evals.run --scenarios S02,S15 --runs 3      # chosen scenarios
```

The eval runner prints a cost estimate before it starts. Each run saves every transcript, tool call, guard hit and score under `results/<timestamp>-<label>/`. Scenarios (17, dev and hold-out) are in [`evals/scenarios/`](evals/scenarios/); metrics M1 to M11 and how each is scored are in [`docs/evals.md`](docs/evals.md). Scoring is rule-based today; the LLM judge for tone is designed but not wired in yet.

Bake-off scripts for provider choices are in [`bakeoff/`](bakeoff/), for example `python3 -m bakeoff.stt_keyterms run` (Deepgram keyterm prompting on Hindi numbers and dates).

## Results so far

Measured figures only; each one links to the run that produced it.

| What | Result | Source |
|---|---|---|
| Text evals, prompt v7, dev set x5 | 52/55 pass, 0 disclosure leaks before verification; escalation precision 0.94, recall 1.0 | `results/20261104-105425-v7-dev5/` |
| Text evals, prompt v4, dev set x5 | 48/55 pass, 0 leaks | `results/20261103-235541-sarvam-v4-dev5/` |
| Live call latency (1 call, 8 turns) | Response gap median 992 ms, p90 1703 ms, plus the 0.5 s VAD stop | `results/latency/20261104-115655-B002.jsonl` |
| Deepgram keyterms (75 synthetic clips) | Date of birth read correctly 15/36 without, 26/36 with; last four digits 9/18 without, 15/18 with; no false numbers | `results/stt_keyterms/20261104-130330.txt` |

**Not yet measured:** the current default prompt (v10: last four digits first, "concerned team" wording) has not been run through the evals or a live call. The latency gate (median under 1.2 s, p90 under 1.8 s over 30 voice turns) has not been run as a 30-turn set. Hold-out scenarios have not been run.

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
