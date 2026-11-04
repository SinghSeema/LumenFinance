# CLAUDE.md: Lumen Finance EMI Reminder Voice Agent

Take-home assignment: an outbound voice agent that calls a borrower about an upcoming or overdue EMI and sounds like a capable, polite human. Runs on a laptop: Pipecat for orchestration, a local LiveKit server for transport. No cloud deployment. The owner must be able to explain every line live, so keep code small and plain.

## Priorities (what is graded)

Conversation quality and naturalness 55%, edge cases and guardrails 15%, eval design and honesty 20%, clarity of reasoning 10%. A narrow agent that sounds great beats a broad one that sounds robotic. A live interview follows, with an unseen borrower scenario, so favor robustness over cleverness.

## Core principle: hybrid rules plus LLM

Code owns the rules; the LLM owns the language.
- **Code:** call stages, what the model may see, tool permissions, verification, spoken number and date formatting, date normalization, timers, outcome validation, escalation flags.
- **LLM:** natural phrasing, understanding intent, following the borrower's language, empathy, choosing which tool to call.
- The prompt is the second line of defence, never the only one. Details in `docs/HLD.md`.

## Non-negotiable rules (IDs in `docs/requirements.md`)

1. No loan detail before verification passes, including to family members (G-01, G-02).
2. No threats, shaming or pressure; no unreal consequences (G-03).
3. No promises of waivers, discounts, restructuring or fee reversals; offer a human (G-04).
4. Escalate by flag for disputes, hardship and explicit requests (G-05).
5. Stop means stop; stay on topic; never invent data; say it is an AI if asked (G-06 to G-09).

## Scope

- Build from scratch. Woka (github.com/SinghSeema/woka) is an ideas-only reference. Do not copy its structure or its Cloud Run, GCP, FastAPI/React, Supabase or backchannel filter.
- Local only: Python Pipecat agent (LiveKit transport), `livekit-server --dev`, a minimal browser client. No Docker requirement, database or deployment work.
- Languages: English and Hindi, including Hinglish. Fictional lender; mock data only.

## Docs (read the one for the area you are working on)

| File | Holds |
| --- | --- |
| `docs/requirements.md` | Functional and quality requirements, guardrail IDs, traceability |
| `docs/HLD.md` | Architecture, hybrid split, data flow, where each guardrail is enforced |
| `docs/LLD.md` | Contracts: state machine, tools, gating, formatter, dates, schema, config |
| `docs/design.md` | Decision log, trade-offs, open items |
| `docs/evals.md` | Scenarios, metrics, judge calibration, reporting decisions |
| `docs/assignment-checklist.md` | Every item in the brief mapped to a doc and status; deliverables and stretch goals |
| `docs/tuning-playbook.md` | Latency and voice tuning: strategy review, experiments T0 to T7, listening checks, results-log template |

## Run locally

Keys come from the owner separately. Never print, log or commit them; `.env` is gitignored, `.env.example` has names only.

```
livekit-server --dev                                   # terminal 1 (keys devkey / secret)
python -m emi_agent.bot --profile hindi_bulbul         # terminal 2 (Pipecat pipeline, joins the LiveKit room)
python scripts/serve_client.py                         # terminal 3, browser client
python -m evals.run --scenarios dev --runs 5           # evals
```

Env names: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `SARVAM_API_KEY`, `CARTESIA_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, optional `LANGFUSE_*`. Check each Pipecat service's documented names. Fail fast on a missing key.

## Layout

```
src/emi_agent/  config providers bot state tools speech_format dates outcome latency prompts/
bakeoff/  evals/  configs/  data/  tests/  scripts/  docs/  results/
```

Providers (STT, TTS, LLM) are chosen by config profile, not code. Defaults and decision rules are in `docs/design.md`.

## Working rules

- Ask before adding a paid service or dependency, changing a guardrail, running a large paid eval, or deviating from these docs.
- Estimate cost before any run with many paid API calls; print token usage after.
- Never state a latency, accuracy or cost figure you did not measure; label estimates.
- Write tests for gating, the formatter, the date normalizer and the outcome schema first. Run tests before saying work is done.
- Prompts live in `src/emi_agent/prompts/`, versioned; change one thing at a time and record results in `results/`.
- Keep modules small. No speculative abstractions.

## Build order (each gate must pass before the next phase)

1. Bake-off: STT, TTS, LLM on one Hinglish test set. Gate: stack locked.
2. Text brain, tools, gating, formatter, scenario runner. Gate: 10 scenarios run text-only.
3. Voice loop on Pipecat with the LiveKit transport. Gate: latency median under 1.2s, p90 under 1.8s over 30 turns.
4. Evals, fixes, re-run, hold-out, demo (3 to 5 minutes), README. Gate: hold-out passes and the report is written.
