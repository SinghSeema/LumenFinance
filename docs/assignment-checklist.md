# Assignment Checklist

Every item in the brief mapped to where it is handled. Status: **Specified** (in the docs), **Built** and **Verified** are filled in during the build.

## Scenario and call flow

| Brief item | Where | Status |
| --- | --- | --- |
| Mock dataset 5 to 10 borrowers with name, DOB or last4, loan type, EMI, due date, days overdue, preferred language | requirements section 4; LLD section 15 | Specified |
| Confirm identity before sharing loan details | F-01, F-02, G-01; HLD section 3 | Specified |
| Remind of EMI amount and due date or overdue amount, naturally | F-03; LLD sections 3, 6 | Specified |
| Find out intent and respond | F-04; LLD section 16 | Specified |
| Offer payment link via mock tool | F-05; LLD section 4 | Specified |
| Close politely, record structured outcome | F-06, G-12; LLD section 5 | Specified |
| Outcome record fields | requirements section 1; LLD section 5 | Specified |
| 8 borrower responses (R1 to R8) | F-07; scenarios S01 to S09 | Specified |

## Conversation quality

| Brief item | Where | Status |
| --- | --- | --- |
| Latency: what was measured and how it was reduced | Q-01; LLD section 13; design section 4; M9 | Specified |
| Barge-in | Q-02; LLD section 9; M10 | Specified |
| Turn-taking and quiet callers | Q-03; LLD sections 9, 11 | Specified |
| Speaking style, numbers and dates spoken naturally | Q-04; LLD sections 6, 7, 16 | Specified |
| Tone | Q-06; M7; tone rubric | Specified |
| Mid-call language switching | Q-08; LLD section 8; S09 | Specified |

## Guardrails

| Brief item | Where | Status |
| --- | --- | --- |
| No loan details to wrong person or family | G-01, G-02; S05, S06 | Specified |
| No threats or shaming | G-03; S08 | Specified |
| No promises of waivers or discounts | G-04; S11 | Specified |
| Escalate for disputes, hardship, requests | G-05; S03, S04, S04b, S11 | Specified |
| Stop when asked, stay on topic, no invention, AI disclosure | G-06 to G-09 | Specified |

## Evals

| Brief item | Where | Status |
| --- | --- | --- |
| At least 10 scenarios with persona, behavior, expected outcome | evals section 2; `evals/scenarios/` (15 files: S01-S13, S14a, S14b) | Built |
| Runs repeated | E-03 | Specified |
| Runner | evals section 5 | Specified |
| Metrics: outcome accuracy, verification before disclosure, promise date, escalation, tone, latency | M1 to M5, M7, M9 | Specified |
| Why chosen and how much trusted | evals section 3 rationale and Trust column; section 4 | Specified |
| Report: results, 2 to 3 real failures, changes, next fixes | evals section 7; E-12 | Specified |

## Tech choices and stretch

| Brief item | Where | Status |
| --- | --- | --- |
| Stack and trade-offs, latency and voice quality | design sections 1, 3, 6 | Specified |
| Stretch: language switching, voice evals | In scope | Specified |
| Stretch: dashboard | Optional, static HTML | Open |
| Stretch: voicemail detection, live hand-off | Not planned; state in README | Decided |

## Deliverables

| Deliverable | Where | Status |
| --- | --- | --- |
| Repo with README (run agent and evals, conversation structure, design decisions) | DL-1 | Done 2026-11-04: key decisions and mock data sections added, v17 results |
| Demo recording, 3 to 5 min, 2 or 3 calls, one difficult | DL-2 | Done 2026-11-04: `DemoRecordings/` (3 calls, audio, 5 min); call pages in `results/calls/` |
| Eval suite and results | DL-3 | Done 2026-11-04: v17 unseen stress run + fixes, `docs/eval-report.md`, `results/dashboard.html` |
| Mock data and tools | DL-4 | Done: `data/`, `src/emi_agent/tools.py`; README section "Mock data and tools" |

## Demo plan

1. Easy call: verify, reminder, payment link sent.
2. Hardship or dispute: empathy, no promises, escalation flag, outcome record shown.
3. Difficult: wrong-number or family member demanding the amount, or a mid-call switch to Hindi.
Show the outcome record and the latency log at the end of each call: open the call page `results/calls/<time>-<borrower>.html` (written by the bot, link in the log).

Live interview (interviewer plays the borrower over a video call): `./scripts/meeting_audio.sh up` routes the meeting audio into the call page and the agent back into the meeting; headphones on; rehearse once before the interview. A browser link for the interviewer is not offered: the stack is local only, and WebRTC media does not pass through an HTTP tunnel.

## Known open items before building

Pipecat service availability, Haiku 5.5 behavior on collection prompts, TTS script choice, free-tier terms (design section 5). The shared Google decision doc already says Pipecat, which now matches the repo docs (Pipecat orchestration, LiveKit transport).
